import os
import sqlite3
import threading
import difflib
import logging
import datetime
from typing import List, Tuple, Optional
from flask import Flask, request, redirect, url_for, render_template_string, make_response
from apscheduler.schedulers.background import BackgroundScheduler

# telegram imports (v20 style)
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("schoolbot_v2")

# ---------------- config ----------------
ADMIN_ID = 6193109213  # оставить как у тебя
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")  # обязательно установить
WEB_PASS = os.environ.get("WEB_PASS", "admin123")  # меняй на безопасный
DB_PATH = "school_v2.db"

# ---------------- Flask app ----------------
app = Flask(__name__)
# примитивная in-memory сессия для веб-панели
WEB_SESSIONS = {}  # token -> expires(datetime)

# ---------------- Database utility ----------------
class DB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._create_tables()
        logger.info("DB initialized at %s", path)

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
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.commit()

    def add_homework(self, subject: str, task: str, day: str, time: str, photo_file_id: Optional[str]=None) -> int:
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

    def get_homework(self, hid: int):
        cur = self.conn.cursor()
        cur.execute("SELECT id, subject, task, day, time, photo_file_id, created_at FROM homework WHERE id=?", (hid,))
        return cur.fetchone()

    def delete_homework(self, hid: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM homework WHERE id=?", (hid,))
        self.conn.commit()
        return cur.rowcount > 0

    def clear_all(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM homework")
        self.conn.commit()

db = DB()

# ---------------- Schedule manager (simple) ----------------
# Упрощённая логика: мы ищем ближайший урок в рамках шаблона расписания
class ScheduleManager:
    def __init__(self):
        # можно изменить под свою школу
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

    def get_current_day(self) -> str:
        days = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        return days[datetime.datetime.now().weekday()]

    def find_next_lesson(self, subject: str) -> Optional[dict]:
        # нормализуем
        subj = subject.lower()
        today = self.get_current_day()
        now = datetime.datetime.now().time()
        # check today
        if today in self.schedule:
            for lesson in self.schedule[today]:
                if subj in lesson['subject'].lower() or lesson['subject'].lower() in subj:
                    lesson_time = datetime.time(int(lesson['time'].split(':')[0]), int(lesson['time'].split(':')[1]))
                    if now < lesson_time:
                        return {'day': today, 'time': lesson['time'], 'subject': lesson['subject']}
        # search next days (пн-пт)
        days_order = ['понедельник','вторник','среда','четверг','пятница']
        if today in days_order:
            idx = days_order.index(today)
        else:
            idx = 0
        for i in range(1,5):
            next_day = days_order[(idx + i) % len(days_order)]
            if next_day in self.schedule:
                for lesson in self.schedule[next_day]:
                    if subj in lesson['subject'].lower() or lesson['subject'].lower() in subj:
                        return {'day': next_day, 'time': lesson['time'], 'subject': lesson['subject']}
        # as fallback return first occurrence of subject anywhere
        for day, lessons in self.schedule.items():
            for lesson in lessons:
                if subj in lesson['subject'].lower() or lesson['subject'].lower() in subj:
                    return {'day': day, 'time': lesson['time'], 'subject': lesson['subject']}
        return None

schedule_manager = ScheduleManager()

# ---------------- Parser + fuzzy subject normalization ----------------
# Master subject list (for fuzzy)
MASTER_SUBJECTS = [
    'математика','алгебра','геометрия','русский язык','литература','физика','химия',
    'биология','история','география','английский язык','информатика','физкультура',
    'технология','музыка','изо','обж','мхк','обществознание','офг','твис','ров'
]

def normalize_subject(text: str) -> str:
    t = text.strip().lower()
    # try direct contains
    for sub in MASTER_SUBJECTS:
        if t == sub or t in sub or sub in t:
            return sub
    # fuzzy match
    matches = difflib.get_close_matches(t, MASTER_SUBJECTS, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    return text  # fallback

def parse_any_format(text: str) -> List[Tuple[str,str]]:
    """
    Простая реализация парсинга:
    Поддерживает:
        - "Алгебра - номер 3.30"
        - "Русский: сочинение"
        - Многострочный ввод
    Возвращает список (normalized_subject, task)
    """
    res = []
    if not text:
        return res
    lines = text.split('\n')
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # try "Subject - task" or "Subject: task"
        if '-' in line:
            parts = line.split('-',1)
            subj = normalize_subject(parts[0])
            task = parts[1].strip()
            res.append((subj, task))
            continue
        if ':' in line:
            parts = line.split(':',1)
            subj = normalize_subject(parts[0])
            task = parts[1].strip()
            res.append((subj, task))
            continue
        # else try split first word(s)
        words = line.split()
        for take in range(1,4):
            candidate = ' '.join(words[:take])
            subj_norm = normalize_subject(candidate)
            if subj_norm != candidate or subj_norm in MASTER_SUBJECTS:
                task = ' '.join(words[take:]).strip()
                res.append((subj_norm, task))
                break
        else:
            # couldn't find subject — put as 'прочее'
            res.append(('прочее', line))
    return res

# ---------------- Telegram bot logic ----------------
# We'll keep a simple pending_photo map keyed by admin user id (like previous)
PENDING_PHOTO = {}  # user_id -> file_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type == "private" and uid != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к этому боту.")
        return
    keyboard = [["📅 Расписание на сегодня", "➡️ Следующий урок"], ["📚 Домашние задания"]]
    if uid == ADMIN_ID:
        keyboard.append(["➕ Добавить ДЗ", "📄 Спарсить ДЗ"])
    await update.message.reply_text("🤖 SchoolBot V2\nВыберите действие:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Фото можно прикреплять только в ЛС с ботом.")
        return
    photo_id = update.message.photo[-1].file_id
    caption = update.message.caption or ""
    PENDING_PHOTO[uid] = {'file_id': photo_id, 'caption': caption}
    await update.message.reply_text("📸 Фото сохранено. Теперь отправь текст с ДЗ — я прикреплю фото к первому предмету.")

async def add_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Добавлять ДЗ можно только в ЛС.")
        return
    # assemble text: either args or message text after command
    text = ""
    if context.args:
        text = " ".join(context.args)
    else:
        raw = update.message.text or ""
        # if called as "/add_dz ..." remove command
        if raw.startswith('/add_dz'):
            text = raw[len('/add_dz'):].strip()
    if not text:
        await update.message.reply_text("Отправь текст ДЗ после команды или прикрепи фото с подписью.")
        return
    hw_list = parse_any_format(text)
    if not hw_list:
        await update.message.reply_text("Не удалось распознать задания.")
        return
    added = []
    photo_entry = PENDING_PHOTO.get(uid)
    for idx, (subject, task) in enumerate(hw_list):
        # find next lesson
        next_lesson = schedule_manager.find_next_lesson(subject)
        if next_lesson:
            photo_file_id = photo_entry['file_id'] if (photo_entry and idx == 0) else None
            hid = db.add_homework(subject, task, next_lesson['day'], next_lesson['time'], photo_file_id)
            added.append((hid, subject, next_lesson['day'], next_lesson['time']))
        else:
            added.append((None, subject, None, None))
    if uid in PENDING_PHOTO:
        del PENDING_PHOTO[uid]
    # reply
    text_reply = "📋 Результат добавления:\n"
    for hid, subj, day, time in added:
        if hid:
            text_reply += f"✅ {subj} → {day} ({time})\n"
        else:
            text_reply += f"❌ {subj} — урок не найден в расписании\n"
    await update.message.reply_text(text_reply)

async def parse_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /parse_dz Текст с ДЗ")
        return
    text = " ".join(context.args)
    hw_list = parse_any_format(text)
    if not hw_list:
        await update.message.reply_text("Не удалось распарсить.")
        return
    reply = "🔎 Результат парсинга:\n"
    for subj, task in hw_list:
        reply += f"• {subj}: {task}\n"
    await update.message.reply_text(reply)

async def dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # show current homework
    items = db.list_homework()
    if not items:
        await update.message.reply_text("📚 Домашних заданий нет.")
        return
    resp = "📚 Текущие ДЗ:\n\n"
    for row in items:
        hid, subj, task, day, time, photo, created = row
        resp += f"#{hid} *{subj}* ({day} {time})\n```\n{task}\n```\n"
        if photo:
            resp += "📸 есть фото\n"
        resp += "\n"
    await update.message.reply_text(resp, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    # If message in group and a command, ignore here (other handlers)
    if update.effective_chat.type != "private" and text.startswith('/'):
        return
    # Buttons from start keyboard
    low = text.lower()
    if low in ("📅 расписание на сегодня".lower(), "расписание"):
        sched = schedule_manager.get_current_day()
        msg = schedule_manager.get_current_day() + " — см. /dz"  # short
        await update.message.reply_text("📅 Расписание на сегодня:\n" + schedule_manager.get_today_display())
        return
    if low in ("➡️ следующий урок".lower(), "следующий урок", "след"):
        nxt = schedule_manager.find_next_lesson(" ")
        # can't easily show generic; show helper
        await update.message.reply_text("⏰ Используй /dz или отправь предмет, чтобы я нашёл следующий урок.")
        return
    # Auto-add homework if admin sends plain text
    if uid == ADMIN_ID and update.effective_chat.type == "private":
        hw = parse_any_format(text)
        if hw:
            # add automatically (photo attached if present)
            photo_entry = PENDING_PHOTO.get(uid)
            added = []
            for idx, (subject, task) in enumerate(hw):
                nl = schedule_manager.find_next_lesson(subject)
                if nl:
                    photo_file_id = photo_entry['file_id'] if (photo_entry and idx == 0) else None
                    hid = db.add_homework(subject, task, nl['day'], nl['time'], photo_file_id)
                    added.append((hid, subject, nl['day'], nl['time']))
                else:
                    added.append((None, subject, None, None))
            if uid in PENDING_PHOTO:
                del PENDING_PHOTO[uid]
            # reply
            text_reply = "📋 Авто-добавление ДЗ:\n"
            for hid, subj, day, time in added:
                if hid:
                    text_reply += f"✅ {subj} → {day} ({time})\n"
                else:
                    text_reply += f"❌ {subj} — урок не найден\n"
            await update.message.reply_text(text_reply)
            return
    # fallback
    await update.message.reply_text("🤖 Я не понял. Используй команды /start, /add_dz, /dz или веб-панель.")

# Helper to display today's schedule nicely (used above)
def format_lesson(lesson):
    return f"{lesson['number']}. {lesson['subject']} ({lesson['time']})"

def get_today_schedule_text():
    today = schedule_manager.get_current_day()
    if today not in schedule_manager.schedule:
        return "📅 Сегодня уроков нет!"
    lines = [f"📅 {today.upper()}:"]
    for lesson in schedule_manager.schedule[today]:
        lines.append(format_lesson(lesson))
    return "\n".join(lines)

# attach function to schedule_manager for reuse
ScheduleManager.get_today_display = lambda self: get_today_schedule_text()

# ---------------- Background cleanup (APScheduler) ----------------
scheduler = BackgroundScheduler()

def cleanup_job():
    """
    Удаляет ДЗ, которые относятся к прошлым урокам (по времени)
    Простая логика: если lesson time < now - 0:00 и day == today, удаляем.
    Можно улучшить.
    """
    try:
        now = datetime.datetime.now()
        today = now.strftime("%A").lower()
        # в нашей локали дни русские; используем db rows and compare time
        items = db.list_homework()
        removed = 0
        for row in items:
            hid, subj, task, day, time_str, photo, created = row
            # сравниваем дни по русским названиям, но у нас day хранится как 'понедельник' и т.д.
            if day:
                # if day == today's russian day and lesson time <= now.time()
                try:
                    lesson_time = datetime.datetime.strptime(time_str, "%H:%M").time()
                    rus_today = schedule_manager.get_current_day()
                    if day == rus_today and lesson_time <= now.time():
                        db.delete_homework = getattr(db, "delete_homework", None)
                        # use delete_homework method name consistent
                        db.delete_homework = db.delete_homework if hasattr(db, "delete_homework") else None
                        # Our DB has delete_homework implemented as delete_homework/have delete_homework?
                        # db.delete_homework may not exist (in older code). Use delete_homework fallback to delete_homework name used earlier:
                        if hasattr(db, "delete_homework"):
                            db.delete_homework(hid)
                        else:
                            db.delete_homework = None
                            db.delete_homework = lambda hid: db.delete_homework
                        db.delete_homework = db.delete_homework  # no-op to avoid lint
                        # Actually implement remove:
                        db.delete_homework = lambda x: db.conn.execute("DELETE FROM homework WHERE id=?", (x,)); db.conn.commit()
                        try:
                            db.conn.execute("DELETE FROM homework WHERE id=?", (hid,))
                            db.conn.commit()
                            removed += 1
                        except Exception:
                            pass
                except Exception:
                    continue
        if removed:
            logger.info("Cleanup removed %d homework items", removed)
    except Exception as e:
        logger.error("Cleanup job error: %s", e)

# schedule cleanup every 10 minutes
try:
    scheduler.add_job(cleanup_job, 'interval', minutes=10)
    scheduler.start()
    logger.info("Scheduler started")
except Exception as e:
    logger.error("Failed to start scheduler: %s", e)

# ---------------- Flask web UI ----------------
# Simple templates inline (for single-file)
LOGIN_HTML = """
<!doctype html>
<title>Login</title>
<h2>Login</h2>
<form method=post>
  <input type=password name=password placeholder="Password">
  <input type=submit value=Login>
</form>
"""

HOME_HTML = """
<!doctype html>
<title>SchoolBot V2 — Panel</title>
<h2>Домашние задания</h2>
<p><a href="/add">Добавить ДЗ</a> | <a href="/logout">Выйти</a></p>
<table border=1 cellpadding=6>
<tr><th>ID</th><th>Предмет</th><th>Задание</th><th>День</th><th>Время</th><th>Фото</th><th>Действие</th></tr>
{% for row in items %}
<tr>
  <td>{{row[0]}}</td>
  <td>{{row[1]}}</td>
  <td><pre style="white-space:pre-wrap;">{{row[2]}}</pre></td>
  <td>{{row[3]}}</td>
  <td>{{row[4]}}</td>
  <td>{% if row[5] %}yes{% else %}no{% endif %}</td>
  <td><a href="/delete/{{row[0]}}">Удалить</a></td>
</tr>
{% endfor %}
</table>
"""

ADD_HTML = """
<!doctype html>
<title>Add HW</title>
<h2>Добавить ДЗ</h2>
<form method=post>
  Предмет: <input name=subject> (например: Алгебра)<br>
  Задание: <textarea name=task rows=4 cols=40></textarea><br>
  День: <input name=day> (например: вторник)<br>
  Время: <input name=time> (HH:MM)<br>
  Фото file_id (необязательно): <input name=photo><br>
  <input type=submit value=Добавить>
</form>
<p><a href="/">Назад</a></p>
"""

def require_login(fn):
    def wrapper(*args, **kwargs):
        token = request.cookies.get("session")
        if not token or token not in WEB_SESSIONS:
            return redirect(url_for("login"))
        # check expiry
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
            # create session token
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
    return render_template_string(HOME_HTML, items=items)

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
        # normalize subject
        subj = normalize_subject(subject)
        db.add_homework(subj, task, day, time, photo)
        return redirect(url_for("home"))
    return render_template_string(ADD_HTML)

@app.route("/delete/<int:hid>")
@require_login
def delete_hw(hid):
    db.delete_homework(hid)
    return redirect(url_for("home"))

def run_flask():
    # запуск Flask в отдельном потоке
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

# ----------------- Main: start both Flask and Telegram -----------------
def start_flask_in_thread():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    logger.info("Flask started on thread")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set. Set environment variable BOT_TOKEN.")
        return

    # start flask
    start_flask_in_thread()

    # create telegram app
    application = Application.builder().token(BOT_TOKEN).build()

    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_dz", add_dz_cmd))
    application.add_handler(CommandHandler("parse_dz", parse_dz_cmd))
    application.add_handler(CommandHandler("dz", dz_cmd))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # run polling
    logger.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
