import os
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler, ConversationHandler
)

# ---------------- Настройки ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "dz.json"
HISTORY_FILE = "dz_history.json"
CD_FILE = "user_cd.json"
TZ = ZoneInfo("Europe/Amsterdam")
ADMIN_ID = 6193109213
COOLDOWN_HOURS = 4

WAIT_ADD = 1
WAIT_REMOVE = 2

# Словарь нормализации предметов
SUBJECT_ALIAS = {
    "русский": "Русский язык",
    "русский язык": "Русский язык",
    "английский": "Английский язык",
    "английский язык": "Английский язык",
    "технология": "Труд",
    "труд": "Труд",
    "литра": "Литература",
    "литература": "Литература",
}

# ---------------- Расписание ----------------
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Труд", "Труд", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский язык", "Алгебра", "Информатика", "Биология", "Английский язык", "Труд"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литература"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский язык", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский язык", "История", "Геометрия"]
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40
FIRST_LESSON_START = 8*60  # минуты от 00:00

# ---------------- Хранилище ----------------
dz_list = []
dz_history = []
user_cd = {}

# ---------------- Файлы ----------------
def load_data():
    global dz_list, dz_history, user_cd
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE,"r",encoding="utf-8") as f:
            dz_list[:] = json.load(f)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE,"r",encoding="utf-8") as f:
            dz_history[:] = json.load(f)
    if os.path.exists(CD_FILE):
        with open(CD_FILE,"r",encoding="utf-8") as f:
            user_cd.update(json.load(f))

def save_data():
    with open(DATA_FILE,"w",encoding="utf-8") as f:
        json.dump(dz_list,f,ensure_ascii=False,indent=2)
    with open(HISTORY_FILE,"w",encoding="utf-8") as f:
        json.dump(dz_history,f,ensure_ascii=False,indent=2)
    with open(CD_FILE,"w",encoding="utf-8") as f:
        json.dump(user_cd,f,ensure_ascii=False,indent=2)

# ---------------- Утилиты ----------------
def normalize_subject(name: str):
    return SUBJECT_ALIAS.get(name.strip().lower(), name.strip())

def weekday_name_from_date(d: date):
    wd = d.weekday()
    return DAYS_ORDER[wd] if wd < 5 else None

def lesson_start_end(d: date, idx: int):
    minutes = FIRST_LESSON_START
    for i in range(idx):
        minutes += LESSON_DURATION
        minutes += LONG_BREAK if i==2 else SHORT_BREAK
    start = datetime.combine(d, datetime.min.time(), tzinfo=TZ)+timedelta(minutes=minutes)
    end = start + timedelta(minutes=LESSON_DURATION)
    return start, end

def find_subject_positions(subject_name):
    res = []
    s = normalize_subject(subject_name).lower()
    for day, lessons in SCHEDULE.items():
        for idx, lesson in enumerate(lessons):
            if s in normalize_subject(lesson).lower():
                res.append((day, idx))
    return res

def next_date_for_day(day_key, from_dt=None):
    if from_dt is None:
        from_dt = datetime.now(TZ)
    today = from_dt.date()
    target = DAYS_ORDER.index(day_key)
    cur_wd = from_dt.weekday()
    delta_days = (target - cur_wd) % 7
    candidate = today + timedelta(days=delta_days)
    return candidate

def assign_one(subject, task):
    now = datetime.now(TZ)
    positions = find_subject_positions(subject)
    if not positions:
        return None
    candidates = []
    for day_key, idx in positions:
        candidate_date = next_date_for_day(day_key, now)
        _, end_dt = lesson_start_end(candidate_date, idx)
        if day_key == weekday_name_from_date(now.date()) and now >= end_dt:
            candidate_date += timedelta(days=7)
            _, end_dt = lesson_start_end(candidate_date, idx)
        candidates.append((end_dt, day_key, idx, candidate_date))
    candidates.sort(key=lambda x: x[0])
    end_dt, day_key, idx, assigned_date = candidates[0]
    record = {
        "subject": normalize_subject(subject),
        "task": task,
        "day": day_key,
        "lesson_index": idx,
        "assigned_date": assigned_date.isoformat(),
        "end_iso": end_dt.isoformat()
    }
    return record

def remove_expired():
    now = datetime.now(TZ)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= now]
    for r in removed:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
    dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]

def cooldown_check(user_id):
    now = datetime.now(TZ)
    if user_id not in user_cd:
        return True, None
    last = datetime.fromisoformat(user_cd[user_id])
    remaining = timedelta(hours=COOLDOWN_HOURS) - (now - last)
    if remaining.total_seconds() > 0:
        return False, remaining
    return True, None

def format_timedelta(td: timedelta):
    h, remainder = divmod(int(td.total_seconds()),3600)
    m, _ = divmod(remainder,60)
    parts=[]
    if h>0: parts.append(f"{h}ч")
    if m>0: parts.append(f"{m}м")
    return " ".join(parts) if parts else "0м"

def format_dz_for_display():
    remove_expired()
    if not dz_list:
        return "🗒 Домашек нет — всё чисто."
    grouped = {}
    for r in dz_list:
        grouped.setdefault(r["day"], []).append(r)
    text = "📚 ДОМАШНИЕ ЗАДАНИЯ\n"
    for day in DAYS_ORDER:
        if day not in grouped:
            continue
        text += f"\n🗓 {day.upper()}\n"
        lessons = sorted(grouped[day], key=lambda x:x["lesson_index"])
        for r in lessons:
            text += f"▫️ **{r['subject']}**\n> {r['task']}\n\n"
        text += "─ ─ ─\n"
    return text

def format_history():
    if not dz_history:
        return "Истории удалённых ДЗ пока нет."
    lines=[]
    for r in dz_history[-20:]:
        dt = r.get("removed_at","")
        lines.append(f"{dt[:16]} | {r['subject']} | {r['task']}")
    return "\n".join(lines)

# ---------------- Обработчики ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Добавить ДЗ", callback_data="add_dz"),
         InlineKeyboardButton("Удалить ДЗ", callback_data="remove_dz")],
        [InlineKeyboardButton("Показать ДЗ", callback_data="show_dz")]
    ]
    await update.message.reply_text(
        "Привет! Я бот для ДЗ.\nКоманды доступны через кнопки.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def dz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    can_use, remaining = cooldown_check(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID and not can_use:
        await update.message.reply_text(
            f"⏳ Подождите {format_timedelta(remaining)} до следующего запроса ДЗ."
        )
        return
    if update.effective_user.id != ADMIN_ID:
        user_cd[update.effective_user.id] = datetime.now(TZ).isoformat()
        save_data()
    await update.message.reply_text(format_dz_for_display(), parse_mode="Markdown")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    await update.message.reply_text(format_history())

# ---------------- Conversation ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "add_dz" and query.from_user.id == ADMIN_ID:
        await query.message.reply_text("Отправьте ДЗ в формате:\nРусский язык - п 14, упр 85")
        return WAIT_ADD
    elif query.data == "remove_dz" and query.from_user.id == ADMIN_ID:
        await query.message.reply_text("Отправьте предмет для удаления всех ДЗ:")
        return WAIT_REMOVE
    elif query.data == "show_dz":
        await query.message.reply_text(format_dz_for_display(), parse_mode="Markdown")
        return ConversationHandler.END
    else:
        await query.message.reply_text("Только админ может редактировать ДЗ.")
        return ConversationHandler.END

async def add_dz_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    msg = update.message.text.strip()
    if "-" not in msg:
        await update.message.reply_text("Неверный формат. Используйте:\nПредмет - ДЗ")
        return WAIT_ADD
    subj, task = map(str.strip, msg.split("-",1))
    record = assign_one(subj, task)
    if record is None:
        await update.message.reply_text("Предмет не найден в расписании.")
        return WAIT_ADD
    dz_list.append(record)
    save_data()
    await update.message.reply_text(f"✅ ДЗ сохранено для {normalize_subject(subj)}.")
    return ConversationHandler.END

async def remove_dz_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    subj = normalize_subject(update.message.text.strip())
    removed = [r for r in dz_list if normalize_subject(r["subject"]) == subj]
    if not removed:
        await update.message.reply_text("Такого предмета нет в текущих ДЗ.")
        return WAIT_REMOVE
    for r in removed:
        dz_history.append({**r, "removed_at": datetime.now(TZ).isoformat(), "reason": "manual"})
    dz_list[:] = [r for r in dz_list if normalize_subject(r["subject"]) != subj]
    save_data()
    await update.message.reply_text(f"✅ Все ДЗ по {subj} удалены.")
    return ConversationHandler.END

# ---------------- Main ----------------
def main():
    load_data()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_callback)],
        states={
            WAIT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_dz_msg)],
            WAIT_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_dz_msg)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dz", dz_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(conv_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
