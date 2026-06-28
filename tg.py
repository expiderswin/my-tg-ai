import os, g4f, sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()
db = sqlite3.connect("bot.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS u (id INTEGER PRIMARY KEY, b INTEGER DEFAULT 10000000)")

def kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Баланс"), KeyboardButton(text="Рефералка")]], resize_keyboard=True)

@dp.message(Command("start"))
async def start(m: Message):
    db.execute("INSERT OR IGNORE INTO u (id) VALUES (?)", (m.from_user.id,))
    db.commit()
    await m.answer("Добро пожаловать.", reply_markup=kb())

@dp.message(F.text == "Баланс")
async def bal(m: Message):
    b = db.execute("SELECT b FROM u WHERE id=?", (m.from_user.id,)).fetchone()[0]
    await m.answer(f"Баланс: {b} токенов.")

@dp.message(F.text == "Рефералка")
async def ref(m: Message):
    await m.answer(f"Ссылка: https://t.me/{(await bot.get_me()).username}?start={m.from_user.id}")

@dp.message(F.text)
async def chat(m: Message):
    if any(w in m.text.lower() for w in ["чит", "hack", "dll"]): return await m.answer("Запрещено.")
    if db.execute("SELECT b FROM u WHERE id=?", (m.from_user.id,)).fetchone()[0] < 1000: return await m.answer("Нет токенов.")
    
    db.execute("UPDATE u SET b = b - 1000 WHERE id=?", (m.from_user.id,))
    db.commit()
    res = g4f.ChatCompletion.create(model=g4f.models.gpt_4o, messages=[{"role": "user", "content": m.text}])
    await m.answer(f"Ответ:\n{res[:4000]}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
