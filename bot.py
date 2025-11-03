#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Homework Bot (fixed & improved)
Requirements:
  - python-telegram-bot v20+
  - aiohttp
  - python-dotenv (optional)
Environment variables:
  BOT_TOKEN, GIST_TOKEN, GIST_ID, ADMIN_ID
"""

import asyncio
import aiohttp
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional
import datetime
import uuid

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- Config / constants ---
COOLDOWN_SECONDS = 4 * 3600  # 4 hours
LOCAL_DZ = "dz.json"
LOCAL_HISTORY = "history.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
PRUNE_INTERVAL = 60  # seconds to check outdated lessons
GIST_RETRY = 3

# Simple built-in weekly schedule (editable)
# rasp: dict weekday(int 0=Mon..6=Sun) -> list of lessons {time: "HH:MM", subject: "Math"}
# Edit this to match your school's schedule.
RAS_SCHEDULE = {
    0: [ {"time":"09:00","subject":"Математика"}, {"time":"10:00","subject":"Русский"} ], # Monday
    1: [ {"time":"09:00","subject":"Физика"}, {"time":"10:00","subject":"История"} ],  # Tue
    2: [ {"time":"09:00","subject":"Информатика"}, {"time":"10:00","subject":"Английский"}],
    3: [ {"time":"09:00","subject":"Биология"}, {"time":"10:00","subject":"География"}],
    4: [ {"time":"09:00","subject":"Химия"}, {"time":"10:00","subject":"Литература"}],
    5: [],  # Saturday
    6: [],  # Sunday
}

# ----- Logging -----
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("homeworkbot")

# ----- Load env -----
from dotenv import load_dotenv
load_dotenv()  # optional .env

BOT_TOKEN = os.getenv("BOT_TOKEN")
GIST_TOKEN = os.getenv("GIST_TOKEN")
GIST_ID = os.getenv("GIST_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is required in env")
    raise SystemExit(1)

# ----- Global async session & lock for Gist -----
_gist_lock = asyncio.Lock()
_aio_session: Optional[aiohttp.ClientSession] = None

def local_path(name: str) -> Path:
    p = Path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

async def get_session() -> aiohttp.ClientSession:
    global _aio_session
    if _aio_session is None or _aio_session.closed:
        headers = {
            "Authorization": f"token {GIST_TOKEN}" if GIST_TOKEN else "",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "HomeworkBot/1.0",
        }
        # Remove empty Authorization header if no token
        if not GIST_TOKEN:
            headers.pop("Authorization", None)
        _aio_session = aiohttp.ClientSession(headers=headers)
    return _aio_session

async def load_gist_file(filename: str) -> Any:
    """Try to load JSON from Gist -> fallback to local file -> return default {} or []"""
    # prefer reading from GitHub Gist if token+id provided
    if GIST_TOKEN and GIST_ID:
        async with _gist_lock:
            session = await get_session()
            gist_url = f"https://api.github.com/gists/{GIST_ID}"
            for attempt in range(GIST_RETRY):
                try:
                    async with session.get(gist_url, timeout=15) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            files = data.get("files", {})
                            if filename in files:
                                raw = files[filename].get("raw_url")
                                if raw:
                                    async with session.get(raw) as r2:
                                        txt = await r2.text()
                                        try:
                                            parsed = json.loads(txt)
                                            logger.info("Loaded %s from Gist", filename)
                                            # Save local copy
                                            p = local_path(filename)
                                            p.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
                                            return parsed
                                        except Exception:
                                            logger.exception("Failed parsing JSON from gist for %s", filename)
                                        break
                        else:
                            logger.warning("Gist GET returned %s", resp.status)
                except Exception:
                    logger.warning("Gist load attempt %d failed for %s", attempt+1, filename)
                    await asyncio.sleep(1 + attempt)
    # fallback local file
    p = local_path(filename)
    if p.exists():
        try:
            parsed = json.loads(p.read_text(encoding="utf-8"))
            logger.info("Loaded %s from local file", filename)
            return parsed
        except Exception:
            logger.exception("Failed to parse local %s", filename)
    # default: return empty structure depending on filename
    return {} if filename.endswith(".json") else None

async def save_gist_file(filename: str, payload: Any) -> bool:
    """Write JSON to Gist (PATCH files) with retries and lock. Also write local file atomically."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # write local atomic
    p = local_path(filename)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    except Exception:
        logger.exception("Failed atomic write local file for %s", filename)
    # try push to Gist if configured
    if not (GIST_TOKEN and GIST_ID):
        logger.info("Gist not configured; skipped remote save for %s", filename)
        return True
    body = {"files": {filename: {"content": text}}}
    async with _gist_lock:
        session = await get_session()
        url = f"https://api.github.com/gists/{GIST_ID}"
        for attempt in range(GIST_RETRY):
            try:
                async with session.patch(url, json=body, timeout=20) as resp:
                    if resp.status in (200, 201):
                        logger.info("Saved %s to Gist", filename)
                        return True
                    else:
                        txt = await resp.text()
                        logger.warning("Gist PATCH failed %s: %s", resp.status, txt[:200])
            except Exception:
                logger.exception("Gist PATCH attempt %d failed", attempt+1)
            await asyncio.sleep(1 + attempt)
    logger.error("Failed to save %s to Gist after retries", filename)
    return False

# ----- Helper utilities -----
def normalize_text(s: str) -> str:
    return " ".join(s.strip().split())

def make_history_entry(action: str, item: dict, user_id: int, reason: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "action": action,
        "item": item,
        "user_id": user_id,
        "reason": reason,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }

def is_admin(user_id: int) -> bool:
    return ADMIN_ID and user_id == ADMIN_ID

# simple in-memory cooldown store: user_id -> { cmd: last_ts }
_COOLDOWNS: Dict[int, Dict[str, float]] = {}

def check_and_set_cooldown(user_id: int, cmd: str, seconds: int = COOLDOWN_SECONDS) -> (bool, int):
    rec = _COOLDOWNS.setdefault(user_id, {})
    last = rec.get(cmd)
    now_ts = time()
    if last and now_ts - last < seconds:
        remain = int(seconds - (now_ts - last))
        return True, remain
    rec[cmd] = now_ts
    return False, 0

def fmt_seconds(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    parts = []
    if h: parts.append(f"{h}ч")
    if m: parts.append(f"{m}м")
    if s and not parts: parts.append(f"{s}с")
    return " ".join(parts) if parts else "0с"

def subject_case_insensitive_equals(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()

# ----- Business logic: find nearest lesson datetime for a subject -----
def find_nearest_lesson_datetime(subject: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """Find the next lesson datetime for subject according to RAS_SCHEDULE."""
    if now is None:
        now = datetime.datetime.now()
    # search next 7 days
    target = subject.strip().lower()
    for day_offset in range(0, 8):  # include today->7
        candidate_day = now + datetime.timedelta(days=day_offset)
        weekday = candidate_day.weekday()
        lessons = RAS_SCHEDULE.get(weekday, [])
        # sort lessons by time
        for lesson in lessons:
            subj = lesson.get("subject", "").strip().lower()
            if subj == target:
                # build datetime
                hhmm = lesson.get("time", "00:00")
                try:
                    hh, mm = map(int, hhmm.split(":"))
                    dt = datetime.datetime(candidate_day.year, candidate_day.month, candidate_day.day, hh, mm)
                    # if searching today, ensure it's not earlier than now (if same day)
                    if day_offset == 0 and dt < now:
                        continue
                    return dt
                except Exception:
                    continue
    return None

# ----- Formatters for /dz output -----
def format_dz_message(dz_store: Dict[str, List[dict]]) -> str:
    if not dz_store:
        return "📚 *Домашних заданий не найдено.*"
    lines: List[str] = []
    # sort days ascending
    for day in sorted(dz_store.keys()):
        try:
            dt = datetime.date.fromisoformat(day)
            day_header = dt.strftime("%A, %d.%m.%Y")
        except Exception:
            day_header = day
        lines.append(f"📅 *{day_header}*")
        for it in dz_store[day]:
            sub = it.get("subject", "—")
            txt = it.get("text", "")
            added_by = it.get("added_by")
            added_at = it.get("added_at", "")
            lines.append(f"• *{sub}* — _{txt}_")
        lines.append("")  # gap
    return "\n".join(lines)

def format_ras_message() -> str:
    lines = ["📆 *Расписание на неделю:*"]
    names = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    for wd in range(7):
        lessons = RAS_SCHEDULE.get(wd, [])
        if not lessons:
            lines.append(f"{names[wd]}: —")
        else:
            lstr = ", ".join([f"{x['time']} {x['subject']}" for x in lessons])
            lines.append(f"{names[wd]}: {lstr}")
    return "\n".join(lines)

# ----- Handlers -----
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для хранения и управления ДЗ. /add_dz, /dz, /ras, /history")

async def cmd_ras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    blocked, remain = check_and_set_cooldown(user.id, "ras")
    if blocked:
        await update.message.reply_text(f"Команда /ras доступна через {fmt_seconds(remain)}.")
        return
    text = format_ras_message()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    blocked, remain = check_and_set_cooldown(user.id, "dz")
    if blocked:
        await update.message.reply_text(f"Команда /dz доступна через {fmt_seconds(remain)}.")
        return
    dz_store = context.application.bot_data.get("dz", {})
    text = format_dz_message(dz_store)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # optional: accept /history <subject>
    args = context.args or []
    subject_filter = " ".join(args).strip().lower() if args else None
    history = context.application.bot_data.get("history", [])
    if not history:
        await update.message.reply_text("История пуста.")
        return
    lines = []
    count = 0
    for ev in reversed(history):  # newest first
        item = ev.get("item", {})
        subj = (item.get("subject") or "").strip()
        txt = item.get("text") or ""
        if subject_filter and subject_filter not in subj.lower():
            continue
        ts = ev.get("timestamp", "")
        action = ev.get("action", "action")
        lines.append(f"• [{action}] *{subj}* — _{txt}_ ({ts})")
        count += 1
        if count >= 50:
            break
    if not lines:
        await update.message.reply_text("Нет записей по запросу.")
        return
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("Только админ может использовать /clear")
        return
    # /clear [subject] or /clear all
    args = context.args or []
    dz_store: Dict[str, List[dict]] = context.application.bot_data.setdefault("dz", {})
    history: List[dict] = context.application.bot_data.setdefault("history", [])
    if not args:
        await update.message.reply_text("Укажи предмет или 'all' для удаления всего: /clear all OR /clear Математика")
        return
    key = " ".join(args).strip()
    if key.lower() == "all":
        # move all to history as deleted
        for day, items in list(dz_store.items()):
            for it in items:
                history.append(make_history_entry("deleted", it, user.id, reason="clear_all"))
        context.application.bot_data["dz"] = {}
        await save_gist_file(LOCAL_DZ, context.application.bot_data["dz"])
        await save_gist_file(LOCAL_HISTORY, history)
        await update.message.reply_text("Все ДЗ удалены и перемещены в историю.")
        return
    # delete by subject across all days
    removed = 0
    for day, items in list(dz_store.items()):
        new_items = []
        for it in items:
            if subject_case_insensitive_equals(it.get("subject",""), key):
                history.append(make_history_entry("deleted", it, user.id, reason="clear_subject"))
                removed += 1
            else:
                new_items.append(it)
        dz_store[day] = new_items
    await save_gist_file(LOCAL_DZ, dz_store)
    await save_gist_file(LOCAL_HISTORY, history)
    await update.message.reply_text(f"Удалено записей по предмету '{key}': {removed}")

# /add_dz — main handler. Accepts "Предмет: текст" after the command.
async def cmd_add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # get raw message minus command
    raw = update.message.text or ""
    content = raw.replace("/add_dz", "", 1).strip()
    if not content:
        # maybe user sent as reply or with args
        args = context.args or []
        content = " ".join(args).strip()
    if not content:
        await update.message.reply_text("Формат: /add_dz Предмет: Текст задания")
        return
    content = normalize_text(content)
    # parse subject:text
    subject = ""
    text = ""
    if ":" in content:
        subject, text = map(str.strip, content.split(":", 1))
    elif "—" in content:
        subject, text = map(str.strip, content.split("—", 1))
    elif "-" in content:
        # last resort split by dash
        subject, text = map(str.strip, content.split("-", 1))
    else:
        await update.message.reply_text("Неправильный формат. Используй: /add_dz Предмет: Текст задания")
        return
    if not subject or not text:
        await update.message.reply_text("Не указан предмет или текст задания.")
        return
    if len(text) > 2000:
        await update.message.reply_text("Слишком длинное задание (макс 2000 символов).")
        return

    # find nearest lesson datetime
    nearest = find_nearest_lesson_datetime(subject)
    if not nearest:
        await update.message.reply_text("Не могу найти предмет в расписании. Проверь расписание бота.")
        return
    day_key = nearest.date().isoformat()
    dz_store: Dict[str, List[dict]] = context.application.bot_data.setdefault("dz", {})
    day_items = dz_store.setdefault(day_key, [])
    # check duplicates by subject
    normalized_new_text = normalize_text(text)
    for item in day_items:
        if subject_case_insensitive_equals(item.get("subject",""), subject):
            old_text = normalize_text(item.get("text",""))
            if old_text == normalized_new_text:
                await update.message.reply_text("Уже есть такое ДЗ на ближайший урок — не меняю.")
                return
            else:
                # move old to history with replaced_by
                history: List[dict] = context.application.bot_data.setdefault("history", [])
                old_with_meta = dict(item)
                old_with_meta["replaced_by"] = text
                old_with_meta["replaced_at"] = datetime.datetime.utcnow().isoformat() + "Z"
                history.append(make_history_entry("replaced", old_with_meta, user.id, reason="add_dz_replaced"))
                # append new
                new_item = {
                    "subject": subject,
                    "text": text,
                    "lesson_datetime": nearest.isoformat(),
                    "added_by": user.id,
                    "added_at": datetime.datetime.utcnow().isoformat() + "Z",
                }
                day_items.append(new_item)
                await save_gist_file(LOCAL_DZ, dz_store)
                await save_gist_file(LOCAL_HISTORY, history)
                await update.message.reply_text("Обнаружено ДЗ по предмету — старое перемещено в историю, добавлено новое.")
                return
    # add new if subject not present
    new_item = {
        "subject": subject,
        "text": text,
        "lesson_datetime": nearest.isoformat(),
        "added_by": user.id,
        "added_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    day_items.append(new_item)
    # persist
    await save_gist_file(LOCAL_DZ, dz_store)
    await update.message.reply_text(f"Добавил ДЗ на ближайший урок по *{subject}* ({nearest.date().isoformat()}) ✅", parse_mode=ParseMode.MARKDOWN)

# fallback text handler (optional) - ignore
async def echo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ignore plain messages to reduce noise
    pass

# ----- Jobs: prune old lessons -----
async def prune_old_dz(context: ContextTypes.DEFAULT_TYPE):
    try:
        dz_store: Dict[str, List[dict]] = context.application.bot_data.setdefault("dz", {})
        history: List[dict] = context.application.bot_data.setdefault("history", [])
        now = datetime.datetime.now()
        changed = False
        for day_key in list(dz_store.keys()):
            try:
                day_date = datetime.date.fromisoformat(day_key)
            except Exception:
                continue
            # if day is before today -> remove all
            if day_date < now.date():
                items = dz_store.pop(day_key, [])
                for it in items:
                    history.append(make_history_entry("deleted", it, 0, reason="date_passed"))
                changed = True
            elif day_date == now.date():
                # also remove lessons whose lesson_datetime < now (time passed)
                items = dz_store.get(day_key, [])
                new_items = []
                for it in items:
                    try:
                        ldt = datetime.datetime.fromisoformat(it.get("lesson_datetime"))
                    except Exception:
                        ldt = None
                    if ldt and ldt < now:
                        history.append(make_history_entry("deleted", it, 0, reason="lesson_passed"))
                        changed = True
                    else:
                        new_items.append(it)
                dz_store[day_key] = new_items
        if changed:
            await save_gist_file(LOCAL_DZ, dz_store)
            await save_gist_file(LOCAL_HISTORY, history)
            logger.info("Pruned old DZ and updated storage.")
    except Exception:
        logger.exception("Error during prune job")

# ----- Application builder and start -----
def build_application() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()
    # register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ras", cmd_ras))
    app.add_handler(CommandHandler("dz", cmd_dz))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("add_dz", cmd_add_dz))
    # ignore text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_handler), 0)
    # schedule the prune job after startup (we will register job in startup)
    return app

async def load_all_data(app: Application):
    # load dz and history from gist/local to app.bot_data
    dz = await load_gist_file(LOCAL_DZ)
    history = await load_gist_file(LOCAL_HISTORY)
    # ensure types
    if not isinstance(dz, dict):
        dz = {}
    if not isinstance(history, list):
        history = history if isinstance(history, list) else []
    app.bot_data["dz"] = dz
    app.bot_data["history"] = history
    # optional: store rasp
    app.bot_data["rasp"] = RAS_SCHEDULE
    logger.info("Loaded data into bot_data: dz=%d days, history=%d events", len(dz), len(history))

async def on_startup(app: Application):
    try:
        await load_all_data(app)
        # register prune job repeating
        app.job_queue.run_repeating(prune_old_dz, interval=PRUNE_INTERVAL, first=10)
        logger.info("Startup tasks completed. Prune job scheduled.")
    except Exception:
        logger.exception("Error in startup")

async def on_shutdown(app: Application):
    try:
        # flush data
        dz = app.bot_data.get("dz", {})
        history = app.bot_data.get("history", [])
        await save_gist_file(LOCAL_DZ, dz)
        await save_gist_file(LOCAL_HISTORY, history)
        # close session
        global _aio_session
        if _aio_session:
            await _aio_session.close()
        logger.info("Shutdown complete.")
    except Exception:
        logger.exception("Shutdown failed.")

async def main():
    # create app
    app = build_application()
    # add startup/shutdown handlers
    app.post_init = None  # ensure no legacy stuff
    # We'll use application.initialize()/run_polling() via asyncio (await app.run_polling())
    # but we need to attach our on_startup tasks to job queue: do it by scheduling create_task after init
    # register our startup/shutdown manually using Application.add_handler? Simpler: call load here before run
    # Register start/shutdown calls via callbacks
    # Attach our on_startup/on_shutdown using callback attributes
    app.add_handler  # noop to satisfy linter
    # attach user defined tasks using events
    app._user_data_lock = asyncio.Lock()  # ensure exists
    try:
        # initialize app (this will setup everything internally)
        await app.initialize()
        # load data and register prune job
        await load_all_data(app)
        app.job_queue.run_repeating(prune_old_dz, interval=PRUNE_INTERVAL, first=10)
        # start polling and block until stopped
        logger.info("Starting bot polling...")
        await app.start()
        await app.updater.start_polling() if getattr(app, "updater", None) else None
        # Actually run polling (higher-level helper)
        await app.run_polling()  # library manages loop inside (async)
    except RuntimeError as e:
        # handle "loop already running" etc
        logger.exception("Runtime error in main: %s", e)
        raise
    except Exception:
        logger.exception("Unhandled exception in main")
        raise
    finally:
        try:
            await on_shutdown(app)
        except Exception:
            logger.exception("Error during final shutdown")

if __name__ == "__main__":
    # Run main with asyncio.run to ensure single event loop control
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("Fatal error while running bot")
