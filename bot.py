# bot.py
import os
import json
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ------------- Настройки -------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "dz.json"
TZ = ZoneInfo("Europe/Amsterdam")

# Расписание (список уроков по позиции 0..6) — использую строки из твоего текста
SCHEDULE = {
    "пн": ["Ров", "Русский язык", "Физра", "Технология", "Технология", "Русский язык", "Музыка"],
    "вт": ["Физика", "Русский", "Алгебра", "Информатика", "Биология", "Английский/Технология", "Английский/Технология"],
    "ср": ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литра"],
    "чт": ["РМГ", "ТВИС", "География", "Физра", "Русский", "Изо", "ОФГ"],
    "пт": ["История", "Алгебра", "География", "Английский", "История", "Геометрия"]
}
DAYS_ORDER = ["пн", "вт", "ср", "чт", "пт"]

# Уроки/перемены:
# урок 40 мин, перемены 10 мин, кроме между 3-м и 4-м уроком (индекс 2 -> 3) перемена 40 мин
# первый урок стартует в 08:00
LESSON_DURATION = 40
SHORT_BREAK = 10
LONG_BREAK = 40
FIRST_LESSON_START = time(hour=8, minute=0)

# ------------- Хранилище -------------
# формат файла: список записей: {
#   "subject": str,
#   "task": str,
#   "day": "пн"/...,
#   "lesson_index": int (0..6),
#   "assigned_date": "YYYY-MM-DD",
#   "end_iso": "ISO datetime with tz"
# }
dz_list = []

# ------------- Утилиты -------------
def load_data():
    global dz_list
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                dz_list = json.load(f)
        except Exception:
            dz_list = []

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(dz_list, f, ensure_ascii=False, indent=2)

def weekday_name_from_date(d: date):
    # вернёт 'пн'..'пт' для даты (если сб/вс — возвращаем None)
    wd = d.weekday()  # 0 = Mon
    if wd >= 5:
        return None
    return DAYS_ORDER[wd]

def today_day_key():
    return weekday_name_from_date(datetime.now(TZ).date())

def lesson_start_datetime_for_date_and_index(d: date, idx: int):
    # вычисляем начало урока idx (0-based) в дате d, с учётом перемен
    # начинаем от FIRST_LESSON_START
    start_dt = datetime.combine(d, FIRST_LESSON_START, tzinfo=TZ)
    minutes = 0
    for i in range(idx):
        minutes += LESSON_DURATION
        # перемена после урока i (перед уроком i+1)
        minutes += (LONG_BREAK if i == 2 else SHORT_BREAK)
    return start_dt + timedelta(minutes=minutes)

def lesson_end_datetime_for_date_and_index(d: date, idx: int):
    return lesson_start_datetime_for_date_and_index(d, idx) + timedelta(minutes=LESSON_DURATION)

def find_subject_positions(subject_name):
    # возвращает список (day_key, lesson_index) где subject_name встречается подстрокой (регистронезависимо)
    res = []
    s = subject_name.lower()
    for day, lessons in SCHEDULE.items():
        for idx, lesson in enumerate(lessons):
            if s in lesson.lower() or lesson.lower() in s:
                res.append((day, idx))
    return res

def next_date_for_day(day_key, from_dt=None):
    # возвращает ближайшую дату для day_key начиная с from_dt.date()
    if from_dt is None:
        from_dt = datetime.now(TZ)
    today = from_dt.date()
    # индекс в DAYS_ORDER
    target = DAYS_ORDER.index(day_key)
    # current weekday index 0..4 or if weekend then map to next monday?
    cur_wd = from_dt.weekday()  # 0..6
    # если сегодня суб/вс — treat cur_wd as weekday position before monday: we'll compute delta normally
    # map cur index into 0..4 cyclical (we'll compute days delta mod 7)
    # find next date >= today that has weekday == target (0=Mon)
    delta_days = ( (target) - (cur_wd if cur_wd <=4 else cur_wd) ) % 7
    candidate = today + timedelta(days=delta_days)
    # If candidate is weekend mapping could give invalid but mod7 ok.
    # Ensure candidate is weekday (should be)
    return candidate

def assign_one(subject, task):
    """
    Для предмета subject и текста task находит ближайший урок (по дате и времени),
    возвращает dict-запись или None если не найдено.
    """
    now = datetime.now(TZ)
    today_key = today_day_key()
    positions = find_subject_positions(subject)
    if not positions:
        return None

    # для каждой найденной позиции вычислим ближайшую дату, учитывая если сегодня и урок уже прошёл -> перенос на следующую такую дату (через 7 дней)
    candidates = []
    for day_key, idx in positions:
        # найдем ближайшую дату для day_key, начиная с today
        # сначала попытка: если day_key == today_key, проверить для этого дня урок idx
        if today_key == day_key:
            # check this week's date
            candidate_date = datetime.now(TZ).date()
            end_dt = lesson_end_datetime_for_date_and_index(candidate_date, idx)
            if now < end_dt:
                # урок ещё не закончился — назначаем на сегодня
                candidates.append((end_dt, day_key, idx, candidate_date))
                continue
            else:
                # урок прошёл — назначаем на следующий такой день (через 7 дней)
                candidate_date = candidate_date + timedelta(days=7)
                end_dt = lesson_end_datetime_for_date_and_index(candidate_date, idx)
                candidates.append((end_dt, day_key, idx, candidate_date))
        else:
            # найдём ближайшую дату для day_key (может оказаться уже в ближайшие дни)
            # compute days until next weekday = python weekday 0..6
            target_weekday = DAYS_ORDER.index(day_key)  # 0..4 (Mon..Fri)
            # convert target to standard weekday numbering (Mon=0..Sun=6)
            # target_weekday is already Mon=0..Fri=4 -> ok
            cur_py_wd = now.weekday()  # 0..6
            days_ahead = (target_weekday - cur_py_wd) % 7
            if days_ahead == 0:
                days_ahead = 7  # if same day but we are here then not equal earlier; safe default
            candidate_date = (now.date() + timedelta(days=days_ahead))
            end_dt = lesson_end_datetime_for_date_and_index(candidate_date, idx)
            candidates.append((end_dt, day_key, idx, candidate_date))

    # выберем кандидат с минимальным end_dt (самый близкий)
    candidates.sort(key=lambda x: x[0])
    end_dt, day_key, idx, assigned_date = candidates[0]
    record = {
        "subject": subject,
        "task": task,
        "day": day_key,
        "lesson_index": idx,
        "assigned_date": assigned_date.isoformat(),
        "end_iso": end_dt.isoformat()
    }
    return record

def remove_expired():
    """Удалить записи, у которых end_iso <= now"""
    now = datetime.now(TZ)
    before = len(dz_list)
    dz_list[:] = [r for r in dz_list if datetime.fromisoformat(r["end_iso"]) > now]
    if len(dz_list) != before:
        save_data()

# ------------- Обработчики -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для ДЗ.\n\n"
        "Формат добавления:\n"
        "/add_dz\n"
        "Русский - параграф 6\n"
        "Физика - упр. 3\n\n"
        "Можно указывать в начале день: \n"
        "Вт:\nРусский - параграф 6\n\n"
        "Команды:\n"
        "/add_dz — начать добавление (или можно отправить сразу текст после команды)\n"
        "/dz — показать все актуальные ДЗ\n"
        "/clear — очистить всё\n"
    )

# /add_dz — принимает сразу текст после команды или многострочный блок
async def add_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    text = update.message.text.replace("/add_dz", "", 1).strip()
    if not text:
        await update.message.reply_text("Отправь после /add_dz строки в формате:\nРусский - параграф 6\nФизика - упр. 3")
        return

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # поддержка указания дня в первой строке: "Вт:" или "вт:"
    explicit_day = None
    if lines:
        first = lines[0].lower().rstrip(":")
        if first in DAYS_ORDER:
            explicit_day = first
            lines = lines[1:]

    added = []
    failed = []
    for line in lines:
        if "-" not in line:
            continue
        subj, task = line.split("-", 1)
        subj = subj.strip()
        task = task.strip()
        # если пользователь явно указал день, попробуем найти на этот день конкретную позицию (предпочтение)
        record = None
        if explicit_day:
            # найдём позиции для subj, но ограничим day==explicit_day
            pos = [(d,i) for (d,i) in find_subject_positions(subj) if d==explicit_day]
            if pos:
                # create record using that specific pos
                day_key, idx = pos[0]
                # compute assigned date: if explicit_day == today and lesson not passed -> today else next such day (maybe same week)
                now = datetime.now(TZ)
                candidate_date = next_date_for_day(day_key, from_dt=now)
                # if candidate_date == today and lesson already passed, add 7 days
                end_dt = lesson_end_datetime_for_date_and_index(candidate_date, idx)
                if datetime.now(TZ) >= end_dt:
                    candidate_date = candidate_date + timedelta(days=7)
                    end_dt = lesson_end_datetime_for_date_and_index(candidate_date, idx)
                record = {
                    "subject": subj,
                    "task": task,
                    "day": day_key,
                    "lesson_index": idx,
                    "assigned_date": candidate_date.isoformat(),
                    "end_iso": end_dt.isoformat()
                }
        if not record:
            record = assign_one(subj, task)
        if record:
            dz_list.append(record)
            added.append(record)
        else:
            failed.append(subj)

    save_data()
    remove_expired()
    msg = ""
    if added:
        msg += "✅ Сохранено:\n"
        for r in added:
            msg += f"{r['subject']} — {r['task']} (на {r['day'].upper()} {r['assigned_date']}, урок #{r['lesson_index']+1})\n"
    if failed:
        msg += "\n⚠️ Не найден предмет (не удалось распределить): " + ", ".join(failed)
    await update.message.reply_text(msg)

async def show_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_data()
    remove_expired()
    if not dz_list:
        await update.message.reply_text("📭 Домашек нет — всё чисто.")
        return
    # отсортируем по assigned date
    sorted_list = sorted(dz_list, key=lambda r: r["end_iso"])
    text = "📘 Текущие домашние задания (по ближайшим урокам):\n\n"
    for r in sorted_list:
        end = datetime.fromisoformat(r["end_iso"]).astimezone(TZ)
        text += f"📅 {r['assigned_date']} ({r['day'].upper()}) — урок #{r['lesson_index']+1}\n• {r['subject']}: {r['task']}\n  (кончится: {end.strftime('%Y-%m-%d %H:%M')})\n\n"
    await update.message.reply_text(text)

async def clear_dz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dz_list.clear()
    save_data()
    await update.message.reply_text("🧹 Всё ДЗ очищено.")

# ------------- Запуск -------------
def main():
    load_data()
    remove_expired()  # при старте убираем устаревшее
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_dz", add_dz))
    app.add_handler(CommandHandler("dz", show_dz))
    app.add_handler(CommandHandler("clear", clear_dz))
    # простой обработчик для произвольного текста (поможет, если пользователь просто шлёт ДЗ без /add_dz)
    async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = update.message.text.strip()
        # если сообщение содержит "-" и несколько строк, считаем это попыткой добавить дз
        if "-" in txt:
            # симулируем "/add_dz " + text
            update.message.text = "/add_dz " + txt
            await add_dz(update, context)
        else:
            await update.message.reply_text("Не распознал. Для добавления ДЗ используйте /add_dz или отправьте строки вида 'Русский - параграф 6'.")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("✅ Бот запущен...")
    app.run_polling()  # запускаем блокирующе — корректно для runtime хостинга

if __name__ == "__main__":
    main()
