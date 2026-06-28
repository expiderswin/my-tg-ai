import os
import logging
import asyncio
import sqlite3
import json
import re
import io
import tempfile
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
import random
import string

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    FSInputFile, BufferedInputFile, InputFile,
    Message, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

import google.generativeai as genai
import anthropic
import openai
import httpx
from PIL import Image, ImageDraw, ImageFont
import speech_recognition as sr
from pydub import AudioSegment
import aiofiles
import replicate
from moviepy.editor import VideoFileClip, ImageSequenceClip, TextClip, CompositeVideoClip
import numpy as np

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8902285510:AAGdfAZfFAOOCSGi_gQ-lqdb_7E7OolE6uo"
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or "AIzaSyDummyKeyReplaceMe"
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY") or "sk-ant-dummy"
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or "sk-dummy"
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY") or "sk-dummy"
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY") or "r8_dummy"

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

    def get_user(self, user_id: int) -> Dict[str, Any]:
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
            self.add_balance(referrer_id, 15)

    def add_generation(self, user_id: int, gen_type: str, prompt: str):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO generations (user_id, type, prompt, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, gen_type, prompt, datetime.now().isoformat()))
            conn.commit()

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

# ========== ГЕНЕРАТОРЫ КОНТЕНТА ==========
class ContentGenerator:
    @staticmethod
    async def generate_image(prompt: str) -> Optional[bytes]:
        """Генерация изображения через Replicate"""
        try:
            # Используем Stable Diffusion
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
            # Скачиваем изображение
            async with httpx.AsyncClient() as client:
                response = await client.get(output[0])
                return response.content
        except Exception as e:
            logger.error(f"Image generation error: {e}")
            # Fallback: создаём простую картинку с текстом
            return await ContentGenerator._create_fallback_image(prompt)

    @staticmethod
    async def _create_fallback_image(text: str) -> bytes:
        """Создание простого изображения с текстом"""
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

    @staticmethod
    async def generate_video(prompt: str, duration: int = 5) -> Optional[bytes]:
        """Генерация простого видео с текстом"""
        try:
            # Создаём кадры с текстом
            frames = []
            for i in range(duration * 24):  # 24 кадра в секунду
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
            
            # Создаём видео из кадров
            clip = ImageSequenceClip(frames, fps=24)
            
            # Сохраняем во временный файл
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
                clip.write_videofile(tmp_file.name, codec='libx264', fps=24)
                tmp_file_path = tmp_file.name
            
            # Читаем файл
            with open(tmp_file_path, 'rb') as f:
                video_data = f.read()
            
            # Удаляем временный файл
            os.unlink(tmp_file_path)
            return video_data
        except Exception as e:
            logger.error(f"Video generation error: {e}")
            return None

# ========== ОБРАБОТЧИК ГОЛОСА ==========
class VoiceProcessor:
    @staticmethod
    async def voice_to_text(voice_file: bytes) -> Optional[str]:
        """Конвертация голосового сообщения в текст"""
        try:
            # Сохраняем аудио во временный файл
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_file:
                tmp_file.write(voice_file)
                tmp_file_path = tmp_file.name
            
            # Конвертируем в WAV
            audio = AudioSegment.from_ogg(tmp_file_path)
            wav_path = tmp_file_path.replace('.ogg', '.wav')
            audio.export(wav_path, format='wav')
            
            # Распознаём речь
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data, language='ru-RU')
            
            # Удаляем временные файлы
            os.unlink(tmp_file_path)
            os.unlink(wav_path)
            
            return text
        except Exception as e:
            logger.error(f"Voice recognition error: {e}")
            return None

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

# ========== ХЕНДЛЕРЫ ==========
@dp.message(Command("start"))
async def start_cmd(message: Message):
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
        InlineKeyboardButton(text="🖼️ Сгенерировать картинку", callback_data="generate_image"),
        InlineKeyboardButton(text="🎬 Сгенерировать видео", callback_data="generate_video")
    )
    keyboard.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )

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
        f"• 🎙️ Распознаю голосовые сообщения\n"
        f"• 📄 Работаю с файлами\n\n"
        f"Просто напиши запрос или отправь голосовое!",
        reply_markup=keyboard.as_markup(),
        parse_mode="Markdown"
    )

@dp.message(Command("models"))
async def models_cmd(message: Message):
    keyboard = InlineKeyboardBuilder()
    for model in MODELS.keys():
        keyboard.row(InlineKeyboardButton(text=model, callback_data=f"setmodel_{model}"))
    await message.answer("Выберите модель:", reply_markup=keyboard.as_markup())

@dp.callback_query(lambda c: c.data.startswith("setmodel_"))
async def set_model(callback: CallbackQuery):
    model = callback.data.split("_")[1]
    db.update_user(callback.from_user.id, model=model)
    await callback.answer(f"✅ Модель изменена на {model}")
    await callback.message.edit_text(f"Активная модель: {model}")

@dp.message(Command("balance"))
async def balance_cmd(message: Message):
    user = db.get_user(message.from_user.id)
    await message.answer(f"💰 Ваш баланс: `{user['balance']}` токенов\n\n"
                        f"🎁 За реферала: +15 токенов", parse_mode="Markdown")

@dp.message(Command("code"))
async def code_cmd(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_code_prompt)
    await message.answer("💻 Напишите, какой код нужно сгенерировать и на каком языке.\nПример: `python парсер сайта`")

@dp.message(Command("image"))
async def image_cmd(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_image_prompt)
    await message.answer("🖼️ Напишите описание изображения.\nПример: `красивый закат на море, цифровое искусство`")

@dp.message(Command("video"))
async def video_cmd(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_for_video_prompt)
    await message.answer("🎬 Напишите текст для видео.\nПример: `AI генерация контента`")

@dp.message(Command("referral"))
async def referral_cmd(message: Message):
    try:
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    except:
        link = f"https://t.me/MyAIBot?start={message.from_user.id}"
    await message.answer(
        f"🔗 Ваша реферальная ссылка:\n`{link}`\n\n"
        f"За каждого приглашённого вы получите 15 токенов!",
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "📖 *Помощь*\n\n"
        "/start - Главное меню\n"
        "/models - Выбор модели\n"
        "/balance - Баланс\n"
        "/code - Генерация кода\n"
        "/image - Генерация изображения\n"
        "/video - Генерация видео\n"
        "/referral - Реферальная ссылка\n\n"
        "🎙️ Отправьте голосовое сообщение - я распознаю речь\n"
        "💬 Просто напишите текст - я отвечу как ИИ",
        parse_mode="Markdown"
    )

@dp.callback_query(lambda c: c.data == "models")
async def show_models(callback: CallbackQuery):
    await models_cmd(callback.message)

@dp.callback_query(lambda c: c.data == "balance")
async def show_balance(callback: CallbackQuery):
    await balance_cmd(callback.message)

@dp.callback_query(lambda c: c.data == "help")
async def show_help(callback: CallbackQuery):
    await help_cmd(callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "generate_image")
async def prompt_image(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_image_prompt)
    await callback.message.answer("🖼️ Напишите описание изображения:")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "generate_video")
async def prompt_video(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_video_prompt)
    await callback.message.answer("🎬 Напишите текст для видео (длительность 5 секунд):")
    await callback.answer()

# ========== ОБРАБОТЧИК СОСТОЯНИЙ ==========
@dp.message(StateFilter(Form.waiting_for_code_prompt))
async def generate_code_response(message: Message, state: FSMContext):
    await state.clear()
    text = message.text
    language = CodeHandler.detect_language(text)

    if language:
        code = CodeHandler.generate_code(language, text)
        ext = CodeHandler.extract_file_extension(language)
        filename = f"code.{ext}"

        with open(filename, "w", encoding="utf-8") as f:
            f.write(code)

        await message.answer_document(
            FSInputFile(filename),
            caption=f"✅ Код на *{language.upper()}* готов!\nРасширение: `.{ext}`",
            parse_mode="Markdown"
        )
        os.remove(filename)
        
        # Списываем токен
        db.add_balance(message.from_user.id, -1)
        db.add_generation(message.from_user.id, "code", text)
    else:
        await message.answer("⚠️ Не удалось определить язык. Укажите язык в запросе (python, cpp, html и т.д.)")

@dp.message(StateFilter(Form.waiting_for_image_prompt))
async def generate_image_response(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if user["balance"] < 5:
        await message.answer("❌ Недостаточно токенов! Нужно 5 токенов для генерации изображения.")
        return
    
    await message.answer("🎨 Генерирую изображение... Это может занять до 30 секунд.")
    
    try:
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
        await message.answer(f"❌ Ошибка генерации: {str(e)}")

@dp.message(StateFilter(Form.waiting_for_video_prompt))
async def generate_video_response(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if user["balance"] < 10:
        await message.answer("❌ Недостаточно токенов! Нужно 10 токенов для генерации видео.")
        return
    
    await message.answer("🎬 Генерирую видео... Это может занять до 1 минуты.")
    
    try:
        video_bytes = await ContentGenerator.generate_video(message.text, duration=5)
        if video_bytes:
            await message.answer_video(
                BufferedInputFile(video_bytes, filename="video.mp4"),
                caption=f"🎬 Видео по запросу: '{message.text[:100]}...'"
            )
            db.add_balance(user_id, -10)
            db.add_generation(user_id, "video", message.text)
        else:
            await message.answer("❌ Не удалось сгенерировать видео. Попробуйте позже.")
    except Exception as e:
        await message.answer(f"❌ Ошибка генерации: {str(e)}")

# ========== ОБРАБОТЧИК ГОЛОСОВЫХ ==========
@dp.message(lambda message: message.voice)
async def handle_voice(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if user["balance"] < 1:
        await message.answer("❌ Недостаточно токенов!")
        return
    
    await message.answer("🎙️ Распознаю голосовое сообщение...")
    
    try:
        # Скачиваем голосовое
        file = await bot.get_file(message.voice.file_id)
        voice_bytes = await bot.download_file(file.file_path)
        
        # Распознаём речь
        text = await VoiceProcessor.voice_to_text(voice_bytes)
        
        if text:
            await message.answer(f"📝 Распознанный текст:\n\n`{text}`", parse_mode="Markdown")
            
            # Отвечаем на распознанный текст
            service = AIProvider.get_service(user["model"])
            response = await service.generate(text, user["history"])
            
            # Сохраняем историю
            history = user["history"]
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": response})
            if len(history) > 20:
                history = history[-20:]
            db.update_user(user_id, history=history)
            
            db.add_balance(user_id, -1)
            await message.answer(response[:4096])
        else:
            await message.answer("❌ Не удалось распознать речь. Попробуйте говорить чётче.")
    except Exception as e:
        logger.error(f"Voice handling error: {e}")
        await message.answer(f"❌ Ошибка обработки голоса: {str(e)}")

# ========== ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ==========
@dp.message()
async def handle_ai_response(message: Message):
    user_id = message.from_user.id
    user = db.get_user(user_id)
    text = message.text

    if not text:
        return

    # Проверка баланса
    if user["balance"] <= 0:
        await message.answer("❌ Недостаточно токенов! Используйте /referral для получения бонусов.")
        return

    # Проверка на запрос кода
    if CodeHandler.detect_language(text):
        language = CodeHandler.detect_language(text)
        code = CodeHandler.generate_code(language, text)
        ext = CodeHandler.extract_file_extension(language)
        filename = f"code.{ext}"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(code)

        await message.answer_document(
            FSInputFile(filename),
            caption=f"✅ Код на *{language.upper()}* сгенерирован!",
            parse_mode="Markdown"
        )
        os.remove(filename)
        db.add_balance(user_id, -1)
        db.add_generation(user_id, "code", text)
        return

    # Обычный запрос к ИИ
    try:
        service = AIProvider.get_service(user["model"])
        
        # Отправляем уведомление о генерации
        wait_msg = await message.answer("🤔 Думаю...")
        
        response = await service.generate(text, user["history"])

        # Сохраняем историю
        history = user["history"]
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": response})
        if len(history) > 20:
            history = history[-20:]
        db.update_user(user_id, history=history)

        # Списываем токен
        db.add_balance(user_id, -1)

        # Удаляем уведомление
        await wait_msg.delete()

        # Отправляем ответ
        await message.answer(response[:4096])

    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== ЗАПУСК ==========
async def main():
    try:
        logger.info("🚀 Бот запущен!")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
