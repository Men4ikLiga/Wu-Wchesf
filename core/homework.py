# core/homework.py
import os
import sqlite3
import datetime
from typing import List, Tuple, Optional
import config

DB_PATH = config.DATABASE
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
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
conn.commit()

def add_homework(subject: str, task: str, day: Optional[str] = None, time: Optional[str] = None, photo_file_id: Optional[str] = None) -> int:
    # guess next lesson if not provided
    if not day or not time:
        from core.schedule import schedule_manager
        nl = schedule_manager.find_next_lesson(subject)
        if nl:
            day = nl['day']; time = nl['time']
        else:
            now = datetime.datetime.now()
            day = schedule_manager.get_current_day() if 'schedule_manager' in globals() else 'понедельник'
            time = now.strftime("%H:%M")
    cur.execute("INSERT INTO homework (subject, task, day, time, photo_file_id) VALUES (?,?,?,?,?)",
                (subject, task, day, time, photo_file_id))
    conn.commit()
    return cur.lastrowid

def list_homework() -> List[Tuple]:
    cur.execute("SELECT id, subject, task, day, time, photo_file_id, created_at FROM homework ORDER BY day, time")
    return cur.fetchall()

def search_by_subject(subject: str) -> List[Tuple]:
    subj = subject.lower()
    cur.execute("SELECT id, subject, task, day, time, photo_file_id, created_at FROM homework")
    rows = cur.fetchall()
    res = [r for r in rows if subj in r[1].lower() or r[1].lower() in subj]
    return res

def delete_by_id(hid: int) -> bool:
    cur.execute("DELETE FROM homework WHERE id=?", (hid,))
    conn.commit()
    return cur.rowcount > 0

def delete_by_subject(subject: str) -> int:
    cur.execute("DELETE FROM homework WHERE lower(subject)=lower(?)", (subject,))
    cnt = cur.rowcount
    conn.commit()
    return cnt

def clear_all() -> int:
    cur.execute("DELETE FROM homework")
    cnt = cur.rowcount
    conn.commit()
    return cnt

# photo handler for admins: save pending photo association via file_id
async def photo_handler(update, context):
    uid = update.effective_user.id
    if uid not in config.ADMINS:
        await update.message.reply_text("Только админ может отправлять фото для ДЗ.")
        return
    photo = update.message.photo[-1].file_id
    caption = update.message.caption or ""
    # if admin sends photo then follows with text, we'll attach photo to first parsed subject
    context.user_data['pending_photo'] = {'file_id': photo, 'caption': caption}
    await update.message.reply_text("Фото сохранено. Теперь отправь текст с ДЗ — фото прикрепится к первому предмету.")
