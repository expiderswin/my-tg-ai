import os, g4f, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Список доступных ИИ и языков
MODELS = ["gpt-4o", "gemini", "claude-3-opus", "deepseek-chat"]
LANGS = ["Python", "C++", "C#", "JavaScript", "HTML/CSS"]

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Выбрать модель"), KeyboardButton(text="Выбрать язык")],
        [KeyboardButton(text="Баланс"), KeyboardButton(text="Рефералка")]
    ], resize_keyboard=True)

# Хранилище выбора пользователя
user_pref = {} 

@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("Добро пожаловать в AI-студию. Выберите настройки для генерации.", reply_markup=main_kb())

@dp.message(F.text == "Выбрать модель")
async def ask_model(m: Message):
    kb = [[KeyboardButton(text=f"AI:{model}")] for model in MODELS]
    await m.answer("Выберите нейросеть:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.text == "Выбрать язык")
async def ask_lang(m: Message):
    kb = [[KeyboardButton(text=f"Lang:{lang}")] for lang in LANGS]
    await m.answer("Выберите язык:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.text.startswith(("AI:", "Lang:")))
async def save_pref(m: Message):
    if m.text.startswith("AI:"): user_pref[m.from_user.id] = {"model": m.text[3:]}
    else: user_pref[m.from_user.id] = {**user_pref.get(m.from_user.id, {"model": "gpt-4o"}), "lang": m.text[5:]}
    await m.answer("Настройка сохранена.", reply_markup=main_kb())

@dp.message(F.text)
async def chat(m: Message):
    if any(w in m.text.lower() for w in ["чит", "hack", "dll"]): 
        return await m.answer("Запрос отклонен: нарушение безопасности.")
    
    pref = user_pref.get(m.from_user.id, {"model": "gpt-4o", "lang": "Python"})
    prompt = f"Напиши код на {pref['lang']} по запросу: {m.text}"
    
    msg = await m.answer("Генерирую...")
    try:
        res = g4f.ChatCompletion.create(model=pref['model'], messages=[{"role": "user", "content": prompt}])
        
        fname = f"code_{m.from_user.id}.txt"
        with open(fname, "w", encoding="utf-8") as f: f.write(res)
        
        await m.answer(f"Результат ({pref['model']} / {pref['lang']}):\n{res[:1000]}...")
        await m.answer_document(FSInputFile(fname))
        os.remove(fname)
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        await bot.delete_message(m.chat.id, msg.message_id)

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
