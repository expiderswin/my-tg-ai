import os, g4f, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Используем строки вместо обращения к объектам g4f.models.X
# Это гарантирует, что ошибки AttributeError не будет
AI_MODELS = {
    "DeepSeek": {"Chat": "deepseek-chat", "Reasoning": "deepseek-r1"},
    "Claude": {"Opus": "claude-3-opus", "Sonnet": "claude-3.5-sonnet"},
    "Gemini": {"Flash": "gemini-flash", "Pro": "gemini-pro"}
}
LANGS = ["Python", "C++", "C#", "JavaScript", "SQL"]
user_data = {}

@dp.message(Command("start"))
async def start(m: Message):
    btns = [[InlineKeyboardButton(text=ai, callback_data=f"ai_{ai}")] for ai in AI_MODELS]
    await m.answer("Выберите нейросеть:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("ai_"))
async def select_ver(call):
    ai = call.data.split("_")[1]
    btns = [[InlineKeyboardButton(text=v, callback_data=f"ver_{ai}_{v}")] for v in AI_MODELS[ai]]
    await call.message.edit_text(f"Выберите версию {ai}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("ver_"))
async def select_lang(call):
    _, ai, ver = call.data.split("_")
    user_data[call.from_user.id] = {"model": AI_MODELS[ai][ver]}
    btns = [[InlineKeyboardButton(text=l, callback_data=f"lang_{l}")] for l in LANGS]
    await call.message.edit_text("Выберите язык:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("lang_"))
async def finalize(call):
    lang = call.data.split("_")[1]
    user_data[call.from_user.id]["lang"] = lang
    await call.message.edit_text(f"Настройка: {user_data[call.from_user.id]['model']} / {lang}. Пишите промпт.")

@dp.message(F.text)
async def chat(m: Message):
    # Блок безопасности
    if any(w in m.text.lower() for w in ["чит", "hack", "dll", "взлом"]):
        return await m.answer("⚠️ Запрос заблокирован.")
    
    cfg = user_data.get(m.from_user.id)
    if not cfg: return await m.answer("Введите /start")
    
    msg = await m.answer("⏳ Генерация...")
    try:
        # Используем модель как строку
        res = g4f.ChatCompletion.create(model=cfg['model'], messages=[{"role": "user", "content": m.text}])
        fname = f"code_{cfg['lang'].lower()}.txt"
        with open(fname, "w", encoding="utf-8") as f: f.write(res)
        await m.answer_document(FSInputFile(fname), caption="Код готов.")
        os.remove(fname)
    except Exception as e:
        await m.answer(f"Ошибка ИИ: {e}")
    finally:
        await bot.delete_message(m.chat.id, msg.message_id)

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
