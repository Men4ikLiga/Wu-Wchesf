import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Твой токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Фиксированное смещение МСК+1 (UTC+4)
# Это избавит нас от ошибок UnknownTimeZoneError
BOT_TZ = timezone(timedelta(hours=4))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
# Передаем наш объект timezone напрямую в планировщик
scheduler = AsyncIOScheduler(timezone=BOT_TZ)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я бот для создания опросов.\n"
        "Пиши так: <b>/op Заголовок, Вариант 1, Вариант 2 . 15:20</b>\n\n"
        "Время указывай по твоему местному (МСК+1).",
        parse_mode="HTML"
    )

async def send_poll_results(chat_id: int, message_id: int):
    try:
        poll = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
        
        text = f"📊 <b>Результаты опроса: {poll.question}</b>\n\n"
        for option in poll.options:
            text += f"▪️ {option.text}: {option.voter_count} чел.\n"
        text += f"\nВсего голосов: {poll.total_voter_count}"
        
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка: {e}")

@dp.message(Command("op"))
async def create_poll(message: types.Message):
    raw_text = message.text.replace('/op ', '', 1).strip()
    
    if '.' not in raw_text:
        await message.answer("Нужна точка перед временем. Пример: /op Тест, Да, Нет . 15:00")
        return
        
    try:
        poll_data, time_str = raw_text.split('.', 1)
        time_str = time_str.strip()
        
        poll_parts = [part.strip() for part in poll_data.split(',')]
        if len(poll_parts) < 3:
            await message.answer("Мало данных. Нужно: Заголовок, Ответ1, Ответ2")
            return
            
        question = poll_parts[0]
        options = poll_parts[1:]

        # Парсим время и выставляем сегодняшнюю дату в нашем часовом поясе
        target_time = datetime.strptime(time_str, "%H:%M")
        now = datetime.now(BOT_TZ)
        run_date = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)

        # Если время уже прошло сегодня, планируем на завтра
        if run_date < now:
            run_date += timedelta(days=1)

        sent_poll = await message.answer_poll(
            question=question,
            options=options,
            is_anonymous=False
        )
        
        # Планируем задачу
        scheduler.add_job(
            send_poll_results,
            trigger='date', # Выполнится один раз в указанную дату/время
            run_date=run_date,
            args=[message.chat.id, sent_poll.message_id]
        )
        
        await message.answer(f"✅ Опрос запущен. Итоги пришлю в {run_date.strftime('%H:%M')}.")
        
    except ValueError:
        await message.answer("Неверный формат времени. Нужно ЧЧ:ММ")
    except Exception as e:
        await message.answer(f"Что-то пошло не так: {e}")

async def main():
    logging.basicConfig(level=logging.INFO)
    scheduler.start()
    await dp.start_polling(bot)

if name == "__main__":
    asyncio.run(main())
