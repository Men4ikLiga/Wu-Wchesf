import os
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# --- Токен бота ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# --- Расписание ---
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "физра", "Технология", "Технология", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский", "Алгебра", "Информатика", "Биология", "Английский / Технология", "Английский / Технология"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литра"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский", "История", "Геометрия"]
}

# --- Память для ДЗ ---
homeworks = {}  # { "день": { "предмет": "дз" } }
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]


# --- Вспомогательные функции ---
def cleanup_old_homeworks():
    """Удаляет ДЗ, если день прошёл"""
    today_name = datetime.now().strftime("%a").lower()[:2]
    if today_name in homeworks:
        del homeworks[today_name]


# --- Команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для домашки.\n\n"
        "Команды:\n"
        "/add_dz — добавить ДЗ\n"
        "/dz — показать все ДЗ\n"
        "/clear — удалить все домашки"
    )


async def add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление ДЗ"""
    text = update.message.text.replace("/add_dz", "").strip()
    if not text:
        await update.message.reply_text(
            "✍️ Напиши список ДЗ в формате:\n\n"
            "Вт:\nРусский - упр. 25\nФизика - параграф 7"
        )
        return

    lines = text.split("\n")
    current_day = datetime.now().strftime("%a").lower()[:2]

    if lines[0].lower().replace(":", "") in DAYS_ORDER:
        current_day = lines[0].lower().replace(":", "")
        lines = lines[1:]

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
        await update.message.reply_text("📭 Домашек нет — всё сделано!")
        return

    msg = "📘 Текущее ДЗ:\n\n"
    for day, subjects in homeworks.items():
        msg += f"🗓 {day.upper()}:\n"
        for sub, dz in subjects.items():
            msg += f"• {sub}: {dz}\n"
        msg += "\n"

    await update.message.reply_text(msg)


async def clear_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    homeworks.clear()
    await update.message.reply_text("🧹 Все домашки удалены!")


# --- Запуск ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_dz", add_dz))
    app.add_handler(CommandHandler("dz", show_dz))
    app.add_handler(CommandHandler("clear", clear_dz))

    print("✅ Бот запущен...")
    app.run_polling()  # Никакого asyncio — просто запуск


if __name__ == "__main__":
    main()
