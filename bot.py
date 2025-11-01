import datetime
import json
import os
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# =====================================================
# ВСТАВЬ СВОЙ ТОКЕН В ПЕРЕМЕННУЮ ОКРУЖЕНИЯ BOT_TOKEN
# На Render / Replit / Railway / PythonAnywhere добавь переменную:
# BOT_TOKEN = "твой токен"
# =====================================================

TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "dz.json"

# ====== РАСПИСАНИЕ ======
schedule = {
    "пн": ["Ров", "Русский язык", "Физра", "Технология", "Технология", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский", "Алгебра", "Информатика", "Биология", "Английский/Технология", "Английский/Технология"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литра"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский", "История", "Геометрия"]
}

# ====== ХРАНИЛИЩЕ ======
dz_data = {}

# ====== УТИЛИТЫ ======

def load_data():
    global dz_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                dz_data.update(json.load(f))
            except json.JSONDecodeError:
                dz_data.clear()

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dz_data, f, ensure_ascii=False, indent=2)

def parse_dz(text: str):
    parsed = []
    for line in text.split("\n"):
        if "-" in line:
            subj, task = line.split("-", 1)
            subj = subj.strip()
            task = task.strip()
            parsed.append((subj, task))
    return parsed

def find_next_day(subj, today_index):
    days = list(schedule.keys())
    for i in range(7):
        idx = (today_index + i) % 5
        day_key = days[idx]
        if any(subj.lower() in s.lower() for s in schedule[day_key]):
            return day_key
    return days[0]

def get_lesson_end_time(day, subj):
    lessons = schedule[day]
    if subj not in lessons:
        return None
    idx = lessons.index(subj)
    start = datetime.datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    total_minutes = 0
    for i in range(idx):
        total_minutes += 40
        total_minutes += 10 if i != 2 else 40
    end_time = start + datetime.timedelta(minutes=total_minutes + 40)
    return end_time.isoformat()

def remove_expired_dz():
    now = datetime.datetime.now()
    changed = False
    for day in list(dz_data.keys()):
        new_tasks = []
        for subj, task, end_time in dz_data[day]:
            if not end_time:
                new_tasks.append((subj, task, end_time))
            else:
                dt = datetime.datetime.fromisoformat(end_time)
                if dt > now:
                    new_tasks.append((subj, task, end_time))
        if new_tasks:
            dz_data[day] = new_tasks
        else:
            del dz_data[day]
            changed = True
    if changed:
        save_data()

# ====== ХЕНДЛЕРЫ ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для хранения домашки.\n"
        "Команды:\n"
        "/add_dz — добавить дз\n"
        "/dz — показать всё дз\n"
        "/clear — очистить всё"
    )

async def add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь список дз в формате:\n\nРусский - параграф 6\nФизика - упражнение 7\n\nКогда закончишь, напиши 'готово'.")
    context.user_data["adding"] = True
    context.user_data["buffer"] = []

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("adding"):
        text = update.message.text.strip()
        if text.lower() == "готово":
            added_text = "\n".join(context.user_data["buffer"])
            parsed = parse_dz(added_text)
            today = datetime.datetime.now().weekday()
            for subj, task in parsed:
                day = find_next_day(subj, today)
                if day not in dz_data:
                    dz_data[day] = []
                dz_data[day].append((subj, task, get_lesson_end_time(day, subj)))
            save_data()
            context.user_data["adding"] = False
            context.user_data["buffer"] = []
            await update.message.reply_text("✅ Домашка сохранена.")
        else:
            context.user_data["buffer"].append(text)
    else:
        await update.message.reply_text("Не понимаю сообщение. Используй /dz или /add_dz.")

async def show_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_expired_dz()
    if not dz_data:
        await update.message.reply_text("📭 Домашки нет!")
        return
    text = ""
    for day, tasks in dz_data.items():
        text += f"\n📅 *{day.upper()}*\n"
        for subj, task, _ in tasks:
            text += f"📘 {subj}: {task}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def clear_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dz_data.clear()
    save_data()
    await update.message.reply_text("🧹 Всё дз очищено.")

# ====== АВТОЧИСТКА ======
async def auto_cleanup():
    while True:
        remove_expired_dz()
        await asyncio.sleep(600)  # каждые 10 минут проверяет актуальность

# ====== MAIN ======
async def main():
    load_data()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_dz", add_dz))
    app.add_handler(CommandHandler("dz", show_dz))
    app.add_handler(CommandHandler("clear", clear_dz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    asyncio.create_task(auto_cleanup())

    print("✅ Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
