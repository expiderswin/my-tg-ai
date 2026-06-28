import os, g4f, asyncio, sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()
db = sqlite3.connect("bot_data.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, b INTEGER DEFAULT 10000, ref_count INTEGER DEFAULT 0)")

# Расширенные модели
MODELS = {
    "GPT": ["gpt-4o", "gpt-4o-mini"],
    "Claude": ["claude-3-opus", "claude-3.5-sonnet", "claude-3-haiku"],
    "DeepSeek": ["deepseek-chat", "deepseek-r1"],
    "Gemini": ["gemini-1.5-flash", "gemini-1.5-pro"]
}
user_cfg = {}

def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Баланс"), KeyboardButton(text="Рефералка")],
        [KeyboardButton(text="Выбрать модель")]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def start(m: Message):
    db.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (m.from_user.id,))
    db.commit()
    await m.answer("Добро пожаловать в AI All.", reply_markup=get_main_kb())

@dp.message(F.text == "Выбрать модель")
async def show_models(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=k, callback_data=f"ai_{k}")] for k in MODELS])
    await m.answer("Выберите ИИ:", reply_markup=kb)

@dp.callback_query(F.data.startswith("ai_"))
async def show_vers(call):
    ai = call.data.split("_")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=v, callback_data=f"ver_{v}")] for v in MODELS[ai]])
    await call.message.edit_text(f"Версии {ai}:", reply_markup=kb)

@dp.callback_query(F.data.startswith("ver_"))
async def set_ver(call):
    ver = call.data.split("_")[1]
    user_cfg[call.from_user.id] = ver
    await call.message.edit_text(f"Активна модель: {ver}")

@dp.message(F.text == "Баланс")
async def show_bal(m: Message):
    b = db.execute("SELECT b FROM users WHERE id=?", (m.from_user.id,)).fetchone()[0]
    await m.answer(f"Баланс: {b} токенов.")

@dp.message(F.text)
async def chat(m: Message):
    ver = user_cfg.get(m.from_user.id, "gpt-4o")
    b = db.execute("SELECT b FROM users WHERE id=?", (m.from_user.id,)).fetchone()[0]
    if b < 100: return await m.answer("Недостаточно средств.")
    
    msg = await m.answer("Думаю...")
    try:
        # Используем g4f без кук (авто-выбор провайдера)
        res = g4f.ChatCompletion.create(model=ver, messages=[{"role": "user", "content": m.text}])
        db.execute("UPDATE users SET b = b - 100 WHERE id=?", (m.from_user.id,))
        db.commit()
        await m.answer(res[:4000])
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        await bot.delete_message(m.chat.id, msg.message_id)

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
