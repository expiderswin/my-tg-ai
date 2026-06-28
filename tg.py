import asyncio
import os
import g4f
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from db import init_db, get_balance, update_balance

API_TOKEN = os.getenv("TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Словарь моделей
MODEL_MAP = {
    "GPT-4o": g4f.models.gpt_4o,
    "Claude-3": g4f.models.claude_3_opus,
    "Gemini": g4f.models.gemini,
    "DeepSeek": g4f.models.deepseek_chat
}

def get_kb():
    kb = [
        [KeyboardButton(text="Баланс"), KeyboardButton(text="Выбор модели")],
        [KeyboardButton(text="GPT-4o"), KeyboardButton(text="Claude-3"), KeyboardButton(text="DeepSeek")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(F.text == "Баланс")
async def show_balance(message: Message):
    bal = get_balance(message.from_user.id)
    await message.answer(f"💰 Ваш баланс: {bal:,} токенов.")

@dp.message(F.text.in_(MODEL_MAP.keys()))
async def set_model(message: Message, state: dict = {}):
    state[message.from_user.id] = message.text
    await message.answer(f"✅ Модель {message.text} активна.")

@dp.message(F.text)
async def ai_handler(message: Message, state: dict = {}):
    # Проверка на читы
    if any(w in message.text.lower() for w in ["чит", "hack", "dll", "взлом"]):
        await message.answer("⚠️ Запрос заблокирован системой безопасности.")
        return

    # Проверка баланса
    bal = get_balance(message.from_user.id)
    if bal < 1000:
        await message.answer("❌ Недостаточно токенов!")
        return

    model_name = state.get(message.from_user.id, "GPT-4o")
    update_balance(message.from_user.id, 1000) # Списание 1000 за запрос

    msg = await message.answer("⏳ Генерирую...")
    
    try:
        response = g4f.ChatCompletion.create(model=MODEL_MAP[model_name], messages=[{"role": "user", "content": message.text}])
        
        # Создание файла
        file_path = f"output.txt"
        with open(file_path, "w", encoding="utf-8") as f: f.write(response)
        
        await message.answer(f"Готово (списано 1000 токенов):")
        await message.answer_document(FSInputFile(file_path), caption="Результат в файле")
        os.remove(file_path)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
    finally:
        await bot.delete_message(message.chat.id, msg.message_id)

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
