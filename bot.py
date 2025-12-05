# schoolbot_v2_ultimate.py
"""
Unified bot + web panel.
Uses external module tickets_system.py for ticket handling.
Requirements:
  pip install python-telegram-bot==20.3 Flask APScheduler
Run:
  export BOT_TOKEN="..."
  export WEB_PASS="..."
  python schoolbot_v2_ultimate.py
"""

import os
import sqlite3
import threading
import logging
import datetime
import difflib
import csv
import io
import re
from typing import List, Tuple, Optional
from flask import Flask, request, redirect, url_for, render_template_string, make_response
from apscheduler.schedulers.background import BackgroundScheduler

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# import tickets module (separate file)
import tickets_system as tickets

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("schoolbot_v2_ultimate")

# ---------------- config ----------------
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6193109213"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEB_PASS = os.environ.get("WEB_PASS", "admin123")
DB_PATH = os.environ.get("DB_PATH", "schoolbot_v2_ultimate.db")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is not set. Set environment variable BOT_TOKEN.")
    raise SystemExit("BOT_TOKEN not set")

# ---------------- Flask app ----------------
app = Flask(__name__)
WEB_SESSIONS = {}  # token -> expiry datetime

# ---------------- Database ----------------
class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                task TEXT,
                day TEXT,
                time TEXT,
                photo_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cleaned_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cleaned_date TEXT,
                subjects TEXT,
                photo_file_ids TEXT,
                cleaned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add_homework(self, subject: str, task: str, day: str, time: str, photo_file_id: Optional[str] = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO homework (subject, task, day, time, photo_file_id) VALUES (?, ?, ?, ?, ?)",
            (subject, task, day, time, photo_file_id)
        )
        self.conn.commit()
        return cur.lastrowid

    def list_homework(self) -> List[Tuple]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, subject, task, day, time, photo_file_id, created_at FROM homework ORDER BY day, time")
        return cur.fetchall()

    def get_homework(self, hid: int) -> Optional[Tuple]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, subject, task, day, time, photo_file_id, created_at FROM homework WHERE id=?", (hid,))
        return cur.fetchone()

    def delete_homework(self, hid: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM homework WHERE id=?", (hid,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_by_subject(self, subject: str) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM homework WHERE lower(subject)=lower(?)", (subject,))
        cnt = cur.rowcount
        self.conn.commit()
        return cnt

    def clear_all(self) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM homework")
        cnt = cur.rowcount
        self.conn.commit()
        return cnt

    def cleanup_old_homework(self) -> List[str]:
        cleaned_subjects = []
        cleaned_photos = []
        try:
            now = datetime.datetime.now()
            rus_today = ScheduleManager.get_russian_day_name(now.weekday())
            current_time_str = now.strftime("%H:%M")
            cur = self.conn.cursor()
            cur.execute("SELECT id, subject, time, photo_file_id FROM homework WHERE day=? AND time<=?", (rus_today, current_time_str))
            rows = cur.fetchall()
            ids = [r[0] for r in rows]
            for r in rows:
                _, subj, t, pid = r
                cleaned_subjects.append(subj)
                if pid:
                    cleaned_photos.append(pid)
            if ids:
                cur.execute("DELETE FROM homework WHERE id IN ({seq})".format(seq=','.join('?'*len(ids))), ids)
                photo_ids_str = ', '.join([p for p in cleaned_photos if p])
                cur.execute("INSERT INTO cleaned_log (cleaned_date, subjects, photo_file_ids) VALUES (?, ?, ?)",
                            (now.strftime("%Y-%m-%d"), ', '.join(list(set(cleaned_subjects))), photo_ids_str))
                self.conn.commit()
            return list(set(cleaned_subjects))
        except Exception as e:
            logger.exception("Error during cleanup_old_homework: %s", e)
            return []

db = Database()

# ---------------- ScheduleManager ----------------
class ScheduleManager:
    def __init__(self):
        self.schedule = {
            'понедельник': [
                {'number': 1, 'subject': 'Ров', 'time': '08:00'},
                {'number': 2, 'subject': 'Русский язык', 'time': '08:50'},
                {'number': 3, 'subject': 'Физкультура', 'time': '09:40'},
                {'number': 4, 'subject': 'Технология', 'time': '10:40'},
                {'number': 5, 'subject': 'Технология', 'time': '11:30'},
                {'number': 6, 'subject': 'Русский язык', 'time': '12:20'},
                {'number': 7, 'subject': 'Музыка', 'time': '13:10'}
            ],
            'вторник': [
                {'number': 1, 'subject': 'Физика', 'time': '08:00'},
                {'number': 2, 'subject': 'Русский язык', 'time': '08:50'},
                {'number': 3, 'subject': 'Алгебра', 'time': '09:40'},
                {'number': 4, 'subject': 'Информатика', 'time': '10:40'},
                {'number': 5, 'subject': 'Биология', 'time': '11:30'},
                {'number': 6, 'subject': 'Английский язык', 'time': '12:20'},
                {'number': 7, 'subject': 'Английский язык', 'time': '13:10'}
            ],
            'среда': [
                {'number': 1, 'subject': 'Геометрия', 'time': '08:00'},
                {'number': 2, 'subject': 'Физика', 'time': '08:50'},
                {'number': 3, 'subject': 'История', 'time': '09:40'},
                {'number': 4, 'subject': 'Физкультура', 'time': '10:40'},
                {'number': 5, 'subject': 'Русский язык', 'time': '11:30'},
                {'number': 6, 'subject': 'Алгебра', 'time': '12:20'},
                {'number': 7, 'subject': 'Литература', 'time': '13:10'}
            ],
            'четверг': [
                {'number': 1, 'subject': 'Россия-мои горизонты', 'time': '08:00'},
                {'number': 2, 'subject': 'ТВИС', 'time': '08:50'},
                {'number': 3, 'subject': 'География', 'time': '09:40'},
                {'number': 4, 'subject': 'Физкультура', 'time': '10:40'},
                {'number': 5, 'subject': 'Русский язык', 'time': '11:30'},
                {'number': 6, 'subject': 'Изо', 'time': '12:20'},
                {'number': 7, 'subject': 'ОФГ', 'time': '13:10'}
            ],
            'пятница': [
                {'number': 1, 'subject': 'История', 'time': '08:00'},
                {'number': 2, 'subject': 'Алгебра', 'time': '08:50'},
                {'number': 3, 'subject': 'География', 'time': '09:40'},
                {'number': 4, 'subject': 'Английский язык', 'time': '10:40'},
                {'number': 5, 'subject': 'История', 'time': '11:30'},
                {'number': 6, 'subject': 'Геометрия', 'time': '12:20'}
            ]
        }

    @staticmethod
    def get_russian_day_name(weekday: int) -> str:
        days = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        return days[weekday]

    def get_current_day(self) -> str:
        return self.get_russian_day_name(datetime.datetime.now().weekday())

    def find_next_lesson(self, subject: str) -> Optional[dict]:
        subj = subject.lower()
        today = self.get_current_day()
        now = datetime.datetime.now().time()
        if today in self.schedule:
            for lesson in self.schedule[today]:
                ls_subj = lesson['subject'].lower()
                if subj in ls_subj or ls_subj in subj:
                    h, m = map(int, lesson['time'].split(':'))
                    lesson_time = datetime.time(h, m)
                    if now < lesson_time:
                        return {'day': today, 'time': lesson['time'], 'subject': lesson['subject']}
        days_order = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница']
        if today in days_order:
            idx = days_order.index(today)
        else:
            idx = 0
        for i in range(1, len(days_order)):
            d = days_order[(idx + i) % len(days_order)]
            if d in self.schedule:
                for lesson in self.schedule[d]:
                    ls_subj = lesson['subject'].lower()
                    if subj in ls_subj or ls_subj in subj:
                        return {'day': d, 'time': lesson['time'], 'subject': lesson['subject']}
        for d, lessons in self.schedule.items():
            for lesson in lessons:
                if subj in lesson['subject'].lower() or lesson['subject'].lower() in subj:
                    return {'day': d, 'time': lesson['time'], 'subject': lesson['subject']}
        return None

schedule_manager = ScheduleManager()

# ---------------- Parsers, fuzzy helpers, triggers ----------------
MASTER_SUBJECTS = [
    'математика','алгебра','геометрия','русский язык','литература','физика','химия',
    'биология','история','география','английский язык','информатика','физкультура',
    'технология','музыка','изо','обж','мхк','обществознание','офг','твис','ров'
]

SUBJECT_KEYWORDS = {
    'математика': ['математика','матем','мат'],
    'алгебра': ['алгебра','алг'],
    'геометрия': ['геометрия','геом'],
    'русский язык': ['русский язык','русский','русск','русяз'],
    'литература': ['литература','литра'],
    'физика': ['физика','физ'],
    'химия': ['химия','хим'],
    'биология': ['биология','биол'],
    'история': ['история','ист'],
    'география': ['география','геогр','гео'],
    'английский язык': ['английский язык','английский','англ','english'],
    'информатика': ['информатика','инфа','информ'],
    'физкультура': ['физкультура','физра','физ-ра'],
    'технология': ['технология','труд'],
    'музыка': ['музыка','муз'],
    'изо': ['изо','изобразительное'],
    'обж': ['обж','безопасность'],
    'мхк': ['мхк'],
    'обществознание': ['обществознание','общ'],
    'офг': ['офг','финграм','основы финансовой грамотности'],
    'твис': ['твис','тв'],
    'ров': ['ров','разговоры о важном']
}

def normalize_subject(text: str) -> str:
    t = text.strip().lower()
    for subj, keys in SUBJECT_KEYWORDS.items():
        for k in keys:
            if t == k or k in t or t in k:
                return subj
    matches = difflib.get_close_matches(t, MASTER_SUBJECTS, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    return text.strip()

def find_subject_in_text(text: str) -> Optional[str]:
    t = text.lower()
    m = re.search(r'по\s+([а-яё\-\s]+)', t)
    if m:
        candidate = m.group(1).strip()
        candidate = re.sub(r'[^\w\s\-]', '', candidate)
        sub = normalize_subject(candidate)
        if sub:
            return sub
    for subj, keys in SUBJECT_KEYWORDS.items():
        for k in keys:
            if re.search(r'\b' + re.escape(k) + r'\b', t):
                return subj
    for subj in MASTER_SUBJECTS:
        if re.search(r'\b' + re.escape(subj) + r'\b', t):
            return subj
    return None

REQUEST_TOKENS = [
    'скиньте дз','скинь дз','скиньте домашку','скинь домашку','кинь дз','кинь домашку',
    'отправьте дз','отправь дз','отправьте домашку','дай дз','дай домашку',
    'покажи дз','покажи домашку','что задали','что задали на дом','что задали на завтра',
    'какая домашка','какое дз','какая домашка на завтра','какая на завтра домашка',
    'скажи дз','скажите дз','скажите домашку','скажи домашку',
    'бот дз','бот, дз','бот, скинь дз','бот скинь дз','бот дай дз',
    'домашка','домашнее задание','дз','дз пж','дз пожалуйста','дз плиз'
]

def is_homework_request_and_extract_subject(text: str) -> Tuple[bool, Optional[str]]:
    t = text.lower().strip()
    t_clean = re.sub(r'[^\w\s\-]', '', t)
    subj = find_subject_in_text(t)
    if subj:
        if 'по ' in t or 'есть' in t or '?' in text or 'какая' in t or 'что' in t:
            return True, subj
        if any(token in t for token in ['дз','домашк','домашн']):
            return True, subj
    for token in REQUEST_TOKENS:
        if t_clean == token or t == token:
            return True, None
    words = t.split()
    if len(words) <= 6:
        for token in REQUEST_TOKENS:
            if token.split()[0] in words or any(w in token for w in words):
                if any(k in t for k in ['дз','домашк','что','скажи','покажи','скинь','отправ']):
                    return True, None
    if re.search(r'кака[яе]\s+домаш', t) or re.search(r'како[йе]\s+дз', t):
        return True, subj
    return False, None

# ---------------- Telegram bot logic ----------------
PENDING_PHOTOS = {}  # user_id -> {'file_id':..., 'caption':...}
GLOBAL_TELEGRAM_APP: Optional[Application] = None

def format_homework_list(rows: List[Tuple]) -> str:
    if not rows:
        return "📚 Домашних заданий нет."
    out = "📚 *ДОМАШНИЕ ЗАДАНИЯ*\n\n"
    by_day = {}
    for (hid, subj, task, day, time_str, photo, created) in rows:
        by_day.setdefault(day, []).append((hid, subj, task, time_str, photo, created))
    days_order = ['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье']
    for day in days_order:
        if day in by_day:
            out += f"*🗓 {day.upper()}*\n"
            for hid, subj, task, time_str, photo, created in by_day[day]:
                photo_mark = " 📸" if photo else ""
                out += f"▫️ *{subj}*{photo_mark} ({time_str})\n```\n{task}\n```\n\n"
            out += "━━━━━━━━━━━━━━━━\n\n"
    return out

async def send_homework_all(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_homework()
    text = format_homework_list(rows)
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=text)

async def send_homework_subject(chat_id: int, subject: str, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_homework()
    matches = []
    for row in rows:
        hid, subj, task, day, time_str, photo, created = row
        if subject.lower() in subj.lower() or subj.lower() in subject.lower():
            matches.append(row)
    if not matches:
        await context.bot.send_message(chat_id=chat_id, text=f"По *{subject.title()}* нет ДЗ 👍", parse_mode='Markdown')
        return
    text = format_homework_list(matches)
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')

# Custom start with greetings
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid == ADMIN_ID:
        greeting = "👑 Рад видеть владельца системы! Готов к работе.\n"
    elif uid == 6111166190:
        greeting = "✨ Рад вас видеть, Анастасия!\n"
    elif uid == 6955239802:
        greeting = "🔱 Рад вас видеть, Главный Следящий за Бесями — Анжелика Михайловна!\n"
    else:
        greeting = (
            "👋 Приветствую вас заблудшая душа!\n\n"
            "Вы не являетесь администратором. Вам доступно:\n"
            "1. Посмотреть домашку — напишите *дз* или */dz*\n"
            "2. Создать тикет обращение — */ticket*\n"
        )
        await update.message.reply_text(greeting, parse_mode="Markdown")
        return

    keyboard = [
        ["📚 Домашние задания", "📅 Расписание на сегодня"],
        ["➡️ Следующий урок"]
    ]
    if uid == ADMIN_ID:
        keyboard.append(["➕ Добавить ДЗ", "📄 Спарсить ДЗ"])

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        greeting +
        "\n📌 Основные команды:\n"
        "/dz — список ДЗ\n"
        "Можно спросить: *бот дз*, *какая домашка по физике?*\n"
        "/ticket — создать тикет обращения\n",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = "🆘 *Помощь — команды и подсказки:*\n\n"
    text += "• /start — меню\n• /dz — показать текущие ДЗ\n• /add_dz <текст> — добавить ДЗ (админ в ЛС)\n• /parse_dz <текст> — парсинг (админ)\n• /ticket — создать тикет (не-админам)\n\n"
    text += "Можно просто написать фразы вроде: 'скажи дз', 'какая домашка по английскому', 'скиньте дз' — бот поймёт и ответит.\n"
    if uid == ADMIN_ID:
        text += "\n*Админские быстрые команды (натуральный язык):*\n"
        text += "• 'удали геометрия' — удалит ДЗ по предмету\n• 'очисти все дз' — удалит все\n• 'добавь дз Алгебра - упр 5' — добавит быстро\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        await update.message.reply_text("📸 Фото можно отправлять в личные сообщения боту (админу).")
        return
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ Только админ может прикреплять фото к ДЗ.")
        return
    photo_file_id = update.message.photo[-1].file_id
    caption = update.message.caption or ""
    PENDING_PHOTOS[uid] = {'file_id': photo_file_id, 'caption': caption}
    await update.message.reply_text("✅ Фото сохранено. Следующее сообщение с текстом будет добавлено как ДЗ, а фото прикрепится к первому предмету.")

async def add_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для этой команды.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Команду используйте в ЛС с ботом.")
        return
    text = ""
    if context.args:
        text = " ".join(context.args)
    else:
        raw = update.message.text or ""
        if raw.startswith("/add_dz"):
            text = raw[len("/add_dz"):].strip()
    if not text:
        await update.message.reply_text("📝 Использование: /add_dz Текст с ДЗ или отправь фото и подпись.")
        return
    await process_and_add_hw_from_text(update, context, text)

async def parse_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав.")
        return
    if not context.args:
        await update.message.reply_text("❗ Использование: /parse_dz Текст")
        return
    text = " ".join(context.args)
    hw = parse_any_format(text)
    if not hw:
        await update.message.reply_text("❌ Не удалось распарсить.")
        return
    reply = "🔎 Результат парсинга:\n"
    for subj, task in hw:
        reply += f"• {subj}: {task}\n"
    await update.message.reply_text(reply)

async def dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.list_homework()
    text = format_homework_list(rows)
    await update.message.reply_text(text, parse_mode='Markdown')

# Admin natural-language commands
async def handle_admin_natural(update: Update, context: ContextTypes.DEFAULT_TYPE, text_lower:str):
    uid = update.effective_user.id
    if text_lower.startswith(("удали все","удалить все","очисти все","очисти всё","удали всё")):
        cnt = db.clear_all()
        await update.message.reply_text(f"🗑️ Очищено: {cnt} записей.")
        return True
    for prefix in ('удали ','удалить ','очисти ','очисть '):
        if text_lower.startswith(prefix):
            rest = text_lower[len(prefix):].strip()
            if rest in ('все','всё','все дз','все задания'):
                cnt = db.clear_all()
                await update.message.reply_text(f"🗑️ Очищено: {cnt} записей.")
                return True
            rest_clean = rest.replace('предмет ','').strip()
            subj_norm = normalize_subject(rest_clean)
            deleted = db.delete_by_subject(subj_norm)
            if deleted:
                await update.message.reply_text(f"✅ Удалено {deleted} заданий по предмету *{subj_norm}*", parse_mode='Markdown')
            else:
                await update.message.reply_text(f"⚠️ Не найдено заданий по предмету *{subj_norm}*", parse_mode='Markdown')
            return True
    for prefix in ('добавь ','добавить '):
        if text_lower.startswith(prefix):
            rest = update.message.text[len(prefix):].strip()
            await process_and_add_hw_from_text(update, context, rest)
            return True
    return False

# Main text handler
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return
    text_lower = text.lower()

    # If user is in tickets.create waiting list, let tickets module handle message first
    if tickets.is_waiting_for_ticket(uid):
        await tickets.ticket_message_handler(update, context)
        return

    # Admin natural commands in private
    if uid == ADMIN_ID and update.effective_chat.type == "private":
        handled = await handle_admin_natural(update, context, text_lower)
        if handled:
            return

    # Smart detection: is it a homework request? and extract subject if present
    is_req, subj = is_homework_request_and_extract_subject(text)
    if is_req:
        if subj:
            await send_homework_subject(update.effective_chat.id, subj, context)
            return
        await send_homework_all(update.effective_chat.id, context)
        return

    # Buttons handling
    if text in ("📅 Расписание на сегодня", "расписание"):
        today = schedule_manager.get_current_day()
        await update.message.reply_text(f"📅 Сегодня: {today}\n(Полное расписание — в веб-панели)")
        return
    if text in ("➡️ Следующий урок","следующий урок","что дальше"):
        await update.message.reply_text("⏰ Отправь название предмета, и я постараюсь найти следующий урок.")
        return
    if text in ("📚 Домашние задания","домашние задания","покажи дз"):
        await send_homework_all(update.effective_chat.id, context)
        return

    # Admin auto-add in private
    if uid == ADMIN_ID and update.effective_chat.type == "private":
        hw = parse_any_format(text)
        if hw:
            await process_and_add_hw_from_text(update, context, text)
            return

    # If admin is replying to a ticket answer (tickets module uses admin_reply_waiting)
    if tickets.is_admin_waiting_reply(uid):
        await tickets.admin_send_reply(update, context)
        return

    # private help / group hint
    if update.effective_chat.type == "private":
        await update.message.reply_text("🤖 Не распознал запрос. Для показа ДЗ напиши 'бот дз' или 'какая домашка по <предмет>'. /help")
    else:
        if re.search(r'\b' + re.escape((context.bot.username or 'бот')) + r'\b', text_lower):
            await update.message.reply_text("Напишите 'бот дз' или 'какая домашка по <предмет>' чтобы получить домашние задания.")
        # else ignore to avoid spam

async def process_and_add_hw_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    uid = update.effective_user.id
    hw_list = parse_any_format(text)
    if not hw_list:
        await update.message.reply_text("❌ Не удалось распознать домашние задания в тексте.")
        return
    results = []
    photo_entry = PENDING_PHOTOS.get(uid)
    for idx, (subj, task) in enumerate(hw_list):
        subj_norm = normalize_subject(subj)
        next_lesson = schedule_manager.find_next_lesson(subj_norm)
        if not next_lesson:
            fallback_day = schedule_manager.get_current_day()
            fallback_time = "00:00"
            hid = db.add_homework(subj_norm, task, fallback_day, fallback_time, photo_entry['file_id'] if photo_entry and idx==0 else None)
            results.append((hid, subj_norm, fallback_day, fallback_time, False))
        else:
            hid = db.add_homework(subj_norm, task, next_lesson['day'], next_lesson['time'], photo_entry['file_id'] if photo_entry and idx==0 else None)
            results.append((hid, subj_norm, next_lesson['day'], next_lesson['time'], True))
    if uid in PENDING_PHOTOS:
        del PENDING_PHOTOS[uid]
    reply = "📋 *Результат добавления ДЗ:*\n\n"
    for hid, subj, day, time_str, found in results:
        if found:
            reply += f"✅ *{subj}* → {day} ({time_str})\n"
        else:
            reply += f"⚠️ *{subj}* — урок не найден, записано как {day} {time_str}\n"
    await update.message.reply_text(reply, parse_mode='Markdown')

# ---------------- Scheduler: auto-cleanup ----------------
scheduler = BackgroundScheduler()

def cleanup_task():
    try:
        cleaned = db.cleanup_old_homework()
        if cleaned:
            logger.info("Cleanup removed subjects: %s", cleaned)
            if GLOBAL_TELEGRAM_APP:
                async def notify():
                    try:
                        msg = f"🗑️ Авто-очистка: удалены ДЗ по предметам:\n" + "\n".join(f"• {s}" for s in cleaned)
                        await GLOBAL_TELEGRAM_APP.bot.send_message(chat_id=ADMIN_ID, text=msg)
                    except Exception as e:
                        logger.exception("Failed to send cleanup notification: %s", e)
                try:
                    GLOBAL_TELEGRAM_APP.create_task(notify())
                except Exception:
                    import asyncio
                    asyncio.create_task(notify())
    except Exception as e:
        logger.exception("Error in cleanup_task: %s", e)

scheduler.add_job(cleanup_task, 'interval', minutes=10, next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=10))
scheduler.start()

# ---------------- Flask web UI (simple panel) ----------------
BASE_CSS = """
<style>
body{font-family:Arial,Helvetica,sans-serif; margin:20px; background:#f7f7f9;}
h2{color:#222}
table{border-collapse:collapse; width:100%}
th,td{border:1px solid #ddd; padding:8px}
th{background:#343a40; color:white}
tr:nth-child(even){background:#f2f2f2}
a.button{display:inline-block;padding:6px 10px;background:#007bff;color:white;text-decoration:none;border-radius:4px}
.form-row{margin-bottom:10px}
.notice{padding:10px;background:#e9ecef;border-radius:6px;margin-bottom:12px}
.small{font-size:0.9em;color:#555}
</style>
"""

LOGIN_HTML = BASE_CSS + """
<h2>SchoolBot — Вход в панель</h2>
<form method=post>
  <div class="form-row"><input type=password name=password placeholder="Пароль" style="width:300px;padding:8px"></div>
  <div class="form-row"><input type=submit value="Войти" class="button"></div>
</form>
"""

HOME_HTML = BASE_CSS + """
<h2>Панель управления SchoolBot</h2>
<p><a class="button" href="/add">Добавить ДЗ</a> <a class="button" href="/delete_by_subject">Удалить по предмету</a> <a class="button" href="/clear_all">Очистить все</a> <a class="button" href="/export">Экспорт CSV</a> <a class="button" href="/tickets">Тикеты</a> <a class="button" href="/logout">Выйти</a></p>
<div class="notice">Всего записей: {{count}}. Фото можно отправить админу: нажми "Отправить фото админу" рядом с записью.</div>
<table>
<tr><th>ID</th><th>Предмет</th><th>Задание</th><th>День</th><th>Время</th><th>Фото</th><th>Действия</th></tr>
{% for row in items %}
<tr>
  <td>{{row[0]}}</td>
  <td>{{row[1]}}</td>
  <td><pre style="white-space:pre-wrap;">{{row[2]}}</pre></td>
  <td>{{row[3]}}</td>
  <td>{{row[4]}}</td>
  <td>{{ 'Да' if row[5] else 'Нет' }}</td>
  <td>
    <a href="/delete/{{row[0]}}">Удалить</a>
    {% if row[5] %}
      | <a href="/send_photo/{{row[0]}}">Отправить фото админу</a>
    {% endif %}
  </td>
</tr>
{% endfor %}
</table>
"""

ADD_HTML = BASE_CSS + """
<h2>Добавить ДЗ вручную</h2>
<form method=post>
  <div class="form-row">Предмет: <input name=subject style="width:300px;padding:6px"></div>
  <div class="form-row">Задание: <textarea name=task rows=4 style="width:100%"></textarea></div>
  <div class="form-row">День (пример: вторник): <input name=day style="width:200px;padding:6px"></div>
  <div class="form-row">Время (HH:MM): <input name=time style="width:120px;padding:6px"></div>
  <div class="form-row">Photo file_id (необязательно): <input name=photo style="width:300px;padding:6px"></div>
  <div class="form-row"><input type=submit value="Добавить" class="button"></div>
</form>
<p><a href="/">Назад</a></p>
"""

DELETE_BY_SUBJ_HTML = BASE_CSS + """
<h2>Удалить по предмету</h2>
<form method=post>
  <div class="form-row">Предмет (или опечатка): <input name=subject style="width:300px;padding:6px"></div>
  <div class="form-row"><input type=submit value="Удалить" class="button"></div>
</form>
<p><a href="/">Назад</a></p>
"""

TICKETS_HTML = BASE_CSS + """
<h2>Тикеты (из модуля)</h2>
<p><a href="/">Назад</a></p>
<table>
<tr><th>ID</th><th>Автор (id)</th><th>Текст</th><th>Статус</th><th>Ответ</th></tr>
{% for t in tickets %}
<tr>
  <td>{{t['id']}}</td>
  <td>{{t['user_id']}}</td>
  <td><pre style="white-space:pre-wrap;">{{t['text']}}</pre></td>
  <td>{{t['status']}}</td>
  <td><pre style="white-space:pre-wrap;">{{t['admin_response'] or ''}}</pre></td>
</tr>
{% endfor %}
</table>
"""

CONFIRM_HTML = BASE_CSS + """
<h2>Готово</h2>
<p class="notice">{{msg}}</p>
<p><a href="/">Назад</a></p>
"""

def require_login(fn):
    def wrapper(*args, **kwargs):
        token = request.cookies.get("session")
        if not token or token not in WEB_SESSIONS:
            return redirect(url_for("login"))
        if WEB_SESSIONS[token] < datetime.datetime.now():
            del WEB_SESSIONS[token]
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password","")
        if pw == WEB_PASS:
            token = os.urandom(16).hex()
            WEB_SESSIONS[token] = datetime.datetime.now() + datetime.timedelta(hours=8)
            resp = make_response(redirect(url_for("home")))
            resp.set_cookie("session", token, max_age=60*60*8)
            return resp
        else:
            return render_template_string(LOGIN_HTML + "<p style='color:red'>Неверный пароль</p>")
    return render_template_string(LOGIN_HTML)

@app.route("/logout")
def logout():
    token = request.cookies.get("session")
    if token and token in WEB_SESSIONS:
        del WEB_SESSIONS[token]
    resp = make_response(redirect(url_for("login")))
    resp.set_cookie("session", "", expires=0)
    return resp

@app.route("/")
@require_login
def home():
    items = db.list_homework()
    return render_template_string(HOME_HTML, items=items, count=len(items))

@app.route("/add", methods=["GET","POST"])
@require_login
def add_hw():
    if request.method == "POST":
        subject = request.form.get("subject","").strip()
        task = request.form.get("task","").strip()
        day = request.form.get("day","").strip().lower()
        time = request.form.get("time","").strip()
        photo = request.form.get("photo","").strip() or None
        if not subject or not task or not day or not time:
            return render_template_string(ADD_HTML + "<p style='color:red'>Заполни все поля</p>")
        subj_norm = normalize_subject(subject)
        db.add_homework(subj_norm, task, day, time, photo)
        return render_template_string(CONFIRM_HTML, msg="Добавлено")
    return render_template_string(ADD_HTML)

@app.route("/delete/<int:hid>")
@require_login
def delete_hw(hid):
    ok = db.delete_homework(hid)
    msg = "Удалено" if ok else "Не найдено"
    return render_template_string(CONFIRM_HTML, msg=msg)

@app.route("/delete_by_subject", methods=["GET","POST"])
@require_login
def delete_by_subject():
    if request.method == "POST":
        subj = request.form.get("subject","").strip()
        if not subj:
            return render_template_string(DELETE_BY_SUBJ_HTML + "<p style='color:red'>Укажи предмет</p>")
        subj_norm = normalize_subject(subj)
        cnt = db.delete_by_subject(subj_norm)
        return render_template_string(CONFIRM_HTML, msg=f"Удалено {cnt} записей по предмету {subj_norm}")
    return render_template_string(DELETE_BY_SUBJ_HTML)

@app.route("/clear_all")
@require_login
def clear_all():
    cnt = db.clear_all()
    return render_template_string(CONFIRM_HTML, msg=f"Очищено {cnt} записей")

@app.route("/export")
@require_login
def export_csv():
    items = db.list_homework()
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id','subject','task','day','time','photo_file_id','created_at'])
    cw.writerows(items)
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=homework_export.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output

@app.route("/send_photo/<int:hid>")
@require_login
def send_photo_admin(hid):
    row = db.get_homework(hid)
    if not row:
        return render_template_string(CONFIRM_HTML, msg="Запись не найдена")
    photo_file_id = row[5]
    if not photo_file_id:
        return render_template_string(CONFIRM_HTML, msg="Фото не присоединено")
    if GLOBAL_TELEGRAM_APP:
        async def sendit():
            try:
                await GLOBAL_TELEGRAM_APP.bot.send_photo(chat_id=ADMIN_ID, photo=photo_file_id, caption=f"Фото из записи #{hid} ({row[1]})")
            except Exception as e:
                logger.exception("Failed to send photo to admin: %s", e)
        try:
            GLOBAL_TELEGRAM_APP.create_task(sendit())
        except Exception:
            import asyncio
            asyncio.create_task(sendit())
        return render_template_string(CONFIRM_HTML, msg="Фото отправлено админу")
    else:
        return render_template_string(CONFIRM_HTML, msg="Телеграм бот ещё не готов")

@app.route("/tickets")
@require_login
def tickets_page():
    # get tickets from tickets module
    tlist = tickets.get_all_tickets()
    return render_template_string(TICKETS_HTML, tickets=tlist)

# Flask runner
def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# ---------------- Run both bot and flask ----------------
def start_flask_thread():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    logger.info("Flask started on background thread")

def run_bot_and_flask():
    global GLOBAL_TELEGRAM_APP
    start_flask_thread()
    application = Application.builder().token(BOT_TOKEN).build()
    GLOBAL_TELEGRAM_APP = application

    # register handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("dz", dz_cmd))
    application.add_handler(CommandHandler("add_dz", add_dz_cmd))
    application.add_handler(CommandHandler("parse_dz", parse_dz_cmd))

    # tickets handlers (from separate module)
    application.add_handler(CommandHandler("ticket", tickets.ticket_command))
    application.add_handler(CallbackQueryHandler(tickets.ticket_callback))
    # message handler for tickets: ticket module expects to receive non-command messages (ticket content or admin reply)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tickets.integrated_message_handler))
    # photo and text handlers for other flows should come after ticket handler to allow ticket module to intercept when needed
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # now general text handler (placed after ticket handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Starting Telegram polling...")
    application.run_polling(stop_signals=None)

if __name__ == "__main__":
    try:
        run_bot_and_flask()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
