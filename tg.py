import asyncio
import os
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

# Получаем токен из настроек Railway (Environment Variables)
API_TOKEN = os.getenv("TOKEN")
INITIAL_TOKENS = 10000000

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, balance INTEGER, mode TEXT)''')
    conn.commit()
    conn.close()

def get_main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Баланс"), KeyboardButton(text="Реферальная программа")],
        [KeyboardButton(text="Выбор модели")]
    ], resize_keyboard=True)

def get_mode_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Режим: Codex"), KeyboardButton(text="Режим: Cursor")],
        [KeyboardButton(text="Назад")]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    init_db()
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, mode) VALUES (?, ?, ?)", 
                   (message.from_user.id, INITIAL_TOKENS, "Codex"))
    conn.commit()
    conn.close()
    await message.answer("Система готова. Выберите действие:", reply_markup=get_main_keyboard())

@dp.message(F.text == "Баланс")
async def show_balance(message: Message):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (message.from_user.id,))
    res = cursor.fetchone()
    conn.close()
    balance = res[0] if res else 0
    await message.answer(f"Ваш остаток: {balance:,} токенов.".replace(",", " "))

@dp.message(F.text == "Реферальная программа")
async def show_ref(message: Message):
    bot_info = await bot.get_me()
    await message.answer(f"Ссылка: https://t.me/{bot_info.username}?start={message.from_user.id}")

@dp.message(F.text == "Выбор модели")
async def choose_mode(message: Message):
    await message.answer("Выберите архитектуру:", reply_markup=get_mode_keyboard())

@dp.message(F.text.startswith("Режим:"))
async def set_mode(message: Message):
    mode = message.text.split(": ")[1]
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET mode = ? WHERE user_id = ?", (mode, message.from_user.id))
    conn.commit()
    conn.close()
    await message.answer(f"Активная модель: {mode}", reply_markup=get_main_keyboard())

@dp.message(F.text == "Назад")
async def back(message: Message):
    await message.answer("Главное меню.", reply_markup=get_main_keyboard())

@dp.message(F.text)
async def handle_request(message: Message):
    code_result = "def hello():\n    print('Hello World')"
    response = f"Результат генерации:\n\n```python\n{code_result}\n```"
    await message.answer(response, parse_mode="Markdown")

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())