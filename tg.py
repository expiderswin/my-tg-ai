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

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton,
    ReplyKeyboardMarkup, FSInputFile, BufferedInputFile
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
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
ADMIN_ID = 7615846791

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDummyKey")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY", "sk-ant-dummy")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "sk-dummy")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-dummy")
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY", "r8_dummy")

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
                    is_banned BOOLEAN DEFAULT 0
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

db = Database()

# ========== AI ПРОВАЙДЕРЫ ==========
# (оставлены без изменений - см. предыдущие версии)

# ========== FSM СОСТОЯНИЯ ==========
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
    builder.row(KeyboardButton(text="⚡ Админ-панель"))
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
    return user_id == ADMIN_ID

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
        f"🤖 *AI Bot*\n"
        f"💰 Баланс: `{user['balance']}`\n"
        f"🧠 Модель: `{user['model']}`",
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

# ========== АДМИН-КНОПКИ ==========
@dp.message(lambda m: m.text == "⚡ Админ-панель")
async def admin_panel_btn(message: Message):
    await admin_panel(message)

@dp.message(lambda m: m.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    total_users = db.get_total_users()
    total_gens = db.get_total_generations()
    today_users = db.get_today_users()
    
    await message.answer(
        f"📊 *Статистика*\n\n"
        f"👥 Всего: {total_users}\n"
        f"🆕 Сегодня: {today_users}\n"
        f"📝 Генераций: {total_gens}",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == "📋 Список пользователей")
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

@dp.message(lambda m: m.text == "📢 Рассылка")
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

@dp.message(lambda m: m.text == "🚫 Бан пользователя")
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
            db.add_admin_log(ADMIN_ID, "ban", user_id)
            await state.clear()
            await message.answer(f"✅ Пользователь {user_id} забанен!", reply_markup=get_admin_keyboard())
            try:
                await bot.send_message(user_id, "🚫 Вы были забанены!")
            except:
                pass
    except ValueError:
        await message.answer("❌ Неверный ID!")

@dp.message(lambda m: m.text == "✅ Разбан пользователя")
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
            db.add_admin_log(ADMIN_ID, "unban", user_id)
            await state.clear()
            await message.answer(f"✅ Пользователь {user_id} разбанен!", reply_markup=get_admin_keyboard())
            try:
                await bot.send_message(user_id, "✅ Вы были разбанены!")
            except:
                pass
    except ValueError:
        await message.answer("❌ Неверный ID!")

@dp.message(lambda m: m.text == "⚡ Топ пользователей")
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

@dp.message(lambda m: m.text == "🔙 Назад в меню")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню", reply_markup=get_main_keyboard())

# ========== ОБРАБОТЧИКИ СОСТОЯНИЙ ДЛЯ ГЕНЕРАЦИИ ==========
# (добавьте сюда ваши обработчики для кода, картинок, видео)

# ========== ЗАПУСК ==========
async def main():
    logger.info("🚀 Бот запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
