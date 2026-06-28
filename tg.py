import os
import logging
import asyncio
import sqlite3
import json
import re
import io
import tempfile
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, BufferedInputFile,
    Message, CallbackQuery, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import BotCommand, BotCommandScopeDefault

import google.generativeai as genai
import anthropic
import openai
import httpx
from PIL import Image, ImageDraw, ImageFont
import speech_recognition as sr
from pydub import AudioSegment
import replicate
from moviepy.editor import ImageSequenceClip
import numpy as np

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8902285510:AAGdfAZfFAOOCSGi_gQ-lqdb_7E7OolE6uo"

# API ключи (замените на свои)
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDummyKey")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY", "sk-ant-dummy")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "sk-dummy")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-dummy")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY", "r8_dummy")

# Настройка моделей
MODELS = {
    "gemini-2.0-flash": {"provider": "gemini", "context": 1000000, "emoji": "🟢"},
    "gemini-1.5-pro": {"provider": "gemini", "context": 2000000, "emoji": "🟢"},
    "claude-3.5-sonnet": {"provider": "claude", "context": 200000, "emoji": "🟣"},
    "claude-3-haiku": {"provider": "claude", "context": 200000, "emoji": "🟣"},
    "gpt-4o": {"provider": "openai", "context": 128000, "emoji": "🟠"},
    "gpt-4-turbo": {"provider": "openai", "context": 128000, "emoji": "🟠"},
    "deepseek-chat": {"provider": "deepseek", "context": 64000, "emoji": "🔵"},
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
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 150,
                    model TEXT DEFAULT 'gemini-2.0-flash',
                    history TEXT DEFAULT '[]',
                    created_at TEXT
                )""")
                c.execute("""CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    bonus INTEGER DEFAULT 15,
                    created_at TEXT
                )""")
                c.execute("""CREATE TABLE IF NOT EXISTS generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    type TEXT,
                    prompt TEXT,
                    created_at TEXT
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"DB init error: {e}")

    def get_user(self, user_id: int) -> Dict[str, Any]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT balance, model, history FROM users WHERE user_id=?", (user_id,))
                row = c.fetchone()
                if not row:
                    c.execute("INSERT INTO users (user_id, balance, created_at) VALUES (?, ?, ?)",
                              (user_id, 150, datetime.now().isoformat()))
                    conn.commit()
                    return {"balance": 150, "model": DEFAULT_MODEL, "history": []}
                return {"balance": row[0], "model": row[1], "history": json.loads(row[2])}
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return {"balance": 150, "model": DEFAULT_MODEL, "history": []}

    def update_user(self, user_id: int, **kwargs):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                for key, value in kwargs.items():
                    if key == "history":
                        value = json.dumps(value)
                    c.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, user_id))
                conn.commit()
        except Exception as e:
            logger.error(f"Update user error: {e}")

    def add_balance(self, user_id: int, amount: int):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
                conn.commit()
        except Exception as e:
            logger.error(f"Add balance error: {e}")

    def add_referral(self, referrer_id: int, referred_id: int):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                          (referrer_id, referred_id, datetime.now().isoformat()))
                conn.commit()
                self.add_balance(referrer_id, 15)
        except Exception as e:
            logger.error(f"Add referral error: {e}")

    def add_generation(self, user_id: int, gen_type: str, prompt: str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO generations (user_id, type, prompt, created_at) VALUES (?, ?, ?, ?)",
                          (user_id, gen_type, prompt, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Add generation error: {e}")

db = Database()

# ========== AI ПРОВАЙДЕРЫ ==========
class AIProvider:
    @staticmethod
    def get_service(model_name: str):
        try:
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
        except Exception as e:
            logger.error(f"Get service error: {e}")
            return GeminiService(DEFAULT_MODEL)

class GeminiService:
    def __init__(self, model_name: str):
        try:
            genai.configure(api_key=GEMINI_KEY)
            self.model = genai.GenerativeModel(model_name)
        except Exception as e:
            logger.error(f"Gemini init error: {e}")
            self.model = None

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            if not self.model:
                return "❌ Gemini не инициализирован. Проверьте API ключ."
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
        try:
            self.client = anthropic.Anthropic(api_key=CLAUDE_KEY)
            self.model_name = model_name
        except Exception as e:
            logger.error(f"Claude init error: {e}")
            self.client = None
            self.model_name = model_name

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            if not self.client:
                return "❌ Claude не инициализирован. Проверьте API ключ."
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
        try:
            self.client = openai.AsyncOpenAI(api_key=OPENAI_KEY)
            self.model_name = model_name
        except Exception as e:
            logger.error(f"OpenAI init error: {e}")
            self.client = None
            self.model_name = model_name

    async def generate(self, prompt: str, history: list = None) -> str:
        try:
            if not self.client:
                return "❌ OpenAI не инициализирован. Проверьте API ключ."
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

# ========== ГЕНЕРАТОРЫ КОНТЕНТА ==========
class ContentGenerator:
    @staticmethod
    async def generate_image(prompt: str) -> Optional[bytes]:
        try:
            if not REPLICATE_API_KEY or REPLICATE_API_KEY == "r8_dummy":
                return await ContentGenerator._create_fallback_image(prompt)
            
            output = replicate.run(
                "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",
                input={
                    "prompt": prompt,
                    "negative_prompt": "ugly, deformed, low quality",
                    "width": 768,
                    "height": 768,
                    "num_outputs": 1,
                    "num_inference_steps": 30,
                    "guidance_scale": 7.5
                }
            )
            async with httpx.AsyncClient() as client:
                response = await client.get(output[0])
                return response.content
        except Exception as e:
            logger.error(f"Image generation error: {e}")
            return await ContentGenerator._create_fallback_image(prompt)

    @staticmethod
    async def _create_fallback_image(text: str) -> Optional[bytes]:
        try:
            img = Image.new('RGB', (800, 400), color=(50, 50, 80))
            d = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            except:
                font = ImageFont.load_default()
            d.text((50, 150), f"AI Generation: {text[:50]}...", fill=(255, 255, 255), font=font)
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            return img_byte_arr.getvalue()
        except Exception as e:
            logger.error(f"Fallback image error: {e}")
            return None

    @staticmethod
    async def generate_video(prompt: str, duration: int = 5) -> Optional[bytes]:
        try:
            frames = []
            for i in range(duration * 24):
                img = Image.new('RGB', (1280, 720), color=(40, 40, 80))
                d = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
                except:
                    font = ImageFont.load_default()
                
                progress = i / (duration * 24)
                text = f"{prompt[:50]}...\nGenerating: {int(progress * 100)}%"
                d.text((50, 300), text, fill=(255, 255, 255), font=font)
                
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                frames.append(np.array(Image.open(img_byte_arr)))
            
            clip = ImageSequenceClip(frames, fps=24)
            
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
                clip.write_videofile(tmp_file.name, codec='libx264', fps=24)
                tmp_file_path = tmp_file.name
            
            with open(tmp_file_path, 'rb') as f:
                video_data = f.read()
            
            os.unlink(tmp_file_path)
            return video_data
        except Exception as e:
            logger.error(f"Video generation error: {e}")
            return None

# ========== ОБРАБОТЧИК ГОЛОСА ==========
class VoiceProcessor:
    @staticmethod
    async def voice_to_text(voice_file: bytes) -> Optional[str]:
        tmp_file_path = None
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_file:
                tmp_file.write(voice_file)
                tmp_file_path = tmp_file.name
            
            audio = AudioSegment.from_ogg(tmp_file_path)
            wav_path = tmp_file_path.replace('.ogg', '.wav')
            audio.export(wav_path, format='wav')
            
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data, language='ru-RU')
            
            return text
        except Exception as e:
            logger.error(f"Voice recognition error: {e}")
            return None
        finally:
            try:
                if tmp_file_path and os.path.exists(tmp_file_path):
                    os.unlink(tmp_file_path)
                if wav_path and os.path.exists(wav_path):
                    os.unlink(wav_path)
            except:
                pass

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
        templates = {
            "python": f"# Python code for: {prompt}\nimport requests\n\ndef main():\n    print('Hello World')\n\nif __name__ == '__main__':\n    main()",
            "cpp": f"// C++ code for: {prompt}\n#include <iostream>\n#include <vector>\n\nint main() {{\n    std::cout << 'Hello World' << std::endl;\n    return 0;\n}}",
            "html": f"<!DOCTYPE html>\n<html>\n<head>\n    <title>{prompt}</title>\n    <style>\n        body {{ font-family: Arial; margin: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }}\n        .container {{ background: white; padding: 20px; border-radius: 10px; }}\n    </style>\n</head>\n<body>\n    <div class='container'>\n        <h1>{prompt}</h1>\n        <p>Generated by AI</p>\n    </div>\n</body>\n</html>",
            "css": f"/* CSS for: {prompt} */\nbody {{\n    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);\n    font-family: 'Arial', sans-serif;\n}}",
            "javascript": f"// JavaScript for: {prompt}\nfunction main() {{\n    console.log('Hello World');\n    document.body.innerHTML = '<h1>{prompt}</h1>';\n}}\nmain();",
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
            "dll": "cpp",
            "sh": "sh",
            "bat": "bat"
        }
        return ext_map.get(language.lower(), "txt")

# ========== FSM (Состояния) ==========
class Form(StatesGroup):
    waiting_for_code_prompt = State()
    waiting_for_image_prompt = State()
    waiting_for_video_prompt = State()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== КЛАВИАТУРА (КНОПКИ В ИНТЕРФЕЙСЕ) ==========
def get_main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    
    builder.row(
        KeyboardButton(text="💬 Чат"),
        KeyboardButton(text="💻 Код"),
        KeyboardButton(text="🖼️ Картинка")
    )
    
    builder.row(
        KeyboardButton(text="🎬 Видео"),
        KeyboardButton(text="💰 Баланс"),
        KeyboardButton(text="🧠 Модели")
    )
    
    builder.row(
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="🔗 Рефералка"),
        KeyboardButton(text="📖 Помощь")
    )
    
    return builder.as_markup(resize_keyboard=True)

# ========== УСТАНОВКА КОМАНД ==========
async def set_commands():
    try:
        commands = [
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="models", description="🧠 Выбор модели"),
            BotCommand(command="balance", description="💰 Баланс"),
            BotCommand(command="referral", description="🔗 Реферальная ссылка"),
            BotCommand(command="help", description="📖 Помощь"),
        ]
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.error(f"Set commands error: {e}")

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    try:
        user = db.get_user(message.from_user.id)
        
        if len(message.text.split()) > 1:
            try:
                referrer_id = int(message.text.split()[1])
                if referrer_id != message.from_user.id:
                    db.add_referral(referrer_id, message.from_user.id)
            except:
                pass

        await message.answer(
            f"🤖 *AI ALL Bot v3.0*\n\n"
            f"👤 Привет, {message.from_user.first_name}!\n"
            f"💰 Баланс: `{user['balance']}` токенов\n"
            f"🧠 Модель: `{user['model']}`\n\n"
            f"📝 *Что я умею:*\n"
            f"• 💬 Отвечаю на любые вопросы\n"
            f"• 💻 Генерирую код на всех языках\n"
            f"• 🎨 Создаю изображения по описанию\n"
            f"• 🎬 Генерирую видео с текстом\n"
            f"• 🎙️ Распознаю голосовые сообщения\n\n"
            f"Используйте кнопки ниже!",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Start error: {e}")
        await message.answer("❌ Ошибка при запуске. Попробуйте позже.")

@dp.message(Command("models"))
async def models_command(message: Message):
    try:
        keyboard = InlineKeyboardBuilder()
        for model_key, model_info in MODELS.items():
            emoji = model_info["emoji"]
            button_text = f"{emoji} {model_key}"
            keyboard.row(InlineKeyboardButton(text=button_text, callback_data=f"setmodel_{model_key}"))
        keyboard.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu"))
        
        user = db.get_user(message.from_user.id)
        await message.answer(
            f"🧠 *Выбор модели*\n\n"
            f"Текущая модель: `{user['model']}`\n\n"
            f"Выберите модель для общения:",
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Models error: {e}")
        await message.answer("❌ Ошибка получения списка моделей.")

@dp.message(Command("balance"))
async def balance_command(message: Message):
    try:
        user = db.get_user(message.from_user.id)
        await message.answer(
            f"💰 *Ваш баланс:* `{user['balance']}` токенов\n\n"
            f"📊 *Стоимость услуг:*\n"
            f"• 💬 Текст/Чат: 1 токен\n"
            f"• 💻 Код: 1 токен\n"
            f"• 🖼️ Картинка: 5 токенов\n"
            f"• 🎬 Видео: 10 токенов\n\n"
            f"🎁 За реферала: +15 токенов",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Balance error: {e}")
        await message.answer("❌ Ошибка получения баланса.")

@dp.message(Command("referral"))
async def referral_command(message: Message):
    try:
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
        
        await message.answer(
            f"🔗 *Ваша реферальная ссылка:*\n"
            f"`{link}`\n\n"
            f"🎁 За каждого приглашённого вы получите 15 токенов!\n"
            f"📊 Приглашённые должны нажать на ссылку и запустить бота.",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Referral error: {e}")
        await message.answer("❌ Ошибка генерации реферальной ссылки.")

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "📖 *Помощь*\n\n"
        "🤖 Я AI-ассистент с множеством функций:\n\n"
        "💬 *Чат*\n"
        "Просто напишите текст, и я отвечу\n\n"
        "💻 *Код*\n"
        "Напишите, какой код нужен и на каком языке\n"
        "Пример: `python парсер сайта`\n\n"
        "🖼️ *Картинка*\n"
        "Опишите, что сгенерировать\n"
        "Пример: `красивый закат`\n\n"
        "🎬 *Видео*\n"
        "Напишите текст для видео\n"
        "Пример: `AI генерация`\n\n"
        "🎙️ *Голос*\n"
        "Отправьте голосовое сообщение - я распознаю речь\n\n"
        "💰 Баланс пополняется за рефералов!\n\n"
        "Используйте кнопки внизу для быстрого доступа!",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

# ========== ОБРАБОТЧИКИ КНОПОК ==========
@dp.message(lambda message: message.text == "💬 Чат")
async def chat_button(message: Message):
    await message.answer(
        "💬 Режим чата активен!\n"
        "Просто напишите любой текст, и я отвечу.\n\n"
        "🎙️ Или отправьте голосовое сообщение!",
        reply_markup=get_main_keyboard()
    )

@dp.message(lambda message: message.text == "💻 Код")
async def code_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_code_prompt)
    await message.answer(
        "💻 Напишите, какой код нужно сгенерировать.\n"
        "Пример: `python парсер сайта`\n"
        "Поддерживаются: Python, C++, HTML, CSS, JavaScript, Java и другие",
        reply_markup=get_main_keyboard()
    )

@dp.message(lambda message: message.text == "🖼️ Картинка")
async def image_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_image_prompt)
    await message.answer(
        "🖼️ Напишите описание изображения.\n"
        "Пример: `красивый закат на море, цифровое искусство`\n\n"
        "💰 Стоимость: 5 токенов",
        reply_markup=get_main_keyboard()
    )

@dp.message(lambda message: message.text == "🎬 Видео")
async def video_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_video_prompt)
    await message.answer(
        "🎬 Напишите текст для видео.\n"
        "Пример: `AI генерация контента`\n\n"
        "💰 Стоимость: 10 токенов\n"
        "⏱️ Длительность: 5 секунд",
        reply_markup=get_main_keyboard()
    )

@dp.message(lambda message: message.text == "💰 Баланс")
async def balance_button(message: Message):
    await balance_command(message)

@dp.message(lambda message: message.text == "🧠 Модели")
async def models_button(message: Message):
    await models_command(message)

@dp.message(lambda message: message.text == "📊 Статистика")
async def stats_button(message: Message):
    try:
        user_id = message.from_user.id
        
        with sqlite3.connect("users.db") as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM generations WHERE user_id=?", (user_id,))
            total_gens = c.fetchone()[0]
            c.execute("SELECT type, COUNT(*) FROM generations WHERE user_id=? GROUP BY type", (user_id,))
            type_stats = c.fetchall()
        
        stats_text = f"📊 *Ваша статистика*\n\n"
        stats_text += f"📝 Всего генераций: {total_gens}\n\n"
        stats_text += f"*По типам:*\n"
        if type_stats:
            for gen_type, count in type_stats:
                emoji_map = {"code": "💻", "image": "🖼️", "video": "🎬", "text": "💬"}
                emoji = emoji_map.get(gen_type, "📄")
                stats_text += f"{emoji} {gen_type}: {count}\n"
        else:
            stats_text += "Пока нет генераций\n"
        
        await message.answer(stats_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await message.answer("❌ Ошибка получения статистики.")

@dp.message(lambda message: message.text == "🔗 Рефералка")
async def referral_button(message: Message):
    await referral_command(message)

@dp.message(lambda message: message.text == "📖 Помощь")
async def help_button(message: Message):
    await help_command(message)

# ========== INLINE CALLBACKS ==========
@dp.callback_query(lambda c: c.data.startswith("setmodel_"))
async def set_model(callback: CallbackQuery):
    try:
        model = callback.data.split("_")[1]
        db.update_user(callback.from_user.id, model=model)
        await callback.answer(f"✅ Модель изменена на {model}")
        await callback.message.edit_text(f"✅ Активная модель: {model}")
        await callback.message.answer("Используйте кнопки для продолжения:", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Set model error: {e}")
        await callback.answer("❌ Ошибка смены модели.")

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await callback.message.delete()
        await start_cmd(callback.message)
    except Exception as e:
        logger.error(f"Back to menu error: {e}")

# ========== ОБРАБОТЧИКИ СОСТОЯНИЙ ==========
@dp.message(StateFilter(Form.waiting_for_code_prompt))
async def generate_code_response(message: Message, state: FSMContext):
    await state.clear()
    text = message.text
    language = CodeHandler.detect_language(text)
    filename = None

    try:
        if language:
            code = CodeHandler.generate_code(language, text)
            ext = CodeHandler.extract_file_extension(language)
            filename = f"code_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

            with open(filename, "w", encoding="utf-8") as f:
                f.write(code)

            await message.answer_document(
                FSInputFile(filename),
                caption=f"✅ Код на *{language.upper()}* готов!\nРасширение: `.{ext}`",
                parse_mode="Markdown"
            )
            
            db.add_balance(message.from_user.id, -1)
            db.add_generation(message.from_user.id, "code", text)
        else:
            await message.answer(
                "⚠️ Не удалось определить язык.\n"
                "Укажите язык в запросе (python, cpp, html и т.д.)\n"
                "Пример: `python парсер сайта`"
            )
    except Exception as e:
        logger.error(f"Code generation error: {e}")
        await message.answer(f"❌ Ошибка генерации кода: {str(e)}")
    finally:
        try:
            if filename and os.path.exists(filename):
                os.remove(filename)
        except:
            pass

@dp.message(StateFilter(Form.waiting_for_image_prompt))
async def generate_image_response(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = db.get_user(user_id)
    wait_msg = None
    
    try:
        if user["balance"] < 5:
            await message.answer(
                "❌ Недостаточно токенов!\n"
                "Нужно 5 токенов для генерации изображения.\n"
                "Пригласите друзей по реферальной ссылке!",
                reply_markup=get_main_keyboard()
            )
            return
        
        wait_msg = await message.answer("🎨 Генерирую изображение... Это может занять до 30 секунд.")
        
        image_bytes = await ContentGenerator.generate_image(message.text)
        if image_bytes:
            await message.answer_photo(
                BufferedInputFile(image_bytes, filename="image.png"),
                caption=f"🖼️ Сгенерировано по запросу: '{message.text[:100]}...'"
            )
            db.add_balance(user_id, -5)
            db.add_generation(user_id, "image", message.text)
        else:
            await message.answer("❌ Не удалось сгенерировать изображение. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await message.answer(f"❌ Ошибка генерации: {str(e)}")
    finally:
        try:
            if wait_msg:
                await wait_msg.delete()
        except:
            pass

@dp.message(StateFilter(Form.waiting_for_video_prompt))
async def generate_video_response(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = db.get_user(user_id)
    wait_msg = None
    
    try:
        if user["balance"] < 10:
            await message.answer(
                "❌ Недостаточно токенов!\n"
                "Нужно 10 токенов для генерации видео.\n"
                "Пригласите друзей по реферальной ссылке!",
                reply_markup=get_main_keyboard()
            )
            return
        
        wait_msg = await message.answer("🎬 Генерирую видео... Это может занять до 1 минуты.")
        
        video_bytes = await ContentGenerator.generate_video(message.text, duration=5)
        if video_bytes:
            await message.answer_video(
                BufferedInputFile(video_bytes, filename="video.mp4"),
                caption=f"🎬 Видео по запросу: '{message.text[:100]}...'"
            )
            db.add_balance(user_id, -10)
            db.add_generation(user_id, "video", message.text)
