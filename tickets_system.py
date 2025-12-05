# tickets_system.py
"""
Tickets system module.
Keeps its own SQLite DB (tickets.db).
Provides:
 - /ticket command for users (non-admins): prompts for text then creates ticket
 - Sends ticket to admins with inline buttons: Accept / Decline / Answer
 - Accept/Decline: notifies author
 - Answer: admin types reply (next message) and it sends to author
 - Admin reply flow tracked per-admin (in-memory)
Usage:
 - Main bot must register:
   CommandHandler("ticket", tickets.ticket_command)
   CallbackQueryHandler(tickets.ticket_callback)
   MessageHandler(filters.TEXT & ~filters.COMMAND, tickets.integrated_message_handler)
"""

import sqlite3
import datetime
import logging
from typing import Optional, Dict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger("tickets_system")

DB_PATH = "tickets.db"
ADMIN_LIST = [6193109213, 6111166190, 6955239802]  # update if needed

class TicketsDB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._create()

    def _create(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                status TEXT,
                admin_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def create_ticket(self, user_id: int, text: str) -> int:
        cur = self.conn.cursor()
        cur.execute("INSERT INTO tickets (user_id, text, status) VALUES (?, ?, ?)", (user_id, text, "open"))
        self.conn.commit()
        return cur.lastrowid

    def get_ticket(self, tid: int):
        cur = self.conn.cursor()
        cur.execute("SELECT id, user_id, text, status, admin_response, created_at FROM tickets WHERE id=?", (tid,))
        return cur.fetchone()

    def set_status(self, tid: int, status: str):
        cur = self.conn.cursor()
        cur.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid))
        self.conn.commit()

    def set_admin_response(self, tid: int, response: str):
        cur = self.conn.cursor()
        cur.execute("UPDATE tickets SET admin_response=? WHERE id=?", (response, tid))
        self.conn.commit()

    def list_all(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, user_id, text, status, admin_response, created_at FROM tickets ORDER BY created_at DESC")
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r[0], "user_id": r[1], "text": r[2], "status": r[3], "admin_response": r[4], "created_at": r[5]
            })
        return result

tickets_db = TicketsDB()

# in-memory waiting states
WAITING_FOR_TICKET_TEXT: Dict[int, bool] = {}      # user_id -> True if we expect their ticket text
ADMIN_WAITING_REPLY: Dict[int, int] = {}          # admin_id -> ticket_id (waiting for admin's next message to send to ticket author)

# Utilities used by main app to check states
def is_waiting_for_ticket(user_id: int) -> bool:
    return WAITING_FOR_TICKET_TEXT.get(user_id, False)

def is_admin_waiting_reply(admin_id: int) -> bool:
    return admin_id in ADMIN_WAITING_REPLY

# Command handler: /ticket
async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # allow non-admins only? We'll allow everyone, but admins get message
    if uid in ADMIN_LIST:
        await update.message.reply_text("⚠️ Админы не создают тикеты. Используйте от имени пользователя.")
        return
    WAITING_FOR_TICKET_TEXT[uid] = True
    await update.message.reply_text("📝 Опишите вашу проблему/идею/вопрос в следующем сообщении. Ожидаю...")

# Message handler integration:
# - If user is waiting_for_ticket -> create ticket
# - Else if admin is waiting reply -> send admin reply to ticket author
# - Else -> return False to indicate not handled (main bot will continue)
async def integrated_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return  # nothing

    # If user is sending ticket text
    if WAITING_FOR_TICKET_TEXT.get(uid):
        # create ticket
        tid = tickets_db.create_ticket(uid, text)
        WAITING_FOR_TICKET_TEXT.pop(uid, None)
        await update.message.reply_text("✅ Отлично! Ваш запрос отправлен на рассмотрение.")
        # notify admins
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✔ Принять", callback_data=f"ticket_acc_{tid}"),
             InlineKeyboardButton("❌ Отказать", callback_data=f"ticket_dec_{tid}")],
            [InlineKeyboardButton("💬 Ответить", callback_data=f"ticket_ans_{tid}")]
        ])
        for admin in ADMIN_LIST:
            try:
                await context.bot.send_message(chat_id=admin,
                    text=f"📨 *Новый тикет #{tid}*\n\n{text}",
                    parse_mode='Markdown',
                    reply_markup=keyboard)
            except Exception as e:
                logger.exception("Failed to notify admin %s: %s", admin, e)
        return

    # If admin is expected to reply to a ticket
    if uid in ADMIN_WAITING_REPLY:
        tid = ADMIN_WAITING_REPLY.pop(uid)
        ticket = tickets_db.get_ticket(tid)
        if not ticket:
            await update.message.reply_text("❌ Тикет не найден или уже закрыт.")
            return
        author_id = ticket[1]
        # send admin's message to author
        try:
            await context.bot.send_message(chat_id=author_id, text=f"📬 Ответ администратора (по тикету #{tid}):\n\n{update.message.text}")
            await update.message.reply_text("✅ Ответ отправлен пользователю.")
            # save admin response in DB
            tickets_db.set_admin_response(tid, update.message.text)
            tickets_db.set_status(tid, "answered")
        except Exception as e:
            logger.exception("Failed to send admin reply: %s", e)
            await update.message.reply_text("❌ Не удалось отправить ответ пользователю.")
        return

    # else: not handled here; main bot should continue processing

# CallbackQuery handler for inline buttons
async def ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    # data format: ticket_acc_<id>, ticket_dec_<id>, ticket_ans_<id>
    parts = data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("Неправильная команда.")
        return
    action = parts[1]
    try:
        tid = int(parts[2])
    except:
        await query.edit_message_text("Неверный ID тикета.")
        return

    ticket = tickets_db.get_ticket(tid)
    if not ticket:
        await query.edit_message_text("Тикет не найден или уже обработан.")
        return

    admin_id = query.from_user.id

    if action == "acc":
        tickets_db.set_status(tid, "accepted")
        # notify author
        author_id = ticket[1]
        try:
            await context.bot.send_message(chat_id=author_id, text=f"🎉 Ваш тикет #{tid} был принят администрацией.")
        except Exception:
            logger.exception("Failed to notify author about accepted ticket.")
        await query.edit_message_text(f"Тикет #{tid} отмечен как *Принят*.", parse_mode='Markdown')
    elif action == "dec":
        tickets_db.set_status(tid, "declined")
        author_id = ticket[1]
        try:
            await context.bot.send_message(chat_id=author_id, text=f"❗ Ваш тикет #{tid} был отклонён администрацией.")
        except Exception:
            logger.exception("Failed to notify author about declined ticket.")
        await query.edit_message_text(f"Тикет #{tid} отмечен как *Отклонён*.", parse_mode='Markdown')
    elif action == "ans":
        # set admin in waiting state
        ADMIN_WAITING_REPLY[admin_id] = tid
        await query.edit_message_text(f"Напишите ответ на тикет #{tid} в следующем сообщении — он будет отправлен автору.")
    else:
        await query.edit_message_text("Неизвестная операция.")

# Helpers for main to check states
def is_waiting_for_ticket(user_id: int) -> bool:
    return WAITING_FOR_TICKET_TEXT.get(user_id, False)

def is_admin_waiting_reply(admin_id: int) -> bool:
    return admin_id in ADMIN_WAITING_REPLY

async def admin_send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # wrapper that main bot calls when admin is expected to reply
    uid = update.effective_user.id
    if uid not in ADMIN_WAITING_REPLY:
        return
    tid = ADMIN_WAITING_REPLY.pop(uid)
    ticket = tickets_db.get_ticket(tid)
    if not ticket:
        await update.message.reply_text("❌ Тикет не найден.")
        return
    author_id = ticket[1]
    text = update.message.text or ""
    if not text:
        await update.message.reply_text("❌ Текст ответа пустой.")
        return
    try:
        await context.bot.send_message(chat_id=author_id, text=f"📬 Ответ администратора (тикет #{tid}):\n\n{text}")
        tickets_db.set_admin_response(tid, text)
        tickets_db.set_status(tid, "answered")
        await update.message.reply_text("✅ Ответ отправлен пользователю.")
    except Exception as e:
        logger.exception("Failed to send admin reply: %s", e)
        await update.message.reply_text("❌ Не удалось отправить ответ.")

def get_all_tickets():
    return tickets_db.list_all()
