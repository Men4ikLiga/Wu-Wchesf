# modules/checklist.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from typing import Dict

# in-memory simple checklist: user_id -> {hw_id: bool}
CHECKS = {}

def cmd_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # show quick actions
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отметить ДЗ выполненным", callback_data="chk_mark")]])
    return context.bot.send_message(chat_id=uid, text="Чек-лист: отметь выполненное", reply_markup=kb)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    uid = query.from_user.id
    if data == "chk_mark":
        # toggle a placeholder state
        CHECKS.setdefault(uid, {})
        # just fake toggle:
        CHECKS[uid]['last'] = not CHECKS[uid].get('last', False)
        state = "✅ Отмечено" if CHECKS[uid]['last'] else "❌ Снято"
        await query.edit_message_text(state)
