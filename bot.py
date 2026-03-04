import pyTelegramBotAPIimport os
import os
import threading
import time
from datetime import datetime

# Берем токен из переменных окружения Railway
TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Функция для ожидания времени и отправки отчета
def schedule_report(chat_id, poll_id, target_time_str):
    try:
        # Ждем, пока наступит нужное время
        while True:
            now = datetime.now().strftime("%H:%M")
            if now == target_time_str:
                # Получаем данные опроса (в простейшем виде через остановку)
                # Важно: Telegram не дает обновлений о каждом голосе без БД, 
                # поэтому мы останавливаем опрос, чтобы увидеть финальные цифры.
                result = bot.stop_poll(chat_id, poll_id)
                
                report = f"📊 Результаты голосования ({target_time_str}):\n"
                for option in result.options:
                    report += f"— {option.text}: {option.voter_count} чел.\n"
                
                bot.send_message(chat_id, report)
                break
            time.sleep(30) # Проверяем раз в полминуты
    except Exception as e:
        print(f"Ошибка в планировщике: {e}")

@bot.message_handler(func=lambda message: True)
def handle_poll_request(message):
    try:
        # Парсинг строки: "Вариант 1, вариант 2. 15:30"
        # 1. Отделяем время (после точки)
        parts = message.text.split('.')
        if len(parts) < 2:
            bot.reply_to(message, "Ошибка! Забыли точку перед временем (например: Вариант 1, Вариант 2. 15:30)")
            return
            
        options_part = parts[0].strip()
        time_part = parts[1].strip()

        # 2. Разделяем варианты (по запятой)
        options = [opt.strip() for opt in options_part.split(',')]
        
        if len(options) < 2:
            bot.reply_to(message, "Нужно минимум 2 варианта через запятую!")
            return

        # 3. Отправляем опрос (не викторина, а обычный)
        sent_poll = bot.send_poll(
            chat_id=message.chat.id,
            question="Ваш выбор:",
            options=options,
            is_anonymous=False  # Можно поставить True, если анонимно
        )

        bot.reply_to(message, f"✅ Опрос создан! Пришлю результаты в {time_part}")

        # 4. Запускаем отдельный поток для слежки за временем
        thread = threading.Thread(target=schedule_report, args=(message.chat.id, sent_poll.poll.id, time_part))
        thread.start()

    except Exception as e:
        bot.reply_to(message, "Что-то пошло не так. Проверь формат: Вариант 1, Вариант 2. 15:30")

bot.infinity_polling()
