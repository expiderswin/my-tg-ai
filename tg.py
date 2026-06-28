import os
import logging
import asyncio
import sqlite3
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import google.generativeai as genai
import anthropic
import openai
import httpx

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")

# Настройка моделей
MODELS = {
    "gemini-2.0-flash": {"provider": "gemini", "context": 1_000_000},
    "gemini-1.5-pro": {"provider": "gemini", "context": 2_000_000},
    "claude-3.5-sonnet": {"provider": "claude", "context": 200_000},
    "claude-3-haiku": {"provider": "claude", "context": 200_000},
    "gpt-4o": {"provider": "openai", "context": 128_000},
    "gpt-4-turbo": {"provider": "openai", "context": 128_000},
    "deepseek-chat": {"provider": "deepseek", "context": 64_000},
}

DEFAULT_MODEL = "gemini-2.0-flash"
LANGUAGE_KEYWORDS = ["python", "py", "javascript", "js", "java", "cpp", "c++", "c#", "cs", "ruby", "go", "rust", "php", "swift", "kotlin", "html", "css", "dll", "exe", "sh", "bat", "ps1"]

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self, db_path="users.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 100,
                model TEXT DEFAULT 'gemini-2.0-flash',
                history TEXT DEFAULT '[]',
                created_at TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                bonus INTEGER DEFAULT 10,
                created_at TEXT
            )""")
            conn.commit()

    def get_user(self, user_id: int) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT balance, model, history FROM users WHERE user_id=?", (user_id,))
            row = c.fetchone()
            if not row:
                c.execute("INSERT INTO users (user_id, balance, created_at) VALUES (?, ?, ?)",
                          (user_id, 100, datetime.now().isoformat()))
                conn.commit()
                return {"balance": 100, "model": DEFAULT_MODEL, "history": []}
            return {"balance": row[0], "model": row[1], "history": json.loads(row[2])}

    def update_user(self, user_id: int, **kwargs):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            for key, value in kwargs.items():
                if key == "history":
                    value = json.dumps(value)
                c.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, user_id))
            conn.commit()

    def add_balance(self, user_id: int, amount: int):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
            conn.commit()

    def add_referral(self, referrer_id: int, referred_id: int):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                      (referrer_id, referred_id, datetime.now().isoformat()))
            conn.commit()
            self.add_balance(referrer_id, 10)

db = Database()

# ========== AI ПРОВАЙДЕРЫ ==========
class AIProvider:
    @staticmethod
    def get_service(model_name: str):
        provider = MODELS[model_name]["provider"]
        if provider == "gemini":
            return GeminiService(model_name)
        elif provider == "claude":
            return ClaudeService(model_name)
        elif provider == "openai":
            return OpenAIService(model_name)
        elif provider == "deepseek":
            return DeepSeekService(model_name)
        raise ValueError(f"Unknown provider: {provider}")

class GeminiService:
    def __init__(self, model_name: str):
        genai.configure(api_key=GEMINI_KEY)
        self.model = genai.GenerativeModel(model_name)

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            if history:
                chat = self.model.start_chat(history=history)
                response = await chat.send_message_async(prompt)
            else:
                response = await self.model.generate_content_async(prompt)
            return response.text
        except Exception as e:
            return f"❌ Gemini Error: {str(e)}"

class ClaudeService:
    def __init__(self, model_name: str):
        self.client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        self.model_name = model_name

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            messages = history or []
            messages.append({"role": "user", "content": prompt})
            response = self.client.messages.create(
                model=self.model_name,
                max_tokens=4096,
                messages=messages
            )
            return response.content[0].text
        except Exception as e:
            return f"❌ Claude Error: {str(e)}"

class OpenAIService:
    def __init__(self, model_name: str):
        self.client = openai.AsyncOpenAI(api_key=OPENAI_KEY)
        self.model_name = model_name

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            messages = history or []
            messages.append({"role": "user", "content": prompt})
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=4096
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"❌ OpenAI Error: {str(e)}"

class DeepSeekService:
    def __init__(self, model_name: str):
        self.model_name = model_name

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            messages = history or []
            messages.append({"role": "user", "content": prompt})
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                    json={"model": self.model_name, "messages": messages, "max_tokens": 4096}
                )
                return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"❌ DeepSeek Error: {str(e)}"

# ========== ОБРАБОТЧИК КОДА ==========
class CodeHandler:
    @staticmethod
    def detect_language(text: str) -> Optional[str]:
        text_lower = text.lower()
        for lang in LANGUAGE_KEYWORDS:
            if lang in text_lower:
                return lang
        return None

    @staticmethod
    def generate_code(language: str, prompt: str) -> str:
        # Шаблоны для разных языков
        templates = {
            "python": f"# Python code for: {prompt}\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()",
            "cpp": f"// C++ code for: {prompt}\n#include <iostream>\n\nint main() {{\n    // Your code here\n    return 0;\n}}",
            "html": f"<!DOCTYPE html>\n<html>\n<head>\n    <title>{prompt}</title>\n    <style>\n        body {{ font-family: Arial; }}\n    </style>\n</head>\n<body>\n    <h1>{prompt}</h1>\n</body>\n</html>",
            "css": f"/* CSS for: {prompt} */\nbody {{\n    background: #f0f0f0;\n}}",
            "javascript": f"// JavaScript for: {prompt}\nfunction main() {{\n    console.log('Hello');\n}}\nmain();",
            "java": f"// Java for: {prompt}\npublic class Main {{\n    public static void main(String[] args) {{\n        // Your code here\n    }}\n}}",
            "dll": "Cannot generate DLL directly, but here's a C++ header:\n#ifdef _WIN32\n#define EXPORT __declspec(dllexport)\n#else\n#define EXPORT\n#endif\n\nextern \"C\" {\n    EXPORT void function();\n}",
        }
        return templates.get(language, f"// {language.upper()} code for: {prompt}\n// Write your code here")

    @staticmethod
    def extract_file_extension(language: str) -> str:
        ext_map = {
            "python": "py", "py": "py",
            "javascript": "js", "js": "js",
            "cpp": "cpp", "c++": "cpp",
            "java": "java",
            "html": "html",
            "css": "css",
            "dll": "dll",
            "sh": "sh",
            "bat": "bat",
            "go": "go",
            "rust": "rs"
        }
        return ext_map.get(language.lower(), "txt")

# ========== FSM (Состояния) ==========
class Form(StatesGroup):
    waiting_for_code_prompt = State()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== ХЕНДЛЕРЫ ==========
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user = db.get_user(message.from_user.id)
    # Реферальная система
    if len(message.text.split()) > 1:
        referrer_id = int(message.text.split()[1])
        if referrer_id != message.from_user.id:
            db.add_referral(referrer_id, message.from_user.id)

    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
        InlineKeyboardButton(text="🧠 Модели", callback_data="models")
    )
    keyboard.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )

    await message.answer(
        f"🤖 *AI ALL Bot*\n\n"
        f"👤 Привет, {message.from_user.first_name}!\n"
        f"💰 Баланс: `{user['balance']}` токенов\n"
        f"🧠 Модель: `{user['model']}`\n\n"
        f"📝 *Что я умею:*\n"
        f"• Отвечаю на любые вопросы\n"
        f"• Генерирую код на всех языках\n"
        f"• Создаю HTML/CSS дизайн\n"
        f"• Работаю с файлами\n\n"
        f"Просто напиши свой запрос!",
        reply_markup=keyboard.as_markup(),
        parse_mode="Markdown"
    )

@dp.message(Command("models"))
async def models_cmd(message: types.Message):
    keyboard = InlineKeyboardBuilder()
    for model in MODELS.keys():
        keyboard.row(InlineKeyboardButton(text=model, callback_data=f"setmodel_{model}"))
    await message.answer("Выберите модель:", reply_markup=keyboard.as_markup())

@dp.callback_query(lambda c: c.data.startswith("setmodel_"))
async def set_model(callback: types.CallbackQuery):
    model = callback.data.split("_")[1]
    db.update_user(callback.from_user.id, model=model)
    await callback.answer(f"✅ Модель изменена на {model}")
    await callback.message.edit_text(f"Активная модель: {model}")

@dp.message(Command("balance"))
async def balance_cmd(message: types.Message):
    user = db.get_user(message.from_user.id)
    await message.answer(f"💰 Ваш баланс: `{user['balance']}` токенов", parse_mode="Markdown")

@dp.message(Command("code"))
async def code_cmd(message: types.Message, state: FSMContext):
    await state.set_state(Form.waiting_for_code_prompt)
    await message.answer("💻 Напишите, какой код нужно сгенерировать и на каком языке.\nПример: `python парсер сайта`")

@dp.message(StateFilter(Form.waiting_for_code_prompt))
async def generate_code_response(message: types.Message, state: FSMContext):
    await state.clear()
    text = message.text
    language = CodeHandler.detect_language(text)

    if language:
        code = CodeHandler.generate_code(language, text)
        ext = CodeHandler.extract_file_extension(language)
        filename = f"code.{ext}"

        # Сохраняем файл
        with open(filename, "w", encoding="utf-8") as f:
            f.write(code)

        # Отправляем как файл
        await message.answer_document(
            FSInputFile(filename),
            caption=f"✅ Код на *{language.upper()}* готов!\n"
                    f"Расширение: `.{ext}`"
        )
        os.remove(filename)
    else:
        # Если язык не определён — просто отвечаем как ИИ
        await handle_ai_response(message)

@dp.callback_query(lambda c: c.data == "models")
async def show_models(callback: types.CallbackQuery):
    await models_cmd(callback.message)

@dp.callback_query(lambda c: c.data == "balance")
async def show_balance(callback: types.CallbackQuery):
    await balance_cmd(callback.message)

@dp.callback_query(lambda c: c.data == "help")
async def help_cmd(callback: types.CallbackQuery):
    await callback.message.answer(
        "📖 *Помощь*\n\n"
        "/start - Главное меню\n"
        "/models - Выбор модели\n"
        "/balance - Баланс\n"
        "/code - Генерация кода\n"
        "/referral - Реферальная ссылка\n\n"
        "Просто отправьте сообщение, и я отвечу!",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(Command("referral"))
async def referral_cmd(message: types.Message):
    link = f"https://t.me/{bot.me.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 Ваша реферальная ссылка:\n`{link}`\n\n"
        f"За каждого приглашённого вы получите 10 токенов!",
        parse_mode="Markdown"
    )

# ========== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ==========
@dp.message()
async def handle_ai_response(message: types.Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    text = message.text

    # Проверка баланса
    if user["balance"] <= 0:
        await message.answer("❌ Недостаточно токенов! Используйте /referral для получения бонусов.")
        return

    # Проверка на запрос кода
    if CodeHandler.detect_language(text):
        # Генерируем код
        language = CodeHandler.detect_language(text)
        code = CodeHandler.generate_code(language, text)
        ext = CodeHandler.extract_file_extension(language)
        filename = f"code.{ext}"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(code)

        await message.answer_document(
            FSInputFile(filename),
            caption=f"✅ Код на *{language.upper()}* сгенерирован!\nФайл: `{filename}`",
            parse_mode="Markdown"
        )
        os.remove(filename)

        # Списываем токен
        db.add_balance(user_id, -1)
        return

    # Обычный запрос к ИИ
    try:
        service = AIProvider.get_service(user["model"])
        response = await service.generate(text, user["history"])

        # Сохраняем историю
        history = user["history"]
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})
        if len(history) > 20:  # Ограничиваем историю
            history = history[-20:]
        db.update_user(user_id, history=history)

        # Списываем токен
        db.add_balance(user_id, -1)

        # Отправляем ответ
        await message.answer(response[:4096])  # Telegram лимит

    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== ЗАПУСК ==========
async def main():
    logger.info("🚀 Бот запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
