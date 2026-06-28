import os, g4f, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command

bot = Bot(token=os.getenv("TOKEN"))
dp = Dispatcher()

# Структура моделей
AI_MODELS = {
    "DeepSeek": {"Chat": g4f.models.deepseek_chat, "Reasoning": g4f.models.deepseek_r1},
    "Claude": {"Opus": g4f.models.claude_3_opus, "Sonnet": g4f.models.claude_3_5_sonnet},
    "Gemini": {"Flash": g4f.models.gemini_flash, "Pro": g4f.models.gemini}
}

user_data = {}

def get_ai_kb():
    buttons = [[InlineKeyboardButton(text=ai, callback_data=f"ai_{ai}")] for ai in AI_MODELS]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("Выберите нейросеть:", reply_markup=get_ai_kb())

@dp.callback_query(F.data.startswith("ai_"))
async def select_version(call):
    ai = call.data.split("_")[1]
    btns = [[InlineKeyboardButton(text=v, callback_data=f"ver_{ai}_{v}")] for v in AI_MODELS[ai]]
    await call.message.edit_text(f"Выберите версию {ai}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("ver_"))
async def save_config(call):
    _, ai, ver = call.data.split("_")
    user_data[call.from_user.id] = {"model": AI_MODELS[ai][ver]}
    await call.message.edit_text(f"Выбрано: {ai} {ver}. Теперь напишите промпт (код).")

@dp.message(F.text)
async def chat(m: Message):
    cfg = user_data.get(m.from_user.id)
    if not cfg: return await m.answer("Сначала выберите ИИ через /start")
    
    msg = await m.answer("Генерирую...")
    try:
        res = g4f.ChatCompletion.create(model=cfg['model'], messages=[{"role": "user", "content": m.text}])
        with open("code.txt", "w", encoding="utf-8") as f: f.write(res)
        await m.answer_document(FSInputFile("code.txt"), caption=f"Код готов ({m.text[:20]}...)")
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        await bot.delete_message(m.chat.id, msg.message_id)

if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
