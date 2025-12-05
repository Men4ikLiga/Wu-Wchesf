# bot.py
import os
import logging
import asyncio
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# local modules
import config
from core import homework as hw_mod
from core import parser as parser_mod
from core import subjects as subjects_mod
from core import schedule as schedule_mod
from core import utils as utils_mod
from modules import tickets, search, checklist, admin_panel_tg

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SchoolBotV3")

BOT_TOKEN = os.environ.get("BOT_TOKEN") or config.BOT_TOKEN

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set in env or config.py")
    raise SystemExit("Set BOT_TOKEN")

# Build application
app = Application.builder().token(BOT_TOKEN).build()

# --- helper: admin check ---
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMINS

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # custom greetings
    if uid == config.OWNER_ID:
        greet = "👑 Привет, хозяин!"
    elif uid == 6111166190:
        greet = "✨ Рад вас видеть, Анастасия!"
    elif uid == 6955239802:
        greet = "🔱 Рад вас видеть, Анжелика Михайловна!"
    else:
        greet = "👋 Привет! Я школьный бот. Напиши 'бот дз' или '/dz' чтобы увидеть задания."

    keyboard = [["📚 Домашка", "📅 Расписание"], ["➡️ Следующий урок", "🔍 Поиск ДЗ"]]
    if is_admin(uid):
        keyboard.append(["➕ Добавить ДЗ", "🛠 Админ панель"])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(greet, reply_markup=reply_markup)

# --- /help ---
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Команды:\n"
        "/start - меню\n"
        "/help - помощь\n"
        "/dz - показать ДЗ\n"
        "/add_dz <текст> - добавить ДЗ (админ в ЛС)\n"
        "/parse_dz <текст> - распарсить (админ)\n"
        "/ticket - создать тикет\n"
        "\nПоддерживаемые фразы: 'бот дз', 'скиньте дз', 'какая домашка по алгебре' и т.д."
    )
    await update.message.reply_text(text)

# --- /dz ---
async def dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = hw_mod.list_homework()
    text = utils_mod.format_homework(rows)
    await update.message.reply_text(text, parse_mode="Markdown")

# --- /add_dz ---
async def add_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Только админы.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❗ Используйте эту команду в личных сообщениях боту.")
        return
    text = " ".join(context.args) if context.args else (update.message.text or "")
    if text.startswith("/add_dz"):
        text = text[len("/add_dz"):].strip()
    if not text:
        await update.message.reply_text("Использование: /add_dz Алгебра - упр 5; Геометрия - стр 10")
        return
    # parse and add
    parsed = parser_mod.parse_multi(text)
    added = []
    for subj, task in parsed:
        hid = hw_mod.add_homework(subject=subj, task=task)
        added.append((hid, subj))
    await update.message.reply_text(f"✅ Добавлено {len(added)} заданий.")

# --- /parse_dz ---
async def parse_dz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Только админы.")
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Пример: /parse_dz Алгебра упр 5 Геометрия задача 1")
        return
    parsed = parser_mod.parse_multi(text)
    reply = "Распознано:\n"
    for subj, task in parsed:
        reply += f"• {subj}: {task}\n"
    await update.message.reply_text(reply)

# --- natural text handler (multi purpose) ---
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    # tickets module may intercept (if user sent /ticket or waiting)
    if tickets.is_waiting_for_ticket(uid) or tickets.is_admin_waiting_reply(uid):
        await tickets.integrated_message_handler(update, context)
        return

    low = text.lower()

    # quick keyboard actions
    if low in ("📚 домашка", "домашка", "дз", "бот дз", "бот, дз", "покажи дз", "скинь дз"):
        await dz_cmd(update, context)
        return
    if "расписани" in low or low in ("📅 расписание", "расписание"):
        sched = schedule_mod.get_today_schedule_text()
        await update.message.reply_text(sched)
        return
    if "следующ" in low or low in ("➡️ следующий урок", "следующий урок"):
        nxt = schedule_mod.get_next_lesson_text()
        await update.message.reply_text(nxt)
        return
    if low.startswith(("удали ","удалить ","очисти ","очисть ")):
        # admin natural commands
        if not is_admin(uid) or update.effective_chat.type != "private":
            await update.message.reply_text("❌ Только админ в ЛС.")
            return
        handled = await admin_panel_tg.handle_admin_natural(update, context, text)
        if not handled:
            await update.message.reply_text("Не распознано.")
        return

    # detect homework-add style message (multi-subject) from admin in private
    if is_admin(uid) and update.effective_chat.type == "private":
        parsed = parser_mod.parse_multi(text)
        if parsed:
            added = []
            for subj, task in parsed:
                hid = hw_mod.add_homework(subject=subj, task=task)
                added.append((hid, subj))
            await update.message.reply_text(f"✅ Добавлено {len(added)} заданий.")
            return

    # detect homework requests: general or subject-specific
    is_req, subj = parser_mod.is_homework_request_and_extract_subject(text)
    if is_req:
        if subj:
            rows = hw_mod.search_by_subject(subj)
            text_out = utils_mod.format_homework(rows)
            await update.message.reply_text(text_out, parse_mode="Markdown")
            return
        else:
            await dz_cmd(update, context)
            return

    # search command via natural language
    if "найди" in low or "иск" in low or low.startswith("поиск"):
        results = search.search_homework(text)
        await update.message.reply_text(utils_mod.format_homework(results), parse_mode="Markdown")
        return

    # fallback
    if update.effective_chat.type == "private":
        await update.message.reply_text("Не понял. /help — список команд.")
    else:
        # don't spam groups; hint only if bot is directly called
        if config.BOT_TOKEN and ("бот" in low or "ду" in low or "дз" in low[:30]):
            await update.message.reply_text("Напиши 'бот дз' или 'какая домашка по <предмет>'")

# --- inline callback handler (used by tickets, admin panel, checklist) ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()
    # delegate to modules
    if data.startswith("ticket_"):
        await tickets.callback_query_handler(update, context)
        return
    if data.startswith("chk_"):
        await checklist.callback_handler(update, context)
        return
    if data.startswith("admin_"):
        await admin_panel_tg.callback_handler(update, context)
        return
    await query.edit_message_text("Неизвестная кнопка.")

# register handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_cmd))
app.add_handler(CommandHandler("dz", dz_cmd))
app.add_handler(CommandHandler("add_dz", add_dz_cmd))
app.add_handler(CommandHandler("parse_dz", parse_dz_cmd))
app.add_handler(CommandHandler("ticket", tickets.ticket_command))
app.add_handler(CommandHandler("checklist", checklist.cmd_checklist))

app.add_handler(CallbackQueryHandler(callback_query_handler))
app.add_handler(MessageHandler(filters.PHOTO, hw_mod.photo_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

# start background cleanup task from schedule module
schedule_mod.start_cleanup_scheduler(app)

if __name__ == "__main__":
    print("Запуск SchoolBot V3...")
    app.run_polling()
