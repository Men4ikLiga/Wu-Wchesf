# bot.py
import os
import re
import json
import requests
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
CD_FILE = "user_cd.json"
RAS_CD_FILE = "ras_cd.json"
TZ = ZoneInfo("Europe/Amsterdam")
ADMIN_ID = 6193109213
COOLDOWN_HOURS = 4

# --- GitHub Gist настройки ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")  # ID Gist, где хранится dz_history.json
GIST_FILENAME = "dz_history.json"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}" if GIST_ID else None


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
FIRST_LESSON_START_MIN = 8 * 60

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
}

# ---------------- Storage ----------------
dz_list = []
dz_history = []
user_cd = {}
ras_cd = {}

# ---------------- Gist функции ----------------
def load_history_from_gist():
    global dz_history
    if not GIST_API_URL or not GITHUB_TOKEN:
        dz_history = []
        print("⚠️ GITHUB_TOKEN или GIST_ID не указаны — история не будет загружена.")
        return
    try:
        r = requests.get(GIST_API_URL, headers={"Authorization": f"token {GITHUB_TOKEN}"})
        if r.status_code == 200:
            gist_data = r.json()
            content = gist_data["files"].get(GIST_FILENAME, {}).get("content", "[]")
            dz_history = json.loads(content)
            print("✅ История загружена из Gist.")
        else:
            dz_history = []
            print(f"⚠️ Ошибка при загрузке Gist: {r.status_code}")
    except Exception as e:
        dz_history = []
        print(f"⚠️ Ошибка чтения Gist: {e}")


def save_history_to_gist():
    if not GIST_API_URL or not GITHUB_TOKEN:
        print("⚠️ GITHUB_TOKEN или GIST_ID не указаны — история не будет сохранена.")
        return
    try:
        data = {"files": {GIST_FILENAME: {"content": json.dumps(dz_history, ensure_ascii=False, indent=2)}}}
        r = requests.patch(GIST_API_URL, headers={"Authorization": f"token {GITHUB_TOKEN}"}, json=data)
        if r.status_code == 200:
            print("✅ История сохранена в Gist.")
        else:
            print(f"⚠️ Ошибка при сохранении Gist: {r.status_code}")
    except Exception as e:
        print(f"⚠️ Ошибка при записи Gist: {e}")


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
    global dz_list, user_cd, ras_cd
    dz_list = load_json_if_exists(DATA_FILE, [])
    user_cd = load_json_if_exists(CD_FILE, {})
    ras_cd = load_json_if_exists(RAS_CD_FILE, {})
    load_history_from_gist()


def save_all():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dz_list, f, ensure_ascii=False, indent=2)
    with open(CD_FILE, "w", encoding="utf-8") as f:
        json.dump(user_cd, f, ensure_ascii=False, indent=2)
    with open(RAS_CD_FILE, "w", encoding="utf-8") as f:
        json.dump(ras_cd, f, ensure_ascii=False, indent=2)
    save_history_to_gist()


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
    return today + timedelta(days=delta)


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
    return {
        "subject": normalize_subject(subject),
        "task": task,
        "day": day_key,
        "lesson_index": idx,
        "assigned_date": assigned_date.isoformat(),
        "end_iso": end_dt.replace(tzinfo=TZ).isoformat(),
    }


def remove_expired():
    now = datetime.now(TZ)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= now]
    for r in removed:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
    dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]
    if removed:
        save_all()


def emoji_for_subject(subject: str):
    key = subject.lower()
    for k, em in EMOJI_MAP.items():
        if k in key:
            return em
    return "📚"


def format_time(dt_iso: str):
    try:
        dt = datetime.fromisoformat(dt_iso)
        return dt.astimezone(TZ).strftime("%d.%m %H:%M")
    except Exception:
        return dt_iso


# ---------------- Telegram Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Отправь домашку в формате: `Предмет: задание`")


async def cmd_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_expired()
    if not dz_list:
        await update.message.reply_text("✅ Домашних заданий нет.")
        return
    lines = []
    for r in dz_list:
        emoji = emoji_for_subject(r["subject"])
        lines.append(f"{emoji} <b>{r['subject']}</b> — {r['task']}  <i>({r['day']} до {format_time(r['end_iso'])})</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Только админ может очищать.")
        return
    dz_history.extend(dz_list)
    dz_list.clear()
    save_all()
    await update.message.reply_text("🧹 Всё очищено.")


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not dz_history:
        await update.message.reply_text("История пуста.")
        return
    text = "\n".join(f"{emoji_for_subject(r['subject'])} {r['subject']} — {r['task']}" for r in dz_history[-30:])
    await update.message.reply_text(text)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = f"📘 В истории {len(dz_history)} записей."
    await update.message.reply_text(text)


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /del <название предмета>")
        return
    subj = normalize_subject(" ".join(args))
    before = len(dz_list)
    dz_list[:] = [r for r in dz_list if r["subject"].lower() != subj.lower()]
    if len(dz_list) != before:
        await update.message.reply_text(f"✅ Удалено дз по {subj}")
        save_all()
    else:
        await update.message.reply_text("❌ Ничего не найдено.")


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if ":" not in text:
        return
    subject, task = text.split(":", 1)
    record = assign_one(subject.strip(), task.strip())
    if record:
        dz_list.append(record)
        save_all()
        await update.message.reply_text(f"✅ Добавлено: {emoji_for_subject(subject)} {subject.strip()}")
    else:
        await update.message.reply_text("❌ Не удалось определить предмет.")


# ---------------- MAIN ----------------
def main():
    load_data()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("dz", cmd_dz))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("del", cmd_del))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    print("✅ Бот запущен.")
    app.run_polling()


if __name__ == "__main__":
    main()
