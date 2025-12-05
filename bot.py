import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

import config
from modules import tickets   # система тикетов (ваша, рабочая)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)

# -----------------------------------------
#              КАСТОМНЫЕ ПРИВЕТСТВИЯ
# -----------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # --- твои кастомные приветствия ---
    if uid == config.OWNER:
        text = (f"С возвращением, хозяин ({uid}) 😈\n"
                f"Бот полностью подчиняется вам.\n\n"
                "Команды:\n"
                "/ticket — создать тикет\n"
                "дз — показать домашку\n"
                "/help — помощь")
    elif uid == 6111166190:
        text = ("Рад видеть вас, Анастасия! 💜\n\n"
                "Команды:\n/ticket — создать тикет\nдз — домашка\n/help — помощь")
    elif uid == 6955239802:
        text = ("Рад вас видеть, Главный Следящий за Бесями — Анжелика Михайловна! 👑🔥\n\n"
                "Команды:\n/ticket — создать тикет\nдз — домашка\n/help — помощь")

    # --- обычные пользователи ---
    elif uid in config.ADMINS:
        text = (f"Здравствуйте, {update.effective_user.first_name}! 👑\n"
                "Вы администратор.\n\n"
                "Команды:\n"
                "/ticket — тикеты\n"
                "дз — домашка\n"
                "/help — помощь")
    else:
        text = (f"Привет, {update.effective_user.first_name}! ✋\n"
                "Вы можете:\n"
                "• Написать 'дз' — получить домашнее задание\n"
                "• /ticket — создать запрос/вопрос/идею\n"
                "• /help — список команд")

    await update.message.reply_text(text)



# -----------------------------------------
#                   HELP
# -----------------------------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Команды:\n"
        "/start — запуск\n"
        "/ticket — создать тикет\n"
        "«дз» — домашка\n"
        "Скоро: /panel — админ-панель\n"
    )



# -----------------------------------------
#         Обработка запроса ДЗ
# -----------------------------------------

TRIGGERS_DZ = [
    "дз", "домашка", "домашнее задание", "задание",
    "какое дз", "скажи дз", "бот дз"
]

async def dz_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()

    # реагируем только если сообщение = триггер целиком
    if text in TRIGGERS_DZ:
        return await update.message.reply_text("📚 ДЗ пока пустое.\n"
                                              "(здесь подключим вывод заданий позже)")


# -----------------------------------------
#       ЕДИНЫЙ РОУТЕР СООБЩЕНИЙ
# -----------------------------------------

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = (update.message.text or "").strip()

    # 1) пользователь пишет текст тикета
    if tickets.is_waiting_for_ticket(uid):
        return await tickets.integrated_message_handler(update, context)

    # 2) админ отвечает на тикет
    if tickets.is_admin_waiting_reply(uid):
        return await tickets.admin_send_reply(update, context)

    # 3) запросы ДЗ
    await dz_request(update, context)



# -----------------------------------------
#        ОБРАБОТКА INLINE КНОПОК
# -----------------------------------------

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await tickets.callback_query_handler(update, context)



# -----------------------------------------
#                MAIN
# -----------------------------------------

def main():
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ticket", tickets.ticket_command))

    # callback кнопок тикетов
    app.add_handler(CallbackQueryHandler(callback_router, pattern="ticket_"))

    # обработка любых текст-сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    print("\n🚀 BOT STARTED SUCCESSFULLY\n")
    app.run_polling()


if __name__ == "__main__":
    main()
