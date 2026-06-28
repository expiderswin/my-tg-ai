import os, g4f, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Хранилище состояний
user_state = {}

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Модель: GPT-4o"), KeyboardButton(text="Модель: Claude")],
        [KeyboardButton(text="Модель: DeepSeek"), KeyboardButton(text="Модель: Gemini")],
        [KeyboardButton(text="Версия: Lite"), KeyboardButton(text="Версия: Pro")]
    ], resize_keyboard=True)

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Python", callback_data="lang_py"), InlineKeyboardButton(text="C++", callback_data="lang_cpp")],
        [InlineKeyboardButton(text="JS", callback_data="lang_js")]
    ])

@dp.message(Command("start"))
async def start(m: Message):
    user_state[m.from_user.id] = {"model": "gpt-4o", "ver": "Pro", "lang": "Python"}
    await m.answer("Выберите модель и версию внизу, а язык программирования — кнопками:", reply_markup=main_kb())
    await m.answer("Язык кода:", reply_markup=lang_kb())

@dp.message(F.text.startswith("Модель:"))
async def set_model(m: Message):
    user_state[m.from_user.id]["model"] = m.text.split(": ")[1]
    await m.answer(f"Модель {m.text} активна.")

@dp.message(F.text.startswith("Версия:"))
async def set_ver(m: Message):
    user_state[m.from_user.id]["ver"] = m.text.split(": ")[1]
    await m.answer(f"Версия {m.text} активна.")

# Обработка голоса
@dp.message(F.voice)
async def handle_voice(m: Message):
    file_id = m.voice.file_id
    file = await bot.get_file(file_id)
    # Здесь логика: скачивание -> преобразование в текст (через библиотеку speech_recognition)
    await m.answer("Голосовое сообщение получено. Анализирую...")
    # ... логика перевода в текст ...

@dp.message(F.text)
async def chat(m: Message):
    if any(w in m.text.lower() for w in ["чит", "hack", "dll"]): return await m.answer("Запрещено.")
    
    cfg = user_state.get(m.from_user.id, {"model": "gpt-4o", "ver": "Pro", "lang": "Python"})
    prompt = f"Язык: {cfg['lang']}. Задача: {m.text}"
    
    try:
        res = g4f.ChatCompletion.create(model=cfg['model'], messages=[{"role": "user", "content": prompt}])
        await m.answer(res[:4000])
    except Exception as e:
        await m.answer(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
