# modules/tickets.py
import sqlite3
import os
import datetime
from typing import Dict, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
import config

DB_PATH = config.TICKET_DB
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    category TEXT,
    text TEXT,
    status TEXT,
    admin_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# in-memory states
WAITING_FOR_TICKET_TEXT = {}   # user_id -> category or True
ADMIN_WAITING_REPLY = {}       # admin_id -> ticket_id

def is_waiting_for_ticket(user_id: int) -> bool:
    return WAITING_FOR_TICKET_TEXT.get(user_id, False)

def is_admin_waiting_reply(admin_id: int) -> bool:
    return admin_id in ADMIN_WAITING_REPLY

async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Allow OWNER to create too for testing
    if uid in config.ADMINS:
        await update.message.reply_text("Админы могут создавать тикеты, укажите категорию или нажмите Отмена.")
    await update.message.reply_text("Выберете категорию тикета: 1) Проблема 2) Идея 3) Вопрос\nОтправьте номер или название категории.")
    WAITING_FOR_TICKET_TEXT[uid] = "await_category"

async def integrated_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return
    state = WAITING_FOR_TICKET_TEXT.get(uid)
    if not state:
        return  # not a ticket flow
    if state == "await_category":
        # map choice
        cat = text.lower()
        if cat in ("1","проблема"):
            category = "Проблема"
        elif cat in ("2","идея"):
            category = "Идея"
        elif cat in ("3","вопрос"):
            category = "Вопрос"
        else:
            category = text.title()
        WAITING_FOR_TICKET_TEXT[uid] = category
        await update.message.reply_text("Теперь опишите подробно вашу проблему/идею/вопрос в следующем сообщении.")
        return
    # state contains category -> this message is ticket text
    category = state
    tid = create_ticket(uid, category, text)
    del WAITING_FOR_TICKET_TEXT[uid]
    await update.message.reply_text("✅ Ваш тикет зарегистрирован. Ожидайте ответа.")
    # notify admins
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✔ Принять", callback_data=f"ticket_acc_{tid}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"ticket_dec_{tid}")],
        [InlineKeyboardButton("💬 Ответить", callback_data=f"ticket_ans_{tid}")]
    ])
    for admin in config.ADMINS:
        try:
            await context.bot.send_message(chat_id=admin, text=f"📨 Новый тикет #{tid}\nКатегория: {category}\n\n{truncate(text)}", reply_markup=keyboard)
        except Exception:
            pass

def create_ticket(user_id: int, category: str, text: str) -> int:
    cur.execute("INSERT INTO tickets (user_id, category, text, status) VALUES (?,?,?,?)", (user_id, category, text, "open"))
    conn.commit()
    return cur.lastrowid

def truncate(s: str, n=500):
    return s if len(s)<=n else s[:n]+"..."

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("Неверная команда.")
        return
    action = parts[1]; tid = int(parts[2])
    ticket = get_ticket(tid)
    if not ticket:
        await query.edit_message_text("Тикет не найден.")
        return
    if action == "acc":
        set_status(tid, "accepted")
        await query.edit_message_text(f"Тикет #{tid} принят.")
        # notify author
        try:
            await context.bot.send_message(chat_id=ticket['user_id'], text=f"Ваш тикет #{tid} принят администрацией.")
        except Exception:
            pass
    elif action == "dec":
        set_status(tid, "declined")
        await query.edit_message_text(f"Тикет #{tid} отклонён.")
        try:
            await context.bot.send_message(chat_id=ticket['user_id'], text=f"Ваш тикет #{tid} отклонён администрацией.")
        except Exception:
            pass
    elif action == "ans":
        # set admin waiting
        ADMIN_WAITING_REPLY[query.from_user.id] = tid
        await query.edit_message_text(f"Напишите ответ на тикет #{tid} в следующем сообщении — он будет отправлен автору.")
    else:
        await query.edit_message_text("Неизвестное действие.")

def get_ticket(tid: int):
    cur.execute("SELECT id, user_id, category, text, status, admin_response, created_at FROM tickets WHERE id=?", (tid,))
    row = cur.fetchone()
    if not row:
        return None
    return {'id':row[0],'user_id':row[1],'category':row[2],'text':row[3],'status':row[4],'admin_response':row[5],'created_at':row[6]}

def set_status(tid: int, status: str):
    cur.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid))
    conn.commit()

def set_admin_response(tid: int, response: str):
    cur.execute("UPDATE tickets SET admin_response=? WHERE id=?", (response, tid))
    conn.commit()

async def admin_send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user.id
    if admin not in ADMIN_WAITING_REPLY:
        return
    tid = ADMIN_WAITING_REPLY.pop(admin)
    ticket = get_ticket(tid)
    if not ticket:
        await update.message.reply_text("Тикет не найден.")
        return
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Пустой ответ.")
        return
    # send to author
    try:
        await context.bot.send_message(chat_id=ticket['user_id'], text=f"Ответ по тикету #{tid}:\n\n{text}")
        set_admin_response(tid, text)
        set_status(tid, "answered")
        await update.message.reply_text("Ответ отправлен.")
    except Exception:
        await update.message.reply_text("Не удалось отправить ответ.")
