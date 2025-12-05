# modules/tickets.py
import os
import sqlite3
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler, Filters, CommandHandler
from config import ADMINS, OWNER_ID, TICKET_DB

# ================== База тикетов ==================

def init_ticket_db():
    conn = sqlite3.connect(TICKET_DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        category TEXT
    )""")
    conn.commit()
    conn.close()

init_ticket_db()

# ================== Создание тикета ==================

def ticket_command(update, context):
    update.message.reply_text("✍ Отправьте текст проблемы/идеи одним сообщением.")

    return 1   # переход в ожидание текста

def get_ticket_text(update, context):
    text = update.message.text
    user_id = update.message.from_user.id

    conn = sqlite3.connect(TICKET_DB)
    c = conn.cursor()
    c.execute("INSERT INTO tickets (user_id, message, category) VALUES (?, ?, ?)",
              (user_id, text, "Проблема"))
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()

    update.message.reply_text("📨 Ваш тикет отправлен на рассмотрение! Ожидайте ответ.")

    # отправка админам
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✔ Принять", callback_data=f"accept_{ticket_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{ticket_id}")],
        [InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{ticket_id}")]
    ])

    for admin in ADMINS:
        context.bot.send_message(
            admin,
            f"📨 Новый тикет #{ticket_id}\n"
            f"От: `{user_id}`\n"
            f"Текст: {text}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    return ConversationHandler.END


# ================== Обработка кнопок действий ==================

def ticket_button(update, context):
    query = update.callback_query
    query.answer()

    data = query.data.split("_")
    action = data[0]
    ticket_id = int(data[1])

    # достаем тикет
    conn = sqlite3.connect(TICKET_DB)
    c = conn.cursor()
    c.execute("SELECT user_id, message, category FROM tickets WHERE id=?", (ticket_id,))
    ticket = c.fetchone()
    conn.close()

    if not ticket:
        return query.edit_message_text("❗ Тикет не найден.")

    user_id, message, category = ticket

    # принятие
    if action == "accept":
        query.edit_message_text(f"✔ Тикет #{ticket_id} принят!")
        context.bot.send_message(user_id, "Ваш тикет был принят ✔")
        return

    # отклонение
    if action == "reject":
        query.edit_message_text(f"❌ Тикет #{ticket_id} отклонён.")
        context.bot.send_message(user_id, "Ваш тикет отклонён ❌")
        return

    # ответ
    if action == "reply":
        query.edit_message_text(f"💬 Напишите ответ на тикет #{ticket_id}.")
        context.user_data["reply_ticket"] = ticket_id
        return 2   # режим ожидания ответа


def reply_to_ticket(update, context):
    ticket_id = context.user_data.get("reply_ticket")
    text = update.message.text

    conn = sqlite3.connect(TICKET_DB)
    c = conn.cursor()
    c.execute("SELECT user_id FROM tickets WHERE id=?", (ticket_id,))
    user_id = c.fetchone()[0]
    conn.close()

    context.bot.send_message(user_id, f"📥 Ответ администратора:\n{text}")
    update.message.reply_text("📤 Ответ отправлен пользователю.")
    return ConversationHandler.END



# ================== Роуты тикетов ==================

def register_tickets_handlers(dp):
    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler("ticket", ticket_command)],
        states={
            1: [MessageHandler(Filters.text & ~Filters.command, get_ticket_text)],
            2: [MessageHandler(Filters.text & ~Filters.command, reply_to_ticket)],
        },
        fallbacks=[],
        allow_reentry=True
    ))

    dp.add_handler(CallbackQueryHandler(ticket_button, pattern="^(accept|reject|reply)_"))
