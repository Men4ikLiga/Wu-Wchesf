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
HISTORY_FILE = "dz_history.json"  # теперь хранится в Gist, локальный файл не используется как основной
CD_FILE = "user_cd.json"     # cooldown для /dz
RAS_CD_FILE = "ras_cd.json"  # cooldown для /ras
TZ = ZoneInfo("Europe/Amsterdam")
ADMIN_ID = 6193109213
COOLDOWN_HOURS = 4

# --- GitHub Gist настройки ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")  # ID Gist, в котором лежит dz_history.json
GIST_FILENAME = "dz_history.json"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}" if GIST_ID else None

# ---------------- SCHEDULE & SUBJECTS ----------------
# Исправления: Технология -> Труд, Литра -> Литература
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Труд", "Труд", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский язык", "Алгебра", "Информатика", "Биология", "Английский язык", "Труд"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литература"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский язык", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский язык", "История", "Геометрия"],
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

# длительность урока и перемен
LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40   # между 3 и 4 уроком
FIRST_LESSON_START_MIN = 8 * 60  # 08:00 в минутах от 00:00

# subject aliases (нормализация)
SUBJECT_ALIAS = {
    "русский": "Русский язык",
    "русский язык": "Русский язык",
    "английский": "Английский язык",
    "английский язык": "Английский язык",
    "технология": "Труд",
    "труд": "Труд",
    "литра": "Литература",
    "литература": "Литература",
    # можно дополнять
}

# emoji per subject base word (used in /dz)
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
    "биология": "🌿",
    "твис": "🧾",
    "ров": "🏫",
    "музыка": "🎼",
}

# ---------------- Storage ----------------
dz_list = []       # active assignments
dz_history = []    # removed assignments
user_cd = {}       # cooldown map for /dz -> {user_id: iso}
ras_cd = {}        # cooldown map for /ras -> {user_id: iso}


# ---------------- Gist функции ----------------
def _gist_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}


def load_history_from_gist():
    """Загружает dz_history.json из GitHub Gist."""
    global dz_history
    if not GIST_API_URL or not GITHUB_TOKEN:
        print("⚠️ GITHUB_TOKEN или GIST_ID не указаны — история не будет загружена.")
        dz_history = []
        return
    try:
        r = requests.get(GIST_API_URL, headers=_gist_headers(), timeout=10)
        if r.status_code == 200:
            gist_data = r.json()
            content = gist_data.get("files", {}).get(GIST_FILENAME, {}).get("content", "[]")
            dz_history = json.loads(content)
            print("✅ История загружена из Gist.")
        else:
            print(f"⚠️ Ошибка при загрузке Gist: {r.status_code} - {r.text[:200]}")
            dz_history = []
    except Exception as e:
        print(f"⚠️ Ошибка чтения Gist: {e}")
        dz_history = []


def save_history_to_gist():
    """Сохраняет dz_history.json в GitHub Gist."""
    if not GIST_API_URL or not GITHUB_TOKEN:
        print("⚠️ GITHUB_TOKEN или GIST_ID не указаны — история не будет сохранена.")
        return
    try:
        data = {
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(dz_history, ensure_ascii=False, indent=2)
                }
            }
        }
        r = requests.patch(
            GIST_API_URL,
            headers=_gist_headers(),
            json=data,
            timeout=10,
        )
        if r.status_code == 200:
            print("✅ История сохранена в Gist.")
        else:
            print(f"⚠️ Ошибка при сохранении Gist: {r.status_code} - {r.text[:200]}")
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
    global dz_list, dz_history, user_cd, ras_cd
    dz_list = load_json_if_exists(DATA_FILE, [])
    dz_history = []  # will be loaded from gist
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
    wd = d.weekday()  # 0 Mon ... 6 Sun
    if wd >= 5:
        return None
    return DAYS_ORDER[wd]


def lesson_start_end(d: date, idx: int):
    # idx 0..6
    minutes = FIRST_LESSON_START_MIN
    for i in range(idx):
        minutes += LESSON_DURATION
        minutes += LONG_BREAK if i == 2 else SHORT_BREAK
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(minutes=minutes)
    end = start + timedelta(minutes=LESSON_DURATION)
    return start, end


def find_subject_positions_exact(subject_name):
    """
    Возвращает список (day_key, lesson_index) где предмет точно совпадает после нормализации.
    """
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
    """
    Находит ближайший урок (по дате/времени) для subject и возвращает запись:
    {
        subject, task, day, lesson_index, assigned_date (YYYY-MM-DD), end_iso (ISO TZ)
    }
    """
    now = datetime.now(TZ)
    positions = find_subject_positions_exact(subject)
    if not positions:
        return None
    candidates = []
    for day_key, idx in positions:
        candidate_date = next_date_for_day(day_key, now)
        _, end_dt = lesson_start_end(candidate_date, idx)
        # если тот же день и урок уже прошёл — берём через 7 дней
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


def remove_expired():
    """
    Переносим авто-удаленные в историю.
    """
    now = datetime.now(TZ)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= now]
    for r in removed:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
    # keep only not expired
    remaining = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]
    changed = len(remaining) != len(dz_list)
    if changed:
        dz_list[:] = remaining
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
        return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_iso


def parse_lines_remove_day_time(lines):
    """
    Принимаем список строк. Возвращаем отфильтрованный список,
    где удалены явные метки дней недели и времени в начале строки.
    Например:
      'Пн 08:30 Русский - п 14' -> 'Русский - п 14'
    """
    out = []
    day_names = ["пн", "пн.", "понедельник", "вт", "вт.", "вторник",
                 "ср", "ср.", "среда", "чт", "чт.", "четверг",
                 "пт", "пт.", "пятница", "суббота", "вс", "вс."]
    time_pattern = re.compile(r"^\s*(\d{1,2}[:.]\d{2})")
    day_pattern = re.compile(r"^\s*([A-Za-zА-Яа-яёЁ\.\-]+)\s*", re.UNICODE)
    for line in lines:
        original = line.strip()
        if not original:
            continue
        # удалим в начале "Вт:" или "Вт" и времена "08:30"
        s = original
        # remove time at start
        s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', "", s)
        # remove day names at start (case-insensitive)
        first_word = s.split()[0].lower() if s.split() else ""
        if first_word.rstrip(":").lower() in day_names:
            s = " ".join(s.split()[1:])  # drop first token
        # if after trimming there is still time token at start, drop it
        s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', "", s)
        out.append(s.strip())
    return out


# ---------------- Cooldown helpers ----------------
def cooldown_check_map(map_obj, user_id):
    now = datetime.now(TZ)
    if str(user_id) not in map_obj:
        return True, None
    last = None
    try:
        last = datetime.fromisoformat(map_obj[str(user_id)])
    except Exception:
        return True, None
    remaining = timedelta(hours=COOLDOWN_HOURS) - (now - last)
    if remaining.total_seconds() > 0:
        return False, remaining
    return True, None


def update_cd_map(map_obj, user_id):
    map_obj[str(user_id)] = datetime.now(TZ).isoformat()
    save_all()


def format_timedelta(td: timedelta):
    # Выводит в человекочитаемом виде (Hч Mм)
    secs = int(td.total_seconds())
    if secs <= 0:
        return "0s"
    parts = []
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    minutes, seconds = divmod(secs, 60)
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    if seconds and not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


# ---------------- Formatting output ----------------
def format_dz_for_display():
    remove_expired()
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
        # sort by lesson index
        lessons = sorted(grouped[day], key=lambda x: x["lesson_index"])
        for r in lessons:
            subj = r["subject"]
            task = r["task"]
            emoji = emoji_for_subject(subj)
            # one block per lesson
            text_lines.append(f"▫️ **{subj}** {emoji}")
            # quoted line(s) — if multi-line, keep indentation
            for tline in task.split("\n"):
                tline = tline.strip()
                if not tline:
                    continue
                text_lines.append(f"> {tline}")
            text_lines.append("")  # blank between lessons
        text_lines.append("─ ─ ─")
    # remove trailing separator
    if text_lines and text_lines[-1] == "─ ─ ─":
        text_lines.pop()
    return "\n".join(text_lines)


def format_schedule_no_dz():
    """
    Форматирование расписания на неделю без дз (для /ras).
    Показываем только предметы по урокам, пронумерованные 1..7.
    """
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


# ---------------- Message processing (plain text adds) ----------------
async def process_plain_text_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Админ может просто отправлять текст с несколькими строками:
    'Русский - п14\nФизика - упр 5'
    Бот обработает каждую строку.
    """
    if update.effective_user.id != ADMIN_ID:
        # we don't auto-add from non-admins
        return

    text = update.message.text or ""
    # split into lines and remove leading /add_dz if present
    # support both: sending message starting with /add_dz and then lines, and direct multi-line
    if text.startswith("/add_dz"):
        # remove first line
        lines = text.splitlines()[1:]
    else:
        lines = text.splitlines()
    # clean lines: remove day/time tokens at start
    lines = parse_lines_remove_day_time(lines)
    if not lines:
        await update.message.reply_text("Не нашёл строк с форматом 'Предмет - ДЗ'.")
        return

    load_data()
    added = []
    skipped_same = []
    warnings = []
    for line in lines:
        if "-" not in line:
            continue
        subj_raw, task_raw = map(str.strip, line.split("-", 1))
        subj_norm = normalize_subject(subj_raw)
        task = " ".join(task_raw.split())  # normalize spaces, keep text
        # attempt to assign
        record = assign_one(subj_norm, task)
        if record is None:
            warnings.append(f"Не найден предмет в расписании: '{subj_raw}'")
            continue
        # check if already exists for same assigned_date
        exists_same = None
        exists_diff = None
        for r in dz_list:
            if normalize_subject(r["subject"]).lower() == subj_norm.lower():
                # if assigned_date matches candidate
                if r["assigned_date"] == record["assigned_date"]:
                    if r["task"].strip() == task.strip():
                        exists_same = r
                    else:
                        exists_diff = r
        if exists_same:
            skipped_same.append(subj_norm)
            continue
        if exists_diff:
            # don't overwrite automatically. notify admin
            warnings.append(
                f"⚠️ По предмету '{subj_norm}' уже есть другое ДЗ на ближайший урок ({exists_diff['assigned_date']}).\n"
                f"Существующее: {exists_diff['task']}\nОтправленное: {task}"
            )
            continue
        # add
        dz_list.append(record)
        added.append(f"{subj_norm} ({record['assigned_date']})")
    if added:
        save_all()
    # prepare response
    parts = []
    if added:
        parts.append("✅ Добавлено:\n" + "\n".join(added))
    if skipped_same:
        parts.append("ℹ️ Пропущено (уже есть, совпадает):\n" + ", ".join(skipped_same))
    if warnings:
        parts.append("\n".join(warnings))
    reply = "\n\n".join(parts) if parts else "Ничего не добавлено."
    await update.message.reply_text(reply)


# ---------------- Command handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Привет! Я бот для домашки класса 7.1\n\n"
        "Создатель и непосредвенный администратор -  @uwu4950"
        "По всем вопросам пишите. доступные команнды:
        "/dz (посмотреть задания)"
        "/ras (рассписание)"
    )
    зawait update.message.reply_text(msg)


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


async def cmd_add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin-only convenience: single-line
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может добавлять ДЗ.")
        return
    # rest of text after command
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Использование: /add_dz Предмет - ДЗ")
        return
    # use same parsing as plain text (single-line)
    lines = [text]
    lines = parse_lines_remove_day_time(lines)
    # reuse processing logic
    update.message.text = "\n".join(lines)
    await process_plain_text_add(update, context)


async def cmd_remove_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может удалять ДЗ.")
        return
    subj = " ".join(context.args).strip()
    if not subj:
        await update.message.reply_text("Использование: /remove_dz <предмет>")
        return
    subj_norm = normalize_subject(subj)
    load_data()
    removed = [r for r in dz_list if normalize_subject(r["subject"]).lower() == subj_norm.lower()]
    if not removed:
        await update.message.reply_text("Такого предмета нет в текущих ДЗ.")
        return
    now = datetime.now(TZ)
    for r in removed:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "manual"})
    dz_list[:] = [r for r in dz_list if normalize_subject(r["subject"]).lower() != subj_norm.lower()]
    save_all()
    await update.message.reply_text(f"✅ Удалено {len(removed)} ДЗ по предмету {subj_norm}.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может очищать все ДЗ.")
        return
    load_data()
    if not dz_list:
        await update.message.reply_text("Список ДЗ уже пуст.")
        return
    now = datetime.now(TZ)
    for r in dz_list:
        dz_history.append({**r, "removed_at": now.isoformat(), "reason": "manual_clear"})
    count = len(dz_list)
    dz_list.clear()
    save_all()
    await update.message.reply_text(f"🧹 Очищено {count} ДЗ и сохранено в истории.")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    if not dz_history:
        await update.message.reply_text("Истории удалённых ДЗ пока нет.")
        return
    # show last 50 entries
    items = dz_history[-50:]
    lines = []
    for r in reversed(items):
        removed_at = r.get("removed_at", "")[:16]
        subj = r.get("subject", "")
        task = r.get("task", "")
        reason = r.get("reason", "")
        lines.append(f"{removed_at} | {subj} | {task} [{reason}]")
    # send in chunks if too long
    chunk_size = 4000
    msg = "\n".join(lines)
    for i in range(0, len(msg), chunk_size):
        await update.message.reply_text(msg[i : i + chunk_size])


# ---------------- Message handler for free text ----------------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Если админ присылает текст с '-' — обрабатываем как добавление.
    В остальных случаях — игнорируем (пользователи не могут добавлять).
    """
    txt = update.message.text or ""
    # quick guard: if message contains '-' treat as potential dz
    if "-" not in txt:
        return
    # admin only
    if update.effective_user.id != ADMIN_ID:
        # ignore messages from non-admin that look like dz
        return
    await process_plain_text_add(update, context)


# ---------------- Startup ----------------
def main():
    load_data()
    # ensure tz usage by converting stored iso strings? handled on access
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("dz", cmd_dz))
    app.add_handler(CommandHandler("ras", cmd_ras))
    app.add_handler(CommandHandler("add_dz", cmd_add_dz))
    app.add_handler(CommandHandler("remove_dz", cmd_remove_dz))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("history", cmd_history))

    # message handler: plain text (admin adding)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    print("✅ Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
