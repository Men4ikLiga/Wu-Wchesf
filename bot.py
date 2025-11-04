# bot.py
import os
import re
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Установи переменную окружения BOT_TOKEN")

DATA_FILE = "dz.json"
HISTORY_FILE = "dz_history.json"
CD_FILE = "user_cd.json"     # cooldown для /dz
RAS_CD_FILE = "ras_cd.json"  # cooldown для /ras
TZ = ZoneInfo("Europe/Amsterdam")  # твоя текущая временная зона
ADMIN_ID = 6193109213
COOLDOWN_HOURS = 4

# ---------------- SCHEDULE & SUBJECTS ----------------
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Труд", "Труд", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский язык", "Алгебра", "Информатика", "Биология", "Английский язык", "Труд"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литература"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский язык", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский язык", "История", "Геометрия"],
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40
FIRST_LESSON_START_MIN = 8 * 60  # 08:00

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

EMOJI_MAP = {
    "русский": "📘",
    "английский": "🇬🇧",
    "матем": "🧮",
    "алгебра": "🧮",
    "геометр": "📐",
    "физика": "⚙️",
    "химия": "⚗️",
    "биология": "🌿",
    "история": "📜",
    "литература": "📖",
    "музыка": "🎵",
    "труд": "🛠️",
    "физра": "🏃",
    "география": "🗺️",
    "изо": "🎨",
    "информатика": "💻",
    "твис": "🧾",
    "ров": "🏫",
    "музыка": "🎼",
}

# ---------------- Storage ----------------
dz_list = []
dz_history = []
user_cd = {}
ras_cd = {}

# ---------------- Persistence ----------------
def load_json_if_exists(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def load_data():
    global dz_list, dz_history, user_cd, ras_cd
    dz_list = load_json_if_exists(DATA_FILE, [])
    dz_history = load_json_if_exists(HISTORY_FILE, [])
    user_cd = load_json_if_exists(CD_FILE, {})
    ras_cd = load_json_if_exists(RAS_CD_FILE, {})

def save_all():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dz_list, f, ensure_ascii=False, indent=2)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(dz_history, f, ensure_ascii=False, indent=2)
    with open(CD_FILE, "w", encoding="utf-8") as f:
        json.dump(user_cd, f, ensure_ascii=False, indent=2)
    with open(RAS_CD_FILE, "w", encoding="utf-8") as f:
        json.dump(ras_cd, f, ensure_ascii=False, indent=2)

# ---------------- Helpers ----------------
def normalize_subject(name: str):
    if not name:
        return name
    key = name.strip().lower()
    return SUBJECT_ALIAS.get(key, name.strip())

def weekday_name_from_date(d: date):
    wd = d.weekday()
    if wd >= 5:
        return None
    return DAYS_ORDER[wd]

def lesson_start_end(d: date, idx: int):
    minutes = FIRST_LESSON_START_MIN
    for i in range(idx):
        minutes += LESSON_DURATION
        minutes += LONG_BREAK if i == 2 else SHORT_BREAK
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(minutes=minutes)
    end = start + timedelta(minutes=LESSON_DURATION)
    return start, end

def find_subject_positions_exact(subject_name):
    res = []
    norm = normalize_subject(subject_name).lower()
    for day, lessons in SCHEDULE.items():
        for idx, lesson in enumerate(lessons):
            if norm == normalize_subject(lesson).lower():
                res.append((day, idx))
    return res

def next_date_for_day(day_key, from_dt=None):
    if from_dt is None:
        from_dt = datetime.now(TZ)
    today = from_dt.date()
    target = DAYS_ORDER.index(day_key)
    cur_wd = from_dt.weekday()
    delta = (target - cur_wd) % 7
    candidate = today + timedelta(days=delta)
    return candidate

def assign_one(subject, task):
    now = datetime.now(TZ)
    positions = find_subject_positions_exact(subject)
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
        "end_iso": end_dt.replace(tzinfo=TZ).isoformat(),
    }
    return record

def emoji_for_subject(subject: str):
    key = subject.lower()
    for k, em in EMOJI_MAP.items():
        if k in key:
            return em
    return "📚"

def format_time(dt_iso: str):
    try:
        dt = datetime.fromisoformat(dt_iso)
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_iso

# ---------------- Auto-cleanup ----------------
async def auto_cleanup(context: ContextTypes.DEFAULT_TYPE):
    load_data()
    now = datetime.now(TZ)
    msk_plus_1 = now.astimezone(ZoneInfo("Europe/Moscow")) + timedelta(hours=1)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= msk_plus_1]
    if not removed:
        return
    for r in removed:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
    dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > msk_plus_1]
    save_all()

    # формируем уведомление админу
    lines = ["🕒 Автоудаление ДЗ:"]
    for r in removed:
        subj = r["subject"]
        task = r["task"]
        day = r["day"].upper()
        lines.append(f"{day} | {subj}: {task}")
    msg = "\n".join(lines)
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=msg)
    except Exception as e:
        print("Не удалось отправить уведомление админу:", e)

# ---------------- Cooldown helpers ----------------
def cooldown_check_map(map_obj, user_id):
    now = datetime.now(TZ)
    if str(user_id) not in map_obj:
        return True, None
    last = datetime.fromisoformat(map_obj[str(user_id)])
    remaining = timedelta(hours=COOLDOWN_HOURS) - (now - last)
    if remaining.total_seconds() > 0:
        return False, remaining
    return True, None

def update_cd_map(map_obj, user_id):
    map_obj[str(user_id)] = datetime.now(TZ).isoformat()
    save_all()

def format_timedelta(td: timedelta):
    mins = int(td.total_seconds() // 60)
    if mins < 60:
        return f"{mins} мин"
    h, m = divmod(mins, 60)
    return f"{h} ч {m} мин"

# ---------------- Formatting output ----------------
def format_dz_for_display():
    if not dz_list:
        return "🗒 Домашек нет — всё чисто."
    grouped = {}
    for r in dz_list:
        grouped.setdefault(r["day"], []).append(r)
    text_lines = ["📚 ДОМАШНИЕ ЗАДАНИЯ", ""]
    for day in DAYS_ORDER:
        if day not in grouped:
            continue
        header = f"🗓 {day.upper()}"
        text_lines.append(header)
        lessons = sorted(grouped[day], key=lambda x: x["lesson_index"])
        for r in lessons:
            subj = r["subject"]
            task = r["task"]
            emoji = emoji_for_subject(subj)
            text_lines.append(f"▫️ **{subj}** {emoji}")
            for tline in task.split("\n"):
                tline = tline.strip()
                if not tline:
                    continue
                text_lines.append(f"> {tline}")
            text_lines.append("")
        text_lines.append("─ ─ ─")
    if text_lines and text_lines[-1] == "─ ─ ─":
        text_lines.pop()
    return "\n".join(text_lines)

def format_schedule_no_dz():
    lines = ["📅 РАСПИСАНИЕ НА НЕДЕЛЮ\n"]
    for day in DAYS_ORDER:
        lines.append(f"📆 {day.upper()}")
        lessons = SCHEDULE.get(day, [])
        for i, subj in enumerate(lessons, start=1):
            lines.append(f"{i}. {subj}")
        lines.append("─ ─ ─")
    if lines and lines[-1] == "─ ─ ─":
        lines.pop()
    return "\n".join(lines)

# ---------------- Command handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Привет! Я бот для домашки.\n\n"
        "Админ может просто отправлять сообщения с ДЗ в формате:\n"
        "Русский язык - п 14\n"
        "Физика - дорешать задачи\n\n"
        "Команды:\n"
        "/dz — показать все ДЗ\n"
        "/ras — расписание недели\n"
        "/add_dz <Предмет> - <ДЗ>\n"
        "/remove_dz <предмет>\n"
        "/clear — очистить все ДЗ\n"
        "/history — история удалений\n"
    )
    await update.message.reply_text(msg)

async def cmd_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    ok, rem = cooldown_check_map(user_cd, update.effective_user.id)
    if update.effective_user.id != ADMIN_ID and not ok:
        await update.message.reply_text(f"⏳ Подождите {format_timedelta(rem)} до следующего запроса /dz.")
        return
    if update.effective_user.id != ADMIN_ID:
        update_cd_map(user_cd, update.effective_user.id)
    text = format_dz_for_display()
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_ras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    ok, rem = cooldown_check_map(ras_cd, update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"⏳ Подождите {format_timedelta(rem)} до следующего запроса /ras.")
        return
    update_cd_map(ras_cd, update.effective_user.id)
    await update.message.reply_text(format_schedule_no_dz())

# ---------------- Startup ----------------
def main():
    load_data()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("dz", cmd_dz))
    app.add_handler(CommandHandler("ras", cmd_ras))

    # автопроверка и автоудаление
    job_queue = app.job_queue
    job_queue.run_repeating(auto_cleanup, interval=60, first=10)

    print("✅ Bot started with auto-cleanup enabled")
    app.run_polling()

if __name__ == "__main__":
    main()
