#!/usr/bin/env python3
# coding: utf-8
"""
Полнофункциональный Telegram бот для ДЗ с сохранением в GitHub Gist.
Поддерживает:
 - /add_dz      (админ) - многострочное или однострочное добавление ДЗ
 - Plain text   (админ) - отправил "Предмет - текст" -> добавлено
 - /dz          показать все ДЗ (красиво)
 - /ras         расписание (без ДЗ)
 - /clear       очистить все ДЗ (админ)
 - /remove_dz   удалить ДЗ по предмету (админ)
 - /history     показать историю удалений/добавлений
 - /find        найти ДЗ по предмету
 - /edit_dz     редактирование ДЗ (админ)
 - /short       короткий список предметов с ДЗ
 - автоудаление ДЗ после истечения урока
 - сохранение/загрузка из GitHub Gist (dz.json, history.json)
 - локальные бэкапы при ошибках
 - cooldowns для /dz и /ras (по 4 часа на пользователя)
 - совместимость с Railway (фиск event loop)
"""

import os
import re
import json
import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import List, Dict, Any, Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# -------------------- CONFIG --------------------
# Переменные окружения (добавь в Railway)
BOT_TOKEN = os.getenv("BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6193109213"))  # твой id по умолчанию
COOLDOWN_HOURS = int(os.getenv("COOLDOWN_HOURS", "4"))
TZ_NAME = os.getenv("TZ", "Europe/Riga")  # или Europe/Amsterdam
TZ = ZoneInfo(TZ_NAME)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не установлен")
if not GITHUB_TOKEN:
    raise RuntimeError("GITHUB_TOKEN не установлен")
if not GIST_ID:
    raise RuntimeError("GIST_ID не установлен")

# Файлы локально (на Railway ephemeral, но используем как кэш и для бэкапов)
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DZ_LOCAL = DATA_DIR / "dz.json"
HISTORY_LOCAL = DATA_DIR / "history.json"
USERCD_LOCAL = DATA_DIR / "user_cd.json"
RASCD_LOCAL = DATA_DIR / "ras_cd.json"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_KEEP = 7  # сколько бэкапов хранить

# Настройка логов
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dz-bot")

# -------------------- SCHEDULE --------------------
# уроки по дням (индексы 0..6)
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Труд", "Труд", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский язык", "Алгебра", "Информатика", "Биология", "Английский язык", "Труд"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литература"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский язык", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский язык", "История", "Геометрия"]
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

# Длительность уроков/перемен (минуты)
LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40  # между 3 и 4 уроком
FIRST_LESSON_START_MIN = 8 * 60  # 08:00

# -------------------- SUBJECT NORMALIZATION --------------------
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

# emoji map
EMOJI_MAP = {
    "русский": "📘",
    "английский": "🇬🇧",
    "алгебра": "🧮",
    "геометр": "📐",
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

# -------------------- IN-MEMORY DATA --------------------
dz_list: List[Dict[str, Any]] = []      # active assignments: {subject, task, day, lesson_index, assigned_date, end_iso}
dz_history: List[Dict[str, Any]] = []   # removed or edited entries with timestamps and reason
user_cd: Dict[str, str] = {}            # cooldown /dz -> user_id: iso
ras_cd: Dict[str, str] = {}             # cooldown /ras -> user_id: iso
last_subject_for_admin: Optional[str] = None  # for quick continuation lines

# -------------------- UTIL: local persistence --------------------
def _load_local():
    global dz_list, dz_history, user_cd, ras_cd
    try:
        if DZ_LOCAL.exists():
            with DZ_LOCAL.open("r", encoding="utf-8") as f:
                dz_list = json.load(f)
        else:
            dz_list = []
    except Exception:
        logger.exception("Failed to load local dz.json")
        dz_list = []
    try:
        if HISTORY_LOCAL.exists():
            with HISTORY_LOCAL.open("r", encoding="utf-8") as f:
                dz_history = json.load(f)
        else:
            dz_history = []
    except Exception:
        logger.exception("Failed to load local history.json")
        dz_history = []
    try:
        if USERCD_LOCAL.exists():
            with USERCD_LOCAL.open("r", encoding="utf-8") as f:
                user_cd.update(json.load(f))
    except Exception:
        logger.exception("Failed to load user_cd.json")
    try:
        if RASCD_LOCAL.exists():
            with RASCD_LOCAL.open("r", encoding="utf-8") as f:
                ras_cd.update(json.load(f))
    except Exception:
        logger.exception("Failed to load ras_cd.json")

def _save_local(backup: bool = False):
    try:
        with DZ_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(dz_list, f, ensure_ascii=False, indent=2)
        with HISTORY_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(dz_history, f, ensure_ascii=False, indent=2)
        with USERCD_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(user_cd, f, ensure_ascii=False, indent=2)
        with RASCD_LOCAL.open("w", encoding="utf-8") as f:
            json.dump(ras_cd, f, ensure_ascii=False, indent=2)
        if backup:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with (BACKUP_DIR / f"dz_{ts}.json").open("w", encoding="utf-8") as f:
                json.dump(dz_list, f, ensure_ascii=False, indent=2)
            with (BACKUP_DIR / f"history_{ts}.json").open("w", encoding="utf-8") as f:
                json.dump(dz_history, f, ensure_ascii=False, indent=2)
            # cleanup old backups
            backups = sorted(BACKUP_DIR.glob("dz_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[BACKUP_KEEP:]:
                try:
                    old.unlink()
                except Exception:
                    pass
    except Exception:
        logger.exception("Failed to save local files")

# -------------------- UTIL: Gist load/save --------------------
GIST_URL = f"https://api.github.com/gists/{GIST_ID}"

async def load_from_gist():
    global dz_list, dz_history
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(GIST_URL, headers=headers, timeout=20) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    files = payload.get("files", {})
                    dz_text = files.get("dz.json", {}).get("content", "{}")
                    hist_text = files.get("history.json", {}).get("content", "[]")
                    try:
                        dz_list = json.loads(dz_text) if dz_text else []
                    except Exception:
                        dz_list = []
                        logger.exception("Failed parsing dz.json from gist")
                    try:
                        dz_history = json.loads(hist_text) if hist_text else []
                    except Exception:
                        dz_history = []
                        logger.exception("Failed parsing history.json from gist")
                    _save_local()
                    logger.info("Loaded data from Gist")
                    return True
                else:
                    text = await resp.text()
                    logger.warning("Gist GET failed %s: %s", resp.status, text[:200])
    except Exception:
        logger.exception("Error contacting gist")
    # fallback to local
    _load_local()
    return False

async def save_to_gist():
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "files": {
            "dz.json": {"content": json.dumps(dz_list, ensure_ascii=False, indent=2)},
            "history.json": {"content": json.dumps(dz_history, ensure_ascii=False, indent=2)}
        }
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(GIST_URL, headers=headers, json=payload, timeout=30) as resp:
                if 200 <= resp.status < 300:
                    _save_local()
                    logger.info("Saved to Gist")
                    return True
                else:
                    text = await resp.text()
                    logger.warning("Gist PATCH failed %s: %s", resp.status, text[:200])
    except Exception:
        logger.exception("Error saving to gist")
    # on failure, save local and backup
    _save_local(backup=True)
    return False

# -------------------- TIME & SCHEDULE HELPERS --------------------
def normalize_subject(name: str) -> str:
    if not name:
        return name
    key = name.strip().lower()
    return SUBJECT_ALIAS.get(key, name.strip().title())

def lesson_start_end_for_date(d: date, idx: int):
    minutes = FIRST_LESSON_START_MIN
    for i in range(idx):
        minutes += LESSON_DURATION
        minutes += LONG_BREAK if i == 2 else SHORT_BREAK
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=TZ) + timedelta(minutes=minutes)
    end = start + timedelta(minutes=LESSON_DURATION)
    return start, end

def next_date_for_day_key(day_key: str, from_dt: Optional[datetime] = None):
    if from_dt is None:
        from_dt = datetime.now(TZ)
    today = from_dt.date()
    # map weekday to 0..4 for DAYS_ORDER
    cur_wd = from_dt.weekday()  # Mon=0
    try:
        target = DAYS_ORDER.index(day_key)
    except ValueError:
        # fallback
        return today
    # compute delta modulo 7
    delta_days = (target - cur_wd) % 7
    candidate = today + timedelta(days=delta_days)
    return candidate

def find_subject_positions_exact(subject_name: str):
    res = []
    norm = normalize_subject(subject_name).lower()
    for day, lessons in SCHEDULE.items():
        for idx, lesson in enumerate(lessons):
            if norm == normalize_subject(lesson).lower():
                res.append((day, idx))
    return res

def assign_one(subject: str, task: str):
    now = datetime.now(TZ)
    positions = find_subject_positions_exact(subject)
    if not positions:
        return None
    candidates = []
    for day_key, idx in positions:
        candidate_date = next_date_for_day_key(day_key, now)
        start, end = lesson_start_end_for_date(candidate_date, idx)
        # if same weekday and already passed -> choose next week
        # map now.weekday() (0..6) to DAYS_ORDER index? simpler: compare dates
        if candidate_date == now.date() and now >= end:
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
    now = datetime.now(TZ)
    removed = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) <= now]
    if removed:
        for r in removed:
            dz_history.append({**r, "removed_at": now.isoformat(), "reason": "auto"})
        dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]
        _save_local()
        if auto_save:
            # schedule save_to_gist but don't await here
            asyncio.create_task(save_to_gist())
        return True
    return False

# -------------------- FORMATTING --------------------
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

def format_dz_for_display():
    remove_expired(auto_save=False)
    if not dz_list:
        return "🗒 Домашек нет — всё чисто."
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
            for t in str(task).split("\n"):
                if t.strip():
                    lines.append(f"> {t.strip()}")
            # add end time optionally (commented by default)
            # end_str = datetime.fromisoformat(r["end_iso"]).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            # lines.append(f"_кончится: {end_str}_")
            lines.append("")  # blank between lessons
        lines.append("─ ─ ─")
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

# -------------------- PARSING / ADD LINES --------------------
DAY_WORDS = {"пн","пн.","понедельник","вт","вт.","вторник","ср","ср.","среда","чт","чт.","четверг","пт","пт.","пятница"}

def strip_day_time_prefix(line: str) -> str:
    s = line.strip()
    # drop leading time like 08:40 or 8:40
    s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', '', s)
    # drop leading day words
    tokens = s.split()
    if tokens:
        first = tokens[0].lower().rstrip(":,")
        if first in DAY_WORDS:
            s = " ".join(tokens[1:])
    # drop time again
    s = re.sub(r'^\s*\d{1,2}[:.]\d{2}\s*', '', s)
    return s.strip()

async def process_add_lines(lines: List[str]):
    global last_subject_for_admin
    added = []
    warnings = []
    for raw in lines:
        s = strip_day_time_prefix(raw)
        if not s:
            continue
        if "-" not in s:
            # continuation of last subject?
            if last_subject_for_admin and added:
                # append to last added record in dz_list for that subject
                subj = last_subject_for_admin
                # find most recent assignment for subject
                candidates = [r for r in dz_list if normalize_subject(r["subject"]).lower() == normalize_subject(subj).lower()]
                if candidates:
                    candidates = sorted(candidates, key=lambda x: x.get("assigned_date",""), reverse=True)
                    candidates[0]["task"] = candidates[0]["task"] + " " + s
                else:
                    warnings.append(f"Нет предыдущего предмета для продолжения: '{s}'")
            else:
                warnings.append(f"Пропущено (нет '-' и нет предыдущего предмета): {raw}")
            continue
        subj_raw, task_raw = map(str.strip, s.split("-", 1))
        subj_norm = normalize_subject(subj_raw)
        last_subject_for_admin = subj_norm
        rec = assign_one(subj_norm, task_raw)
        if rec is None:
            warnings.append(f"Предмет '{subj_raw}' не найден в расписании — пропущено.")
            continue
        # check duplicate on same assigned_date & subject
        exists = next((r for r in dz_list if normalize_subject(r["subject"]).lower() == normalize_subject(rec["subject"]).lower() and r["assigned_date"] == rec["assigned_date"]), None)
        if exists:
            if exists["task"].strip() == rec["task"].strip():
                # exact duplicate skip
                continue
            else:
                warnings.append(f"⚠️ По предмету {rec['subject']} уже есть другое ДЗ на {rec['assigned_date']}.")
                continue
        dz_list.append(rec)
        dz_history.append({**rec, "added_at": datetime.now(TZ).isoformat(), "reason": "added"})
        added.append(f"{rec['subject']} ({rec['assigned_date']})")
    _save_local()
    await save_to_gist()
    return added, warnings

# -------------------- COMMAND HANDLERS --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для ДЗ.\n"
        "Админ может добавить ДЗ простым текстом или командой /add_dz\n\n"
        "Команды:\n"
        "/add_dz — добавить ДЗ (многострочно)\n"
        "/dz — показать текущие ДЗ\n"
        "/ras — расписание недели\n"
        "/clear — очистить все ДЗ (админ)\n"
        "/remove_dz <предмет> — удалить ДЗ по предмету (админ)\n"
        "/history — показать историю удалённых/добавленных\n"
        "/find <предмет> — найти ДЗ по предмету\n"
        "/edit_dz <предмет> - <новое дз> — редактировать (админ)\n"
        "/short — короткий список предметов с ДЗ\n"
    )

async def cmd_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if remove_expired():
        await save_to_gist()
    text = format_dz_for_display()
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
    await update.message.reply_markdown(format_schedule_text())

async def cmd_add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может добавлять ДЗ.")
        return
    full = update.message.text or ""
    # if /add_dz with multiline content -> take lines after command
    if "\n" in full:
        lines = full.splitlines()[1:]
    else:
        args_text = " ".join(context.args) if context.args else ""
        if not args_text:
            await update.message.reply_text("Использование: /add_dz Предмет - ДЗ (или многосрочно после команды).")
            return
        lines = [args_text]
    added, warnings = await process_add_lines(lines)
    parts = []
    if added:
        parts.append("✅ Добавлено:\n" + "\n".join(added))
    if warnings:
        parts.append("⚠️ Предупреждения:\n" + "\n".join(warnings))
    if not parts:
        parts = ["Ни одного задания не добавлено."]
    await update.message.reply_text("\n\n".join(parts))

async def cmd_remove_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может удалять.")
        return
    subj = " ".join(context.args).strip()
    if not subj:
        await update.message.reply_text("Использование: /remove_dz <предмет>")
        return
    subj_norm = normalize_subject(subj)
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
        await update.message.reply_text("Ничего не найдено.")

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
    await load_from_gist()  # ensure latest
    if not dz_history:
        await update.message.reply_text("История удалённых ДЗ пуста.")
        return
    items = dz_history[-100:]
    lines = []
    for r in reversed(items):
        removed = r.get("removed_at") or r.get("added_at") or ""
        subj = r.get("subject", "")
        task = r.get("task", r.get("task", r.get("hw", "")))
        reason = r.get("reason", "")
        lines.append(f"{removed[:16]} | {subj} | {task} [{reason}]")
    # send in chunks
    chunk = 4000
    msg = "\n".join(lines)
    for i in range(0, len(msg), chunk):
        await update.message.reply_text(msg[i:i+chunk])

async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /find <предмет>")
        return
    subj = normalize_subject(" ".join(context.args))
    results = [r for r in dz_list if normalize_subject(r["subject"]).lower() == subj.lower()]
    if not results:
        await update.message.reply_text("ДЗ по этому предмету не найдены.")
        return
    lines = []
    for r in sorted(results, key=lambda x: x["assigned_date"]):
        lines.append(f"▫️ *{r['subject']}* ({r['assigned_date']})\n> {r['task']}\n")
    await update.message.reply_markdown("\n".join(lines))

async def cmd_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not dz_list:
        await update.message.reply_text("Домашек нет.")
        return
    subjects = sorted({r["subject"] for r in dz_list})
    await update.message.reply_text("📚 Есть ДЗ по:\n" + ", ".join(subjects))

async def cmd_edit_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может редактировать.")
        return
    text = " ".join(context.args)
    if "-" not in text:
        await update.message.reply_text("Использование: /edit_dz <предмет> - <новое дз>")
        return
    subj_raw, task = map(str.strip, text.split("-", 1))
    subj = normalize_subject(subj_raw)
    candidates = [r for r in dz_list if normalize_subject(r["subject"]).lower() == subj.lower()]
    if not candidates:
        await update.message.reply_text("Такого предмета нет в текущих ДЗ.")
        return
    candidates = sorted(candidates, key=lambda x: x["assigned_date"], reverse=True)
    rec = candidates[0]
    rec["task"] = task
    dz_history.append({**rec, "edited_at": datetime.now(TZ).isoformat(), "reason": "edited", "new_task": task})
    _save_local()
    await save_to_gist()
    await update.message.reply_text(f"✅ ДЗ по {subj} обновлено.")

# -------------------- MESSAGE HANDLER (plain text adds) --------------------
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if "-" not in text:
        return
    # only admin can add via plain text
    if update.effective_user.id != ADMIN_ID:
        # ignore messages from others to prevent spam
        return
    # support sending block starting with /add_dz or just lines
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
        resp = ["Ни одного задания не добавлено."]
    await update.message.reply_text("\n\n".join(resp))

# -------------------- BACKGROUND CLEANUP TASK --------------------
async def periodic_cleanup():
    while True:
        try:
            changed = remove_expired(auto_save=False)
            if changed:
                await save_to_gist()
            await asyncio.sleep(300)  # 5 minutes
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error in periodic cleanup")
            await asyncio.sleep(60)

# -------------------- STARTUP / MAIN --------------------
async def main():
    # load from gist or local
    await load_from_gist()
    _save_local()
    # initial cleanup
    if remove_expired(auto_save=False):
        await save_to_gist()
    # build app
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # register handlers
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
    # start periodic cleanup
    task = asyncio.create_task(periodic_cleanup())
    logger.info("Bot started")
    try:
        await app.run_polling()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

# Railway-friendly bootstrap
if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # if an event loop is already running, schedule main()
            loop.create_task(main())
        else:
            loop.run_until_complete(main())
    except RuntimeError:
        asyncio.run(main())
