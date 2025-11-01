import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

# --- Токен (ты указываешь в переменной окружения) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Расписание уроков ---
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "физра", "Технология", "Технология", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский", "Алгебра", "Информатика", "Биология", "Английский / Технология", "Английский / Технология"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литра"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский", "История", "Геометрия"],
}

# --- Память для ДЗ ---
homeworks = {}  # { "день": { "предмет": "дз" } }
current_subject = None
current_day = None

DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

# --- Вспомогательные функции ---
def next_day_of_week(day: str):
    """Возвращает дату следующего дня недели"""
    today = datetime.now()
    target = DAYS_ORDER.index(day)
    current = DAYS_ORDER.index(today.strftime("%a").lower()[:2])
    delta = (target - current) % 5 or 5
    return today + timedelta(days=delta)

def cleanup_old_homeworks():
    """Удаляем устаревшее ДЗ (после дня урока)"""
    today_name = datetime.now().strftime("%a").lower()[:2]
    if today_name in homeworks:
        del homeworks[today_name]

# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я бот для домашки.\n"
        "Команды:\n"
        "/add_dz — добавить домашку\n"
        "/dz — показать все домашки\n"
        "/clear — очистить всё ДЗ"
    )

async def add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление ДЗ (пошагово или списком)"""
    global current_subject, current_day

    text = update.message.text.replace("/add_dz", "").strip()
    if not text:
        await update.message.reply_text(
            "✍️ Отправь список ДЗ в формате:\n"
            "Русский - параграф 6\n"
            "Физика - упражнения 3-5\n\n"
            "или напиши день недели в начале:\n"
            "Вт:\nРусский - упр. 7"
        )
        return

    # Проверяем, начинается ли с дня недели
    first_line = text.split("\n")[0].lower().replace(":", "").strip()
    if first_line in DAYS_ORDER:
        current_day = first_line
        lines = text.split("\n")[1:]
    else:
        # если нет — используем текущий день
        today = datetime.now().strftime("%a").lower()[:2]
        current_day = today
        lines = text.split("\n")

    if current_day not in homeworks:
        homeworks[current_day] = {}

    for line in lines:
        if "-" in line:
            subject, dz = line.split("-", 1)
            homeworks[current_day][subject.strip()] = dz.strip()

    await update.message.reply_text(f"✅ ДЗ сохранено на {current_day.upper()}!")

async def show_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_old_homeworks()
    if not homeworks:
        await update.message.reply_text("📭 ДЗ нет — всё сделано!")
        return

    msg = "📘 Домашние задания:\n\n"
    for day, subjects in homeworks.items():
        msg += f"🗓 {day.upper()}:\n"
        for sub, dz in subjects.items():
            msg += f"• {sub}: {dz}\n"
        msg += "\n"

    await update.message.reply_text(msg)

async def clear_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    homeworks.clear()
    await update.message.reply_text("🧹 Все домашки удалены!")

# --- Запуск приложения ---
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_dz", add_dz))
    app.add_handler(CommandHandler("dz", show_dz))
    app.add_handler(CommandHandler("clear", clear_dz))

    print("✅ Бот запущен...")
    await app.run_polling()

# 🚀 Для совместимости с async runtime:
if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except RuntimeError:
        # Если event loop уже запущен (на хостинге)
        asyncio.run(main())
