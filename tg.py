import os
import logging
import asyncio
import sqlite3
import json
import re
import io
import tempfile
from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()  # загружаем переменные из .env

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton,
    ReplyKeyboardMarkup, FSInputFile, BufferedInputFile,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import BotCommand, BotCommandScopeDefault

import google.generativeai as genai
import anthropic
import openai
import httpx
from PIL import Image, ImageDraw, ImageFont
import speech_recognition as sr
from pydub import AudioSegment
import replicate
import numpy as np

# ========== MOVIEPY (опционально) ==========
try:
    from moviepy import ImageSequenceClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    try:
        from moviepy.editor import ImageSequenceClip
        MOVIEPY_AVAILABLE = True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        logging.warning("⚠️ MoviePy не установлен. Генерация видео недоступна.")

# ========== КОНФИГУРАЦИЯ (из .env) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")   # теперь это строка

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в .env!")

if not all([GEMINI_KEY, CLAUDE_KEY, OPENAI_KEY, DEEPSEEK_KEY, REPLICATE_API_KEY]):
    logging.warning("⚠️ Некоторые API-ключи отсутствуют! Проверьте .env файл.")

# ========== МОДЕЛИ ==========
MODELS = {
    "gemini-2.0-flash": {"provider": "gemini", "context": 1000000},
    "gemini-1.5-pro": {"provider": "gemini", "context": 2000000},
    "claude-3.5-sonnet": {"provider": "claude", "context": 200000},
    "claude-3-haiku": {"provider": "claude", "context": 200000},
    "gpt-4o": {"provider": "openai", "context": 128000},
    "gpt-4-turbo": {"provider": "openai", "context": 128000},
    "deepseek-chat": {"provider": "deepseek", "context": 64000},
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
                    created_at TEXT,
                    is_banned BOOLEAN DEFAULT 0,
                    username TEXT,
                    first_name TEXT
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
                c.execute("""CREATE TABLE IF NOT EXISTS admin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    action TEXT,
                    target_id INTEGER,
                    details TEXT,
                    created_at TEXT
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"DB init error: {e}")

    def get_user(self, user_id: int) -> Dict[str, Any]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT balance, model, history, is_banned FROM users WHERE user_id=?", (user_id,))
                row = c.fetchone()
                if not row:
                    c.execute("INSERT INTO users (user_id, balance, created_at) VALUES (?, ?, ?)",
                              (user_id, 150, datetime.now().isoformat()))
                    conn.commit()
                    return {"balance": 150, "model": DEFAULT_MODEL, "history": [], "is_banned": False}
                return {"balance": row[0], "model": row[1], "history": json.loads(row[2]), "is_banned": row[3] == 1}
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return {"balance": 150, "model": DEFAULT_MODEL, "history": [], "is_banned": False}

    def get_all_users(self) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT user_id, balance, model, created_at, is_banned FROM users ORDER BY created_at DESC")
                rows = c.fetchall()
                return [
                    {
                        "user_id": row[0],
                        "balance": row[1],
                        "model": row[2],
                        "created_at": row[3],
                        "is_banned": row[4] == 1
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Get all users error: {e}")
            return []

    def get_total_users(self) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM users")
                return c.fetchone()[0]
        except Exception as e:
            logger.error(f"Get total users error: {e}")
            return 0

    def get_total_generations(self) -> int:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM generations")
                return c.fetchone()[0]
        except Exception as e:
            logger.error(f"Get total generations error: {e}")
            return 0

    def get_today_users(self) -> int:
        try:
            today = datetime.now().date().isoformat()
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = ?", (today,))
                return c.fetchone()[0]
        except Exception as e:
            logger.error(f"Get today users error: {e}")
            return 0

    def ban_user(self, user_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Ban user error: {e}")
            return False

    def unban_user(self, user_id: int) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Unban user error: {e}")
            return False

    def add_admin_log(self, admin_id: int, action: str, target_id: int = None, details: str = None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO admin_logs (admin_id, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
                          (admin_id, action, target_id, details, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Add admin log error: {e}")

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

    def add_balance(self, user_id: int, amount: int):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
                conn.commit()
        except Exception as e:
            logger.error(f"Add balance error: {e}")

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

db = Database()

# ========== AI ПРОВАЙДЕРЫ ==========
class AIProvider:
    @staticmethod
    def get_service(model_name: str):
        try:
            provider = MODELS.get(model_name, MODELS[DEFAULT_MODEL])["provider"]
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
                return "❌ Gemini не инициализирован"
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
                return "❌ Claude не инициализирован"
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
                return "❌ OpenAI не инициализирован"
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
            if not REPLICATE_API_KEY:
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
        if not MOVIEPY_AVAILABLE:
            return None
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

# ========== FSM ==========
class Form(StatesGroup):
    waiting_for_code_prompt = State()
    waiting_for_image_prompt = State()
    waiting_for_video_prompt = State()
    waiting_for_broadcast = State()
    waiting_for_ban_user = State()
    waiting_for_unban_user = State()

# ========== MIDDLEWARE ==========
class AntiSpamMiddleware:
    def __init__(self, time_window: int = 5, max_requests: int = 3):
        self.time_window = time_window
        self.max_requests = max_requests
        self.user_requests = defaultdict(list)

    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text and not event.text.startswith('/'):
            user_id = event.from_user.id
            current_time = datetime.now()
            self.user_requests[user_id] = [
                req_time for req_time in self.user_requests[user_id]
                if (current_time - req_time).total_seconds() < self.time_window
            ]
            if len(self.user_requests[user_id]) >= self.max_requests:
                await event.answer(f"⏳ Подождите {self.time_window} секунд!")
                return
            self.user_requests[user_id].append(current_time)
        return await handler(event, data)

class BanMiddleware:
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user = db.get_user(event.from_user.id)
            if user.get("is_banned", False):
                await event.answer("🚫 Вы забанены! Обратитесь к администратору.")
                return
        return await handler(event, data)

class BanCallbackMiddleware:
    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            user = db.get_user(event.from_user.id)
            if user.get("is_banned", False):
                await event.answer("🚫 Вы забанены!", show_alert=True)
                return
        return await handler(event, data)

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== ПОДКЛЮЧЕНИЕ MIDDLEWARE ==========
dp.message.middleware(AntiSpamMiddleware(time_window=5, max_requests=3))
dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanCallbackMiddleware())

# ========== КЛАВИАТУРЫ ==========
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
    # Кнопка "Админ-панель" УДАЛЕНА – доступ только через /admin
    return builder.as_markup(resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📊 Статистика бота"),
        KeyboardButton(text="📋 Список пользователей")
    )
    builder.row(
        KeyboardButton(text="📢 Рассылка"),
        KeyboardButton(text="🚫 Бан пользователя")
    )
    builder.row(
        KeyboardButton(text="✅ Разбан пользователя"),
        KeyboardButton(text="⚡ Топ пользователей")
    )
    builder.row(KeyboardButton(text="🔙 Назад в меню"))
    return builder.as_markup(resize_keyboard=True)

# ========== ПРОВЕРКА АДМИНА ==========
def is_admin(user_id: int) -> bool:
    if ADMIN_ID is None:
        return False
    return str(user_id) == str(ADMIN_ID)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
    user = db.get_user(message.from_user.id)
    if len(message.text.split()) > 1:
        try:
            referrer_id = int(message.text.split()[1])
            if referrer_id != message.from_user.id:
                db.add_referral(referrer_id, message.from_user.id)
        except:
            pass
    await message.answer(
        f"🤖 *AI Bot v3.0*\n\n"
        f"👤 Привет, {message.from_user.first_name}!\n"
        f"💰 Баланс: `{user['balance']}` токенов\n"
        f"🧠 Модель: `{user['model']}`\n\n"
        f"Используйте кнопки ниже!",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён!")
        return
    total_users = db.get_total_users()
    total_gens = db.get_total_generations()
    today_users = db.get_today_users()
    await message.answer(
        f"⚡ *Админ-панель*\n\n"
        f"👥 Всего: {total_users}\n"
        f"🆕 Сегодня: {today_users}\n"
        f"📝 Генераций: {total_gens}",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

# ========== ОБРАБОТЧИКИ КНОПОК ==========
@dp.message(F.text == "💬 Чат")
async def chat_button(message: Message):
    await message.answer("💬 Режим чата активен!\nПросто напишите любой текст, и я отвечу.\n\n🎙️ Или отправьте голосовое сообщение!")

@dp.message(F.text == "💻 Код")
async def code_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_code_prompt)
    await message.answer("💻 Напишите, какой код нужно сгенерировать.\nПример: `python парсер сайта`\nПоддерживаются: Python, C++, HTML, CSS, JavaScript, Java и другие")

@dp.message(F.text == "🖼️ Картинка")
async def image_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_image_prompt)
    await message.answer("🖼️ Напишите описание изображения.\nПример: `красивый закат на море, цифровое искусство`\n\n💰 Стоимость: 5 токенов")

@dp.message(F.text == "🎬 Видео")
async def video_button(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_video_prompt)
    await message.answer("🎬 Напишите текст для видео.\nПример: `AI генерация контента`\n\n💰 Стоимость: 10 токенов\n⏱️ Длительность: 5 секунд")

@dp.message(F.text == "💰 Баланс")
async def balance_button(message: Message):
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"💰 *Ваш баланс:* `{user['balance']}` токенов\n\n"
        f"📊 *Стоимость услуг:*\n"
        f"• 💬 Текст/Чат: 1 токен\n"
        f"• 💻 Код: 1 токен\n"
        f"• 🖼️ Картинка: 5 токенов\n"
        f"• 🎬 Видео: 10 токенов\n\n"
        f"🎁 За реферала: +15 токенов",
        parse_mode="Markdown"
    )

@dp.message(F.text == "🧠 Модели")
async def models_button(message: Message):
    keyboard = InlineKeyboardBuilder()
    for model_key in MODELS.keys():
        keyboard.row(InlineKeyboardButton(text=model_key, callback_data=f"setmodel_{model_key}"))
    keyboard.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    user = db.get_user(message.from_user.id)
    await message.answer(
        f"🧠 *Выбор модели*\n\n"
        f"Текущая модель: `{user['model']}`",
        reply_markup=keyboard.as_markup(),
        parse_mode="Markdown"
    )

@dp.message(F.text == "📊 Статистика")
async def stats_button(message: Message):
    user = db.get_user(message.from_user.id)
    total_gens = db.get_total_generations()
    await message.answer(
        f"📊 *Ваша статистика*\n\n"
        f"💰 Баланс: {user['balance']}\n"
        f"🧠 Модель: {user['model']}\n"
        f"📝 Всего генераций в боте: {total_gens}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "🔗 Рефералка")
async def referral_button(message: Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 *Ваша реферальная ссылка:*\n"
        f"`{link}`\n\n"
        f"🎁 За каждого приглашённого вы получите 15 токенов!",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📖 Помощь")
async def help_button(message: Message):
    await message.answer(
        "📖 *Помощь*\n\n"
        "🤖 Я AI-ассистент с множеством функций:\n\n"
        "💬 *Чат* - просто напишите текст\n"
        "💻 *Код* - укажите язык и задачу\n"
        "🖼️ *Картинка* - опишите что сгенерировать\n"
        "🎬 *Видео* - напишите текст для видео\n"
        "🎙️ *Голос* - отправьте голосовое сообщение\n\n"
        "💰 Баланс пополняется за рефералов!",
        parse_mode="Markdown"
    )

# ========== АДМИН-КНОПКИ (без отдельной кнопки в меню) ==========
@dp.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    total_users = db.get_total_users()
    total_gens = db.get_total_generations()
    today_users = db.get_today_users()
    await message.answer(
        f"📊 *Статистика бота*\n\n"
        f"👥 Всего: {total_users}\n"
        f"🆕 Сегодня: {today_users}\n"
        f"📝 Генераций: {total_gens}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "📋 Список пользователей")
async def admin_users_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = db.get_all_users()
    if not users:
        await message.answer("📭 Нет пользователей")
        return
    text = "📋 *Пользователи (первые 10):*\n\n"
    for user in users[:10]:
        status = "🚫" if user.get("is_banned", False) else "✅"
        text += f"{status} ID: `{user['user_id']}` | 💰 {user['balance']}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Form.waiting_for_broadcast)
    await message.answer(
        "📢 Введите текст для рассылки:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(StateFilter(Form.waiting_for_broadcast))
async def process_broadcast(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_admin_keyboard())
        return
    if not is_admin(message.from_user.id):
        return
    await message.answer("⏳ Начинаю рассылку...")
    users = db.get_all_users()
    success, fail = 0, 0
    for user in users:
        try:
            await bot.send_message(
                user["user_id"],
                f"📢 *Сообщение администратора:*\n\n{message.text}",
                parse_mode="Markdown"
            )
            success += 1
        except:
            fail += 1
        await asyncio.sleep(0.05)
    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n📨 Успешно: {success}\n❌ Ошибок: {fail}",
        reply_markup=get_admin_keyboard()
    )

@dp.message(F.text == "🚫 Бан пользователя")
async def admin_ban(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Form.waiting_for_ban_user)
    await message.answer(
        "🚫 Введите ID пользователя:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(StateFilter(Form.waiting_for_ban_user))
async def process_ban(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_admin_keyboard())
        return
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
        if db.ban_user(user_id):
            db.add_admin_log(int(ADMIN_ID), "ban", user_id)
            await state.clear()
            await message.answer(f"✅ Пользователь {user_id} забанен!", reply_markup=get_admin_keyboard())
            try:
                await bot.send_message(user_id, "🚫 Вы были забанены!")
            except:
                pass
    except ValueError:
        await message.answer("❌ Неверный ID!")

@dp.message(F.text == "✅ Разбан пользователя")
async def admin_unban(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Form.waiting_for_unban_user)
    await message.answer(
        "✅ Введите ID пользователя:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(StateFilter(Form.waiting_for_unban_user))
async def process_unban(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_admin_keyboard())
        return
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
        if db.unban_user(user_id):
            db.add_admin_log(int(ADMIN_ID), "unban", user_id)
            await state.clear()
            await message.answer(f"✅ Пользователь {user_id} разбанен!", reply_markup=get_admin_keyboard())
            try:
                await bot.send_message(user_id, "✅ Вы были разбанены!")
            except:
                pass
    except ValueError:
        await message.answer("❌ Неверный ID!")

@dp.message(F.text == "⚡ Топ пользователей")
async def admin_top_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = db.get_all_users()
    sorted_users = sorted(users, key=lambda x: x.get("balance", 0), reverse=True)[:10]
    if not sorted_users:
        await message.answer("📭 Нет данных")
        return
    text = "🏆 *Топ пользователей:*\n\n"
    for i, user in enumerate(sorted_users, 1):
        medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."
        text += f"{medal} ID: `{user['user_id']}` | 💰 {user['balance']}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🔙 Назад в меню")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню", reply_markup=get_main_keyboard())

# ========== ОБРАБОТЧИКИ СОСТОЯНИЙ (ГЕНЕРАЦИЯ) ==========
@dp.message(StateFilter(Form.waiting_for_code_prompt))
async def process_code_gen(message: Message, state: FSMContext):
    await state.clear()
    msg = await message.answer("⏳ Генерирую код...")
    lang = CodeHandler.detect_language(message.text) or "python"
    code = CodeHandler.generate_code(lang, message.text)
    await message.answer(f"💻 *Код ({lang}):*\n\n```{code}```", parse_mode="Markdown")
    db.add_generation(message.from_user.id, "code", message.text)
    await msg.delete()

@dp.message(StateFilter(Form.waiting_for_image_prompt))
async def process_image_gen(message: Message, state: FSMContext):
    await state.clear()
    msg = await message.answer("🎨 Рисую изображение, подождите 10-15 сек...")
    img_data = await ContentGenerator.generate_image(message.text)
    if img_data:
        await message.answer_photo(BufferedInputFile(img_data, filename="gen.png"))
    else:
        await message.answer("❌ Ошибка при создании картинки.")
    db.add_generation(message.from_user.id, "image", message.text)
    await msg.delete()

@dp.message(StateFilter(Form.waiting_for_video_prompt))
async def process_video_gen(message: Message, state: FSMContext):
    await state.clear()
    msg = await message.answer("🎬 Создаю видео... это займет время.")
    video_data = await ContentGenerator.generate_video(message.text)
    if video_data:
        await message.answer_video(BufferedInputFile(video_data, filename="video.mp4"))
    else:
        await message.answer("❌ Ошибка генерации видео (или не установлен FFmpeg).")
    db.add_generation(message.from_user.id, "video", message.text)
    await msg.delete()

# ========== ОБРАБОТЧИК ГОЛОСОВЫХ ==========
@dp.message(F.voice)
async def handle_voice(message: Message):
    user = db.get_user(message.from_user.id)
    if user.get("is_banned", False):
        await message.answer("🚫 Вы забанены!")
        return
    voice_file = await bot.get_file(message.voice.file_id)
    file_bytes = await bot.download_file(voice_file.file_path)
    msg = await message.answer("🎙️ Распознаю голос...")
    text = await VoiceProcessor.voice_to_text(file_bytes.read())
    if text:
        await message.answer(f"📝 Вы сказали: *{text}*", parse_mode="Markdown")
        service = AIProvider.get_service(user["model"])
        response = await service.generate(text, history=user["history"])
        history = user["history"]
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})
        if len(history) > 20:
            history = history[-20:]
        db.update_user(message.from_user.id, history=history)
        db.add_balance(message.from_user.id, -1)
        await message.answer(response[:4096])
    else:
        await message.answer("❌ Не удалось распознать голос.")
    await msg.delete()

# ========== ОСНОВНОЙ ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (ЧАТ) ==========
@dp.message(F.text)
async def chat_handler(message: Message):
    if message.text.startswith('/') or message.text in [
        "💬 Чат", "💻 Код", "🖼️ Картинка", "🎬 Видео",
        "💰 Баланс", "🧠 Модели", "📊 Статистика",
        "🔗 Рефералка", "📖 Помощь",
        "📊 Статистика бота", "📋 Список пользователей",
        "📢 Рассылка", "🚫 Бан пользователя",
        "✅ Разбан пользователя", "⚡ Топ пользователей",
        "🔙 Назад в меню", "❌ Отмена"
    ]:
        return
    user_id = message.from_user.id
    user = db.get_user(user_id)
    if user["balance"] <= 0:
        await message.answer("❌ Недостаточно токенов! Используйте /referral.")
        return
    if user.get("is_banned", False):
        await message.answer("🚫 Вы забанены!")
        return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        service = AIProvider.get_service(user["model"])
        response = await service.generate(message.text, history=user["history"])
        history = user["history"]
        history.append({"role": "user", "content": message.text})
        history.append({"role": "assistant", "content": response})
        if len(history) > 20:
            history = history[-20:]
        db.update_user(user_id, history=history)
        db.add_balance(user_id, -1)
        await message.answer(response[:4096])
    except Exception as e:
        logger.error(f"Chat handler error: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== ОБРАБОТЧИК ВЫБОРА МОДЕЛИ (CALLBACK) ==========
@dp.callback_query(F.data.startswith("setmodel_"))
async def set_model(callback: CallbackQuery):
    model_name = callback.data.split("_")[1]
    db.update_user(callback.from_user.id, model=model_name)
    await callback.message.edit_text(f"✅ Модель установлена: {model_name}")
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Главное меню", reply_markup=get_main_keyboard())
    await callback.answer()

# ========== ОСНОВНОЙ ЗАПУСК ==========
async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="admin", description="⚡ Админ-панель")
    ])
    logger.info("🚀 Бот успешно запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
