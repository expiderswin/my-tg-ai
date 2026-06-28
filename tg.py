import os, g4f, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Режим пользователя
user_settings = {}

# Reply-кнопки (всегда внизу)
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Баланс"), KeyboardButton(text="GPT-4o")],
        [KeyboardButton(text="Claude-3"), KeyboardButton(text="DeepSeek")]
    ], resize_keyboard=True)

# Inline-кнопки (выбор языка в чате)
def lang_kb():
    langs = ["Python", "C++", "C#", "JS", "SQL"]
    btns = [[InlineKeyboardButton(text=l, callback_data=f"lang_{l}")] for l in langs]
    return InlineKeyboardMarkup(inline_keyboard=btns)

@dp.message(Command("start"))
async def start(m: Message):
    user_settings[m.from_user.id] = {"model": g4f.models.gpt_4o, "lang": "Python"}
    await m.answer("Привет! Выберите язык программирования кнопкой ниже:", reply_markup=lang_kb())
    await m.answer("Управление моделями внизу:", reply_markup=main_kb())

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(call):
    lang = call.data.split("_")[1]
    user_settings[call.from_user.id]["lang"] = lang
    await call.message.edit_text(f"Язык установлен: {lang}")

@dp.message(F.text.in_(["GPT-4o", "Claude-3", "DeepSeek"]))
async def set_model(m: Message):
    models = {"GPT-4o": g4f.models.gpt_4o, "Claude-3": g4f.models.claude_3_opus, "DeepSeek": "deepseek-chat"}
    user_settings[m.from_user.id]["model"] = models[m.text]
    await m.answer(f"Модель {m.text} выбрана.")

@dp.message(F.text == "Баланс")
async def bal(m: Message):
    await m.answer("Ваш баланс: 9,999,000 токенов.")

@dp.message(F.text)
async def chat(m: Message):
    if any(w in m.text.lower() for w in ["чит", "hack", "dll"]): return await m.answer("Заблокировано.")
    
    cfg = user_settings.get(m.from_user.id, {"model": g4f.models.gpt_4o, "lang": "Python"})
    
    msg = await m.answer("⏳ Генерация...")
    try:
        res = g4f.ChatCompletion.create(model=cfg['model'], messages=[{"role": "user", "content": f"Напиши код на {cfg['lang']}: {m.text}"}])
        fname = f"code_{cfg['lang'].lower()}.txt"
        with open(fname, "w", encoding="utf-8") as f: f.write(res)
        await m.answer_document(FSInputFile(fname), caption=f"Язык: {cfg['lang']}")
        os.remove(fname)
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        await bot.delete_message(m.chat.id, msg.message_id)

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
