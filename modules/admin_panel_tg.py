# modules/admin_panel_tg.py
from core import homework as hw
from core import parser as parser_mod
from core import subjects as subj_mod
from telegram import Update
from telegram.ext import ContextTypes

async def handle_admin_natural(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    low = text.lower()
    # clear all
    if low.startswith(("очисти все","удали все","очисти всё","удали всё")):
        cnt = hw.clear_all()
        await update.message.reply_text(f"🗑 Очищено {cnt} записей.")
        return True
    # delete by subject
    if low.startswith(("удали ","удалить ","очисти ","очисть ")):
        rest = low.split(' ',1)[1] if ' ' in low else ''
        rest = rest.replace('предмет','').strip()
        subj = subj_mod.normalize_subject(rest)
        cnt = hw.delete_by_subject(subj)
        await update.message.reply_text(f"Удалено {cnt} записей по предмету {subj}.")
        return True
    # add quick pattern "добавь Алгебра ... "
    if low.startswith(("добавь ","добавить ")):
        rest = text.split(' ',1)[1] if ' ' in text else ''
        parsed = parser_mod.parse_multi(rest)
        if not parsed:
            await update.message.reply_text("Не удалось распарсить.")
            return True
        added = 0
        for subj,task in parsed:
            hw.add_homework(subj, task)
            added += 1
        await update.message.reply_text(f"Добавлено {added} заданий.")
        return True
    return False

# callback handler for admin buttons if any
async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = (query.data or "")
    await query.edit_message_text("Нажал кнопку: " + data)
