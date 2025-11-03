# bot.py
"""
Telegram Homeworks Bot with GitHub Gist persistence.

Requirements:
  pip install python-telegram-bot==20.4 aiohttp

Env vars required:
  BOT_TOKEN
  GITHUB_TOKEN
  GIST_ID
Optional:
  ADMIN_ID (default 6193109213)
  TZ (timezone, default "Europe/Riga")
"""

import os
import re
import json
import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    ContextTypes,
    filters,
)

# ---------------- config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6193109213"))
TZ_NAME = os.getenv("TZ", "Europe/Riga")
TZ = ZoneInfo(TZ_NAME)
COOLDOWN_HOURS = int(os.getenv("COOLDOWN_HOURS", "4"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN required")
if not GITHUB_TOKEN:
    raise RuntimeError("GITHUB_TOKEN required")
if not GIST_ID:
    raise RuntimeError("GIST_ID required")

# local storage paths (on Railway they will be ephemeral, but backups help)
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DZ_LOCAL = DATA_DIR / "dz.json"
HISTORY_LOCAL = DATA_DIR / "history.json"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("homework-bot")

# ---------------- schedule and constants ----------------
# durations for finding lesson end time (minutes)
LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40  # between 3 and 4th lesson
FIRST_LESSON_START = 8 * 60  # minutes from 00:00 -> 08:00

# Schedule: day keys are 'пн','вт','ср','чт','пт'
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Труд", "Труд", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский язык", "Алгебра", "Информатика", "Биология", "Английский язык", "Труд"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литература"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский язык", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский язык", "История", "Геометрия"],
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

# subject normalization
SUBJECT_ALIAS = {
    "русский": "Русский язык",
    "русский язык": "Русский язык",
    "английский": "Английский язык",
    "английский язык": "Английский язык",
    "англ": "Английский язык",
    "технология": "Труд",
    "труд": "Труд",
    "литра": "Литература",
    "литература": "Литература",
    "физра": "Физра",
    "информатика": "Информатика",
    "алгебра": "Алгебра",
    "геометрия": "Геометрия",
    "биология": "Биология",
    "география": "География",
    "изо": "Изо",
    "музыка": "Музыка",
    "история": "История",
}

# emoji map best-effort
EMOJI_MAP = {
    "русский": "📘",
    "английский": "🇬🇧",
    "алгебра": "🧮",
    "геометрия": "📐",
    "физика": "⚙️",
    "биология": "🌿",
    "история": "📜",
    "литература": "📖",
    "музыка": "🎵",
    "труд": "🛠️",
    "физра": "🏃",
    "география": "🗺️",
    "изо": "🎨",
    "информатика": "💻",
}

# in-memory data
dz_list: List[Dict[str, Any]] = []     # each record: subject, task, day (pн..пт), lesson_index, assigned_date ISO, end_iso
dz_history: List[Dict[str, Any]] = []  # removed records with removed_at, reason
user_cd: Dict[str, str] = {}           # user_id -> iso last /dz
ras_cd: Dict[str, str] = {}            # cooldown for /ras
last_subject_for_admin: Optional[str] = None  # last used subject by admin for quick entries

# regex helpers
TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
DAY_WORDS = set(["пн", "вт", "ср", "чт", "пт", "понедельник", "вторник", "среда", "четверг", "пятница"])

# ---------------- persistence helpers ----------------
async def load_from_gist():
    """
    Load dz.json and history.json from gist. If not found or network error -> load local files if exist.
    """
    global dz_list, dz_history
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    files = data.get("files", {})
                    dz_content = files.get("dz.json", {}).get("content", "{}")
                    hist_content = files.get("history.json", {}).get("content", "[]")
                    try:
                        dz_list = json.loads(dz_content) if dz_content else []
                    except Exception:
                        dz_list = []
                        logger.exception("Failed to parse dz.json from gist")
                    try:
                        dz_history = json.loads(hist_content) if hist_content else []
                    except Exception:
                        dz_history = []
                        logger.exception("Failed to parse history.json from gist")
                    logger.info("Loaded data from Gist")
                    # write local copies
                    _save_local()
                    return
                else:
                    logger.warning("Gist GET returned %s", resp.status)
    except Exception:
        logger.exception("Error loading from gist")
    # fallback: load local
    _load_local()

async def save_to_gist():
    """
    Save current dz_list and dz_history into gist. On failure, write local backups.
    """
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "files": {
            "dz.json": {"content": json.dumps(dz_list, ensure_ascii=False, indent=2)},
            "history.json": {"content": json.dumps(dz_history, ensure_ascii=False, indent=2)},
        }
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=headers, json=payload, timeout=20) as resp:
                if 200 <= resp.status < 300:
                    logger.info("Saved data to gist successfully")
                    _save_local()  # update local copy too
                    return True
                else:
                    text = await resp.text()
                    logger.warning("Failed to save to gist: %s %s", resp.status, text[:200])
    except Exception:
        logger.exception("Exception while saving to gist")
    # fallback: save local as backup
    _save_local(backup=True)
    return False

def _load_local():
    global dz_list, dz_history
    try:
        if DZ_LOCAL.exists():
            with DZ_LOCAL.open("r", encoding="utf-8") as f:
                dz_list = json.load(f)
        else:
            dz_list = []
    except Exception:
        dz_list = []
        logger.exception("Failed to load local dz.json")
    try:
        if HISTORY_LOCAL.exists():
            with HISTORY_LOCAL.open("r", encoding="utf-8") as f:
                dz_history = json.load(f)
        else:
            dz_history = []
    except Exception:
        dz_history = []
        logger.exception("Failed to load local history.json")

def _save_local(backup: bool = False):
    """
    Save local files. If backup True, create timestamped backup.
    """
    try:
        with DZ_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(dz_list, f, ensure_ascii=False, indent=2)
        with HISTORY_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(dz_history, f, ensure_ascii=False, indent=2)
        if backup:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with (BACKUP_DIR / f"dz_{ts}.json").open("w", encoding="utf-8") as f:
                json.dump(dz_list, f, ensure_ascii=False, indent=2)
            with (BACKUP_DIR / f"history_{ts}.json").open("w", encoding="utf-8") as f:
                json.dump(dz_history, f, ensure_ascii=False, indent=2)
            # keep only last N backups
            files = sorted(BACKUP_DIR.glob("dz_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[BACKUP_COUNT:]:
                try:
                    old.unlink()
                except Exception:
                    pass
    except Exception:
        logger.exception("Failed to save local files")

# ---------------- time & schedule helpers ----------------
def normalize_subject(name: str) -> str:
    if not name:
        return name
    key = name.strip().lower()
    return SUBJECT_ALIAS.get(key, name.strip().capitalize())

def lesson_start_end_for_date(d: date, idx: int):
    """
    idx is 0-based lesson index (0..6)
    returns (start_datetime, end_datetime) in TZ
    """
    minutes = FIRST_LESSON_START
    for i in range(idx):
        minutes += LESSON_DURATION
        minutes += LONG_BREAK if i == 2 else SHORT_BREAK
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(minutes=minutes)
    end = start + timedelta(minutes=LESSON_DURATION)
    return start, end

def next_date_for_day_key(day_key: str, from_dt: Optional[datetime] = None) -> date:
    if from_dt is None:
        from_dt = datetime.now(TZ)
    today = from_dt.date()
    cur_wd = from_dt.weekday()  # Mon=0
    # our DAYS_ORDER index: 0..4
    target = DAYS_ORDER.index(day_key)
    # map cur_wd to 0..4: if weekend, treat as next Monday?
    # We'll compute delta modulo 7
    delta_days = (target - cur_wd) % 7
    candidate = today + timedelta(days=delta_days)
    return candidate

def find_subject_positions_exact(subject_name: str):
    """
    Return list of (day_key, lesson_idx) where normalized subject equals schedule item.
    """
    res = []
    norm = normalize_subject(subject_name).lower()
    for day_key, lessons in SCHEDULE.items():
        for idx, lesson in enumerate(lessons):
            if norm == normalize_subject(lesson).lower():
                res.append((day_key, idx))
    return res

# ---------------- core logic: assign / add / remove / expire ----------------
def assign_one(subject: str, task: str):
    """
    Create assignment record for subject and task:
    - find nearest lesson occurrence
    - return record or None if subject not found
    """
    now = datetime.now(TZ)
    positions = find_subject_positions_exact(subject)
    if not positions:
        return None
    candidates = []
    for day_key, idx in positions:
        candidate_date = next_date_for_day_key(day_key, now)
        start, end = lesson_start_end_for_date(candidate_date, idx)
        if day_key == DAYS_ORDER[now.weekday()] and now >= end:
            # if today and lesson already ended -> use next week
            candidate_date = candidate_date + timedelta(days=7)
            start, end = lesson_start_end_for_date(candidate_date, idx)
        candidates.append((end, day_key, idx, candidate_date))
    candidates.sort(key=lambda x: x[0])
    end_dt, day_key, idx, assigned_date = candidates[0]
    record = {
        "subject": normalize_subject(subject),
        "task": task.strip(),
        "day": day_key,
        "lesson_index": idx,
        "assigned_date": assigned_date.isoformat(),
        "end_iso": end_dt.isoformat()
    }
    return record

def remove_expired(auto_save: bool = True):
    """
    Move expired records to history with reason 'auto', save
    """
    global dz_list, dz_history
    now = datetime.now(TZ)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= now]
    if removed:
        for r in removed:
            dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
        dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]
        _save_local()
        # try save to gist asynchronously: caller will handle awaiting save_to_gist
        return True
    return False

# ---------------- formatting ----------------
def emoji_for_subject(subject: str) -> str:
    k = subject.lower()
    for key, em in EMOJI_MAP.items():
        if key in k:
            return em
    return "📚"

def format_timedelta_short(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s <= 0:
        return "0м"
    h = s // 3600
    m = (s % 3600) // 60
    if h > 0:
        return f"{h}ч {m}м" if m > 0 else f"{h}ч"
    return f"{m}м"

def format_dz_for_display_text():
    """
    Build pretty text for /dz
    """
    remove_expired(auto_save=False)
    if not dz_list:
        return "🗒 Домашек нет — всё чисто."
    # group by day
    grouped = {}
    for r in dz_list:
        grouped.setdefault(r["day"], []).append(r)
    lines = ["📚 *ДОМАШНИЕ ЗАДАНИЯ*", ""]
    for day in DAYS_ORDER:
        if day not in grouped:
            continue
        lines.append(f"🗓 *{day.upper()}*")
        lessons = sorted(grouped[day], key=lambda x: x["lesson_index"])
        for r in lessons:
            subj = r["subject"]
            task = r["task"]
            emoji = emoji_for_subject(subj)
            lines.append(f"▫️ *{subj}* {emoji}")
            # split task to multiple lines if long
            for t in str(task).split("\n"):
                if t.strip():
                    lines.append(f"> {t.strip()}")
            # optional: show end time
            # end_str = datetime.fromisoformat(r['end_iso']).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            # lines.append(f"_кончится: {end_str}_")
            lines.append("")  # blank between lessons
        lines.append("─ ─ ─")
    # remove last separator
    if lines and lines[-1] == "─ ─ ─":
        lines.pop()
    return "\n".join(lines)

def format_schedule_text():
    lines = ["📘 *РАСПИСАНИЕ НА НЕДЕЛЮ*", ""]
    for day in DAYS_ORDER:
        lines.append(f"🗓 *{day.upper()}*")
        lessons = SCHEDULE.get(day, [])
        for i, subj in enumerate(lessons, start=1):
            lines.append(f"{i}. {subj}")
        lines.append("─ ─ ─")
    if lines and lines[-1] == "─ ─ ─":
        lines.pop()
    return "\n".join(lines)

# ---------------- parsing admin input ----------------
def strip_day_time_prefix(line: str) -> str:
    """
    Remove leading day words or times like 'Пн 08:30' from a line.
    """
    s = line.strip()
    # remove leading time
    s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', "", s)
    # remove day tokens
    tokens = s.split()
    if tokens:
        first = tokens[0].lower().strip(":,")
        if first in DAY_WORDS:
            s = " ".join(tokens[1:])
    # remove time again if any
    s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', "", s)
    return s.strip()

async def process_add_lines(lines: List[str]):
    """
    lines: list of strings in format 'Subject - task' OR lines continuing previous task.
    This function returns list of added subjects (for messages), and saves to dz_list & history.
    """
    global last_subject_for_admin
    added = []
    warnings = []
    for raw in lines:
        s = strip_day_time_prefix(raw)
        if not s:
            continue
        # if no '-' but we have last_subject_for_admin -> treat as continuation of last task
        if "-" not in s:
            if last_subject_for_admin and added:
                # append to last added item's task
                last_rec = added[-1]
                # find last record in dz_list for that subject assigned_date
                subj = last_subject_for_admin
                # append continuation to last dz_list entry with that subject
                # find most recent assignment for subject
                candidates = [r for r in dz_list if normalize_subject(r["subject"]).lower() == normalize_subject(subj).lower()]
                if candidates:
                    candidates = sorted(candidates, key=lambda x: x.get("assigned_date",""), reverse=True)
                    candidates[0]["task"] = candidates[0]["task"] + " " + s
                else:
                    warnings.append(f"Нет предыдущего предмета для добавления продолжения: '{s}'")
            else:
                warnings.append(f"Строка пропущена (нет '-' и нет last_subject): {raw}")
            continue
        subj_raw, task_raw = map(str.strip, s.split("-", 1))
        subj_norm = normalize_subject(subj_raw)
        last_subject_for_admin = subj_norm
        rec = assign_one(subj_norm, task_raw)
        if rec is None:
            warnings.append(f"Предмет '{subj_raw}' не найден в расписании — пропущено.")
            continue
        # check duplicates on same assigned_date & subject
        exists = next((r for r in dz_list if normalize_subject(r["subject"]).lower() == normalize_subject(rec["subject"]).lower() and r["assigned_date"] == rec["assigned_date"]), None)
        if exists:
            if exists["task"].strip() == rec["task"].strip():
                # exact duplicate -> skip
                continue
            else:
                warnings.append(f"По предмету {rec['subject']} уже есть другое ДЗ на {rec['assigned_date']}.")
                continue
        dz_list.append(rec)
        dz_history.append({**rec, "added_at": datetime.now(TZ).isoformat(), "reason": "added"})
        added.append(f"{rec['subject']} ({rec['assigned_date']})")
    # save local & gist
    _save_local()
    await save_to_gist()
    return added, warnings

# ---------------- Command handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для ДЗ.\nКоманды: /add_dz (админ), /dz, /ras, /clear (админ), /remove_dz <предмет> (админ), /history, /find <предмет>, /short"
    )

async def cmd_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # cooldown per user for /dz except admin
    uid = str(update.effective_user.id)
    now = datetime.now(TZ)
    if update.effective_user.id != ADMIN_ID:
        last = user_cd.get(uid)
        if last:
            last_dt = datetime.fromisoformat(last)
            rem = timedelta(hours=COOLDOWN_HOURS) - (now - last_dt)
            if rem.total_seconds() > 0:
                await update.message.reply_text(f"⏳ У вас кд на /dz: {format_timedelta_short(rem)}")
                return
        user_cd[uid] = now.isoformat()
        _save_local()
    # remove expired and save if any removed
    if remove_expired():
        # try to persist expired removals
        await save_to_gist()
    text = format_dz_for_display_text()
    await update.message.reply_markdown(text)

async def cmd_ras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    now = datetime.now(TZ)
    last = ras_cd.get(uid)
    if last:
        last_dt = datetime.fromisoformat(last)
        rem = timedelta(hours=COOLDOWN_HOURS) - (now - last_dt)
        if rem.total_seconds() > 0:
            await update.message.reply_text(f"⏳ У вас кд на /ras: {format_timedelta_short(rem)}")
            return
    ras_cd[uid] = now.isoformat()
    _save_local()
    text = format_schedule_text()
    await update.message.reply_markdown(text)

async def cmd_add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может добавлять ДЗ.")
        return
    # text after command: may be multiline in update.message.text
    full = update.message.text or ""
    lines = []
    if "\n" in full:
        lines = full.splitlines()[1:]  # skip command line
    else:
        # single line: args used
        args_text = " ".join(context.args) if context.args else ""
        if not args_text:
            await update.message.reply_text("Использование: /add_dz <Subject - task> или отправь многострочный сообщение.")
            return
        lines = [args_text]
    added, warnings = await process_add_lines(lines)
    reply_parts = []
    if added:
        reply_parts.append("✅ Добавлено:\n" + "\n".join(added))
    if warnings:
        reply_parts.append("⚠️ Предупреждения:\n" + "\n".join(warnings))
    if not reply_parts:
        reply_parts = ["Ни одного задания не добавлено."]
    await update.message.reply_text("\n\n".join(reply_parts))

async def cmd_remove_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может удалять ДЗ.")
        return
    subj = " ".join(context.args).strip()
    if not subj:
        await update.message.reply_text("Использование: /remove_dz <предмет>")
        return
    subj_norm = normalize_subject(subj)
    load_before = len(dz_list)
    now_iso = datetime.now(TZ).isoformat()
    removed = []
    remaining = []
    for r in dz_list:
        if normalize_subject(r["subject"]).lower() == subj_norm.lower():
            dz_history.append({**r, "removed_at": now_iso, "reason": "manual_remove"})
            removed.append(r)
        else:
            remaining.append(r)
    dz_list[:] = remaining
    if removed:
        _save_local()
        await save_to_gist()
        await update.message.reply_text(f"✅ Удалено {len(removed)} ДЗ по {subj_norm}")
    else:
        await update.message.reply_text("Ничего не найдено для удаления.")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может очищать все ДЗ.")
        return
    if not dz_list:
        await update.message.reply_text("Список ДЗ уже пуст.")
        return
    now_iso = datetime.now(TZ).isoformat()
    for r in dz_list:
        dz_history.append({**r, "removed_at": now_iso, "reason": "manual_clear"})
    count = len(dz_list)
    dz_list.clear()
    _save_local()
    await save_to_gist()
    await update.message.reply_text(f"🧹 Очищено {count} ДЗ и сохранено в истории.")

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # load latest from gist to be sure
    await load_from_gist()
    if not dz_history:
        await update.message.reply_text("История удалённых ДЗ пуста.")
        return
    # show last 50 entries
    items = dz_history[-50:]
    lines = []
    for r in reversed(items):
        removed = r.get("removed_at", r.get("added_at", ""))[:16]
        subj = r.get("subject", "")
        task = r.get("task", r.get("task", r.get("hw","")))
        reason = r.get("reason", "")
        lines.append(f"{removed} | {subj} | {task} [{reason}]")
    # send in chunks
    chunk_size = 4000
    msg = "\n".join(lines)
    for i in range(0, len(msg), chunk_size):
        await update.message.reply_text(msg[i:i+chunk_size])

async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /find <предмет>")
        return
    subj = normalize_subject(" ".join(context.args))
    results = [r for r in dz_list if normalize_subject(r["subject"]).lower() == subj.lower()]
    if not results:
        await update.message.reply_text("ДЗ по этому предмету не найдены.")
        return
    lines = [f"▫️ *{r['subject']}* ({r['assigned_date']})\n> {r['task']}\n" for r in sorted(results, key=lambda x: x['assigned_date'])]
    await update.message.reply_markdown("\n".join(lines))

async def cmd_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not dz_list:
        await update.message.reply_text("Домашек нет.")
        return
    subjects = sorted({r["subject"] for r in dz_list})
    await update.message.reply_text("📚 Сегодня есть ДЗ по:\n" + ", ".join(subjects))

async def cmd_edit_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может редактировать.")
        return
    text = " ".join(context.args)
    if "-" not in text:
        await update.message.reply_text("Использование: /edit_dz <предмет> - <новое дз>")
        return
    subj_raw, task = map(str.strip, text.split("-",1))
    subj = normalize_subject(subj_raw)
    # find most recent assignment for subject
    candidates = [r for r in dz_list if normalize_subject(r["subject"]).lower() == subj.lower()]
    if not candidates:
        await update.message.reply_text("Такого предмета нет в текущих ДЗ.")
        return
    # edit the most recent by assigned_date
    candidates = sorted(candidates, key=lambda x: x["assigned_date"], reverse=True)
    rec = candidates[0]
    rec["task"] = task
    dz_history.append({**rec, "edited_at": datetime.now(TZ).isoformat(), "reason": "edited", "new_task": task})
    _save_local()
    await save_to_gist()
    await update.message.reply_text(f"✅ ДЗ по {subj} обновлено.")

# ---------------- message handler for free text ----------------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If admin sends plain text containing '-' treat as add lines.
    Otherwise ignore (non-admin cannot add).
    """
    text = update.message.text or ""
    if "-" not in text:
        return
    if update.effective_user.id != ADMIN_ID:
        # ignore non-admin plain additions
        return
    # support both multi-line and single-line block
    if text.startswith("/add_dz"):
        lines = text.splitlines()[1:]
    else:
        lines = text.splitlines()
    added, warnings = await process_add_lines(lines)
    resp = []
    if added:
        resp.append("✅ Добавлено:\n" + "\n".join(added))
    if warnings:
        resp.append("⚠️ Предупреждения:\n" + "\n".join(warnings))
    if not resp:
        resp = ["Ничего не добавлено."]
    await update.message.reply_text("\n\n".join(resp))

# ---------------- background task for periodic cleanup ----------------
async def periodic_cleanup_task(app):
    while True:
        try:
            changed = remove_expired(auto_save=False)
            if changed:
                # persist removals
                await save_to_gist()
            await asyncio.sleep(300)  # every 5 minutes
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in periodic cleanup")
            await asyncio.sleep(60)

# ---------------- startup ----------------
async def main():
    # load initial data
    await load_from_gist()
    # ensure local files updated
    _save_local()
    # cleanup expired at start
    if remove_expired(auto_save=False):
        await save_to_gist()
    # build app
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("dz", cmd_dz))
    app.add_handler(CommandHandler("ras", cmd_ras))
    app.add_handler(CommandHandler("add_dz", cmd_add_dz))
    app.add_handler(CommandHandler("remove_dz", cmd_remove_dz))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("short", cmd_short))
    app.add_handler(CommandHandler("edit_dz", cmd_edit_dz))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    # start periodic cleanup task
    task = asyncio.create_task(periodic_cleanup_task(app))
    logger.info("Bot started")
    try:
        await app.run_polling()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

if __name__ == "__main__":
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # если Railway уже запустил event loop — просто запускаем задачу
            loop.create_task(main())
        else:
            loop.run_until_complete(main())
    except RuntimeError:
        # fallback на случай, если луп не существует
        asyncio.run(main())

