import asyncio
import logging
from datetime import datetime
import pytz

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Твой токен бота
BOT_TOKEN = "ТВОЙ_ТОКЕН_ЗДЕСЬ"

# Часовой пояс МСК+1 (UTC+4)
TIMEZONE = pytz.timezone('Asia/Samara')

# Инициализация бота, диспетчера и планировщика
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

# --- Обработчик команды /start ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для создания опросов.\n"
        "Отправь мне команду в формате:\n"
        "/op Название опроса, Вариант 1, Вариант 2 . 15:20\n\n"
        "Время указывай по МСК+1."
    )

# --- Функция, которая вызовется в назначенное время ---
async def send_poll_results(chat_id: int, message_id: int):
    try:
        # Останавливаем опрос, чтобы получить результаты
        poll = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
        
        # Формируем текст с результатами
        text = f"📊 <b>Результаты опроса: {poll.question}</b>\n\n"
        for option in poll.options:
            text += f"▪️ {option.text}: {option.voter_count} голос(ов)\n"
            
        text += f"\nВсего голосов: {poll.total_voter_count}"
        
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка при получении результатов: {e}")
        await bot.send_message(chat_id=chat_id, text="Не удалось получить результаты опроса. Возможно, сообщение было удалено.")

# --- Обработчик команды /op ---
@dp.message(Command("op"))
async def create_poll(message: types.Message):
    # Убираем саму команду из текста
    raw_text = message.text.replace('/op ', '', 1).strip()
    
    # Проверяем, есть ли точка (разделитель времени)
    if '.' not in raw_text:
        await message.answer("Ошибка формата. Не забыл точку перед временем? Пример: /op Вопрос, Да, Нет . 15:20")
        return
        
    try:
        # Разделяем на данные опроса и время
        poll_data, time_str = raw_text.split('.', 1)
        time_str = time_str.strip()
        
        # Разделяем название и варианты ответа
        poll_parts = [part.strip() for part in poll_data.split(',')]
        if len(poll_parts) < 3:
            await message.answer("Нужно указать название и минимум 2 варианта ответа через запятую.")
            return
            
        question = poll_parts[0]
        options = poll_parts[1:]
        
        # Проверяем лимит Telegram (от 2 до 10 вариантов)
        if len(options) > 10:
            await message.answer("Telegram поддерживает максимум 10 вариантов ответа.")
            return

        # Парсим время
        target_time = datetime.strptime(time_str, "%H:%M")
        hour = target_time.hour
        minute = target_time.minute
        
        # Отправляем опрос (is_anonymous=False, если хочешь видеть, кто как голосовал)
        sent_message = await message.answer_poll(
            question=question,
            options=options,
            is_anonymous=False
        )
        
        # Планируем задачу на отправку результатов
        scheduler.add_job(
            send_poll_results,
            trigger='cron',
            hour=hour,
            minute=minute,
            args=[message.chat.id, sent_message.message_id]
        )
        
        await message.answer(f"✅ Опрос создан! Результаты придут в {time_str} (МСК+1).")
        
    except ValueError:
        await message.answer("Ошибка в формате времени. Пожалуйста, используй формат ЧЧ:ММ (например, 15:20).")
    except Exception as e:
        await message.answer(f"Произошла непредвиденная ошибка: {e}")

# --- Запуск бота ---
async def main():
    logging.basicConfig(level=logging.INFO)
    # Запускаем планировщик
    scheduler.start()
    # Запускаем поллинг
    await dp.start_polling(bot)

if name == "__main__":
    asyncio.run(main())
