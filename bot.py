# bot_v1_full.py — Part 1/3
import asyncio
import sqlite3
import datetime
import os
import logging
import difflib
from typing import List, Dict, Optional, Tuple

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ============================ ЛОГИРОВАНИЕ ============================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================ КОНФИГУРАЦИЯ ============================
ADMIN_ID = 6193109213
DZ_COOLDOWN = 4 * 60 * 60
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# ============================ УМНЫЙ ПАРСЕР ТЕКСТА (С AUTO-CORRECT) ============================
class SmartHomeworkParser:
    """
    Улучшенный парсер:
    - разбирает тексты в формате 'Предмет - задание', 'Предмет: задание', 'Предмет задание'
    - поддерживает многострочный ввод
    - умеет исправлять опечатки в названиях предметов с помощью fuzzy matching
    """
    def __init__(self):
        # Базовые ключевые слова — взяты из твоего списка, можно расширять
        self.subject_keywords = {
            'математика': ['математика', 'матем', 'мат', 'math'],
            'алгебра': ['алгебра', 'алг'],
            'геометрия': ['геометрия', 'геом'],
            'русский язык': ['русский язык', 'русский', 'русск', 'русяз'],
            'литература': ['литература', 'литра', 'литер'],
            'физика': ['физика', 'физ'],
            'химия': ['химия', 'хим'],
            'биология': ['биология', 'биол'],
            'история': ['история', 'ист'],
            'география': ['география', 'геогр'],
            'английский язык': ['английский язык', 'англ. язык', 'англ', 'english'],
            'информатика': ['информатика', 'информатика и икт', 'инфа', 'информ'],
            'физкультура': ['физкультура', 'физра', 'физ-ра'],
            'технология': ['технология', 'труд', 'труд (технология)'],
            'музыка': ['музыка', 'муз'],
            'изо': ['изо', 'изобразительное искусство'],
            'обж': ['обж', 'безопасность'],
            'мхк': ['мхк', 'мировая художественная культура'],
            'обществознание': ['обществознание', 'общ'],
            'астрономия': ['астрономия', 'астрон'],
            'черчение': ['черчение', 'черч'],
            'экономика': ['экономика', 'эконом'],
            'право': ['право'],
            'психология': ['психология', 'психол'],
            'экология': ['экология', 'экол'],
            'орксэ': ['орксэ', 'основы религиозных культур'],
            'риторика': ['риторика', 'риторик'],
            'краеведение': ['краеведение', 'краевед'],
            'ров': ['ров', 'разговоры о важном'],
            'рмг': ['рмг', 'русский родной'],
            'россия-мои горизонты': ['россия-мои горизонты', 'россия', 'горизонты'],
            'твис': ['твис', 'тв'],
            'офг': ['офг', 'основы фин.грам', 'основы финансовой грамотности', 'финграм'],
            'немецкий язык': ['немецкий', 'нем'],
            'французский язык': ['французский', 'франц'],
            'проект': ['проект', 'проектная деятельность']
        }
        # Список "мастер-имен" предметов (ключи словаря) для fuzzy matching
        self.master_subjects = list(self.subject_keywords.keys())

    def parse_any_format(self, text: str) -> List[Tuple[str, str]]:
        """
        Парсит произвольный текст и возвращает список (subject, task_text).
        Работает с многострочными вводами.
        """
        homework_list: List[Tuple[str, str]] = []
        lines = text.split('\n')
        current_subject = None
        homework_lines: List[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            subject_in_line = self._find_subject_in_line(line)
            if subject_in_line:
                # Если был предыдущий предмет — сохранить его
                if current_subject and homework_lines:
                    homework_text = ' '.join(homework_lines).strip()
                    if homework_text:
                        homework_list.append((current_subject, homework_text))
                current_subject = subject_in_line
                homework_lines = []
                homework_part = self._extract_homework_from_line(line, subject_in_line)
                if homework_part:
                    homework_lines.append(homework_part)
            elif current_subject:
                homework_lines.append(line)
            else:
                # Попытка парсить "Предмет Задание" в одной строке без разделителя
                maybe_subject, maybe_task = self._split_subject_task_guess(line)
                if maybe_subject:
                    current_subject = maybe_subject
                    homework_lines = [maybe_task] if maybe_task else []
                else:
                    # Если не нашли предмет — запоминаем как "неопределённый" (пропускаем)
                    continue

        # Финальный накопитель
        if current_subject and homework_lines:
            homework_text = ' '.join(homework_lines).strip()
            if homework_text:
                homework_list.append((current_subject, homework_text))

        return homework_list

    def _find_subject_in_line(self, line: str) -> Optional[str]:
        """
        Ищет явный предмет в строке (по ключевым словам).
        Возвращает normalized subject (ключ из master_subjects) или None.
        """
        line_lower = line.lower()
        # Проверяем ключевые слова
        for subject, keywords in self.subject_keywords.items():
            for keyword in keywords:
                keyword_lower = keyword.lower()
                if (line_lower.startswith(keyword_lower) or
                    f" {keyword_lower} " in line_lower or
                    f"{keyword_lower}:" in line_lower or
                    f"{keyword_lower}-" in line_lower):
                    # нашли — вернём normalized subject
                    return subject
        # Если не найдено явным образом, пробуем fuzzy match для слова в начале
        first_word = line_lower.split()[0]
        fm = self._fuzzy_match_subject(first_word)
        if fm:
            return fm
        return None

    def _extract_homework_from_line(self, line: str, subject: str) -> str:
        """
        Возвращает часть строки после ключевого слова предмета (задание).
        Если явно не найдено — возвращает строку целиком.
        """
        line_lower = line.lower()
        # пытаемся найти одно из ключевых слов для этого предмета внутри строки
        for keyword in self.subject_keywords.get(subject, []):
            kl = keyword.lower()
            if line_lower.startswith(kl):
                return line[len(keyword):].strip(' :-–')
            pos = line_lower.find(kl)
            if pos != -1:
                return line[pos + len(keyword):].strip(' :-–')
        # если не нашли, убираем первое слово (возможно это предмет) и возвращаем остаток
        parts = line.split(' ', 1)
        if len(parts) == 2:
            return parts[1].strip(' :-–')
        return ''

    def _split_subject_task_guess(self, line: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Попытка угадать предмет и задание в строке без разделителя:
        например: "Алгебра номер 3.30" или "Алгбра 3.30"
        """
        words = line.split()
        if not words:
            return None, None
        # Попробуем progressive prefix matching (1..3 слов)
        for take in range(1, min(4, len(words)+1)):
            candidate = ' '.join(words[:take])
            fm = self._fuzzy_match_subject(candidate)
            if fm:
                rest = ' '.join(words[take:]).strip()
                return fm, rest
        return None, None

    def _fuzzy_match_subject(self, text: str) -> Optional[str]:
        """
        Пытаемся найти ближайший предмет к тексту (fuzzy matching).
        Возвращаем предмет из master_subjects при уверенном совпадении.
        Порог можно настроить.
        """
        # Проверяем точное совпадение с ключевыми словами
        t = text.lower()
        for subject, keywords in self.subject_keywords.items():
            if t == subject.lower():
                return subject
            for kw in keywords:
                if t == kw.lower():
                    return subject

        # Теперь fuzzy match по именам предметов и ключевым словам
        candidates = self.master_subjects + [kw for kws in self.subject_keywords.values() for kw in kws]
        # Используем difflib.get_close_matches
        close = difflib.get_close_matches(t, candidates, n=1, cutoff=0.75)
        if close:
            found = close[0]
            # нужно вернуть normalized subject (ключ) для найденного слова
            for subject, keywords in self.subject_keywords.items():
                if found == subject or found in keywords:
                    return subject
        # Попробуем более слабый порог
        close2 = difflib.get_close_matches(t, candidates, n=1, cutoff=0.6)
        if close2:
            found = close2[0]
            for subject, keywords in self.subject_keywords.items():
                if found == subject or found in keywords:
                    return subject
        return None

# ============================ БАЗА ДАННЫХ ============================
class Database:
    def __init__(self, db_path: str = 'school_bot.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS bound_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER UNIQUE,
                bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                task_text TEXT,
                lesson_time TEXT,
                due_date TEXT,
                photo_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_dz_command TIMESTAMP,
                cooldown_until TIMESTAMP
            )
        ''')
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS cleaned_homework_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cleaned_date TEXT,
                subjects TEXT,
                photo_file_ids TEXT,
                cleaned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def add_bound_group(self, group_id: int) -> bool:
        try:
            self.conn.execute('INSERT OR REPLACE INTO bound_groups (group_id) VALUES (?)', (group_id,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"DB error add_bound_group: {e}")
            return False

    def get_bound_group(self) -> Optional[int]:
        try:
            cursor = self.conn.execute('SELECT group_id FROM bound_groups LIMIT 1')
            row = cursor.fetchone()
            return row[0] if row else None
        except sqlite3.Error as e:
            logger.error(f"DB error get_bound_group: {e}")
            return None

    def add_homework(self, subject: str, task_text: str, lesson_time: str, due_date: str, photo_file_id: str = None) -> bool:
        try:
            self.conn.execute(
                'INSERT INTO homework (subject, task_text, lesson_time, due_date, photo_file_id) VALUES (?, ?, ?, ?, ?)',
                (subject, task_text, lesson_time, due_date, photo_file_id)
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"DB error add_homework: {e}")
            return False

    def get_current_homework(self) -> List[tuple]:
        try:
            now = datetime.datetime.now()
            current_time = now.strftime("%H:%M")
            current_day = self._get_russian_day_name(now.weekday())
            cursor = self.conn.execute(
                '''SELECT subject, task_text, lesson_time, due_date, photo_file_id FROM homework 
                   WHERE due_date = ? AND lesson_time > ? 
                   ORDER BY lesson_time''',
                (current_day, current_time)
            )
            today_homework = cursor.fetchall()
            days_order = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
            current_index = days_order.index(current_day) if current_day in days_order else 0
            future_days = days_order[current_index + 1:] + days_order[:current_index]
            future_homework = []
            for day in future_days:
                cursor = self.conn.execute(
                    'SELECT subject, task_text, lesson_time, due_date, photo_file_id FROM homework WHERE due_date = ? ORDER BY lesson_time',
                    (day,)
                )
                future_homework.extend(cursor.fetchall())
            return today_homework + future_homework
        except sqlite3.Error as e:
            logger.error(f"DB error get_current_homework: {e}")
            return []

    def _get_russian_day_name(self, weekday: int) -> str:
        days = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        return days[weekday]

    def cleanup_old_homework(self) -> List[str]:
        cleaned_subjects = []
        cleaned_photo_ids = []
        try:
            now = datetime.datetime.now()
            current_time = now.strftime("%H:%M")
            current_day = self._get_russian_day_name(now.weekday())
            cursor = self.conn.execute(
                'SELECT subject, lesson_time, photo_file_id FROM homework WHERE due_date = ? AND lesson_time <= ?',
                (current_day, current_time)
            )
            lessons_to_clean = cursor.fetchall()
            for subject, lesson_time, photo_file_id in lessons_to_clean:
                cleaned_subjects.append(subject)
                if photo_file_id:
                    cleaned_photo_ids.append(photo_file_id)
            self.conn.execute(
                'DELETE FROM homework WHERE due_date = ? AND lesson_time <= ?',
                (current_day, current_time)
            )
            if cleaned_subjects:
                unique_subjects = list(set(cleaned_subjects))
                photo_ids_str = ', '.join([pid for pid in cleaned_photo_ids if pid])
                self.conn.execute(
                    'INSERT INTO cleaned_homework_log (cleaned_date, subjects, photo_file_ids) VALUES (?, ?, ?)',
                    (now.strftime("%Y-%m-%d"), ', '.join(unique_subjects), photo_ids_str)
                )
            self.conn.commit()
            logger.info(f"Auto-cleanup: removed {len(cleaned_subjects)} subjects, {len(cleaned_photo_ids)} photos")
            return list(set(cleaned_subjects))
        except sqlite3.Error as e:
            logger.error(f"DB error cleanup_old_homework: {e}")
            return []

    def clear_all_homework(self) -> bool:
        try:
            self.conn.execute('DELETE FROM homework')
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"DB error clear_all_homework: {e}")
            return False

    def check_cooldown(self, user_id: int) -> Optional[str]:
        try:
            cursor = self.conn.execute('SELECT cooldown_until FROM cooldowns WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                cooldown_until = datetime.datetime.fromisoformat(result[0])
                if datetime.datetime.now() < cooldown_until:
                    time_left = cooldown_until - datetime.datetime.now()
                    hours = time_left.seconds // 3600
                    minutes = (time_left.seconds % 3600) // 60
                    return f"{hours}ч {minutes}м"
            return None
        except sqlite3.Error as e:
            logger.error(f"DB error check_cooldown: {e}")
            return None

    def set_cooldown(self, user_id: int) -> bool:
        try:
            cooldown_until = datetime.datetime.now() + datetime.timedelta(seconds=DZ_COOLDOWN)
            self.conn.execute('INSERT OR REPLACE INTO cooldowns (user_id, cooldown_until) VALUES (?, ?)', (user_id, cooldown_until.isoformat()))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"DB error set_cooldown: {e}")
            return False

# ============================ МЕНЕДЖЕР РАСПИСАНИЯ ============================
class ScheduleManager:
    def __init__(self):
        # расписание аналогично твоему — оставил без изменений, можно править
        self.schedule = {
            'понедельник': [
                {'number': 1, 'subject': 'Ров', 'time': '08:00-08:40'},
                {'number': 2, 'subject': 'Русский язык', 'time': '08:50-09:30'},
                {'number': 3, 'subject': 'Физкультура', 'time': '09:40-10:20'},
                {'number': 4, 'subject': 'Технология', 'time': '10:40-11:20'},
                {'number': 5, 'subject': 'Технология', 'time': '11:30-12:10'},
                {'number': 6, 'subject': 'Русский язык', 'time': '12:20-13:00'},
                {'number': 7, 'subject': 'Музыка', 'time': '13:10-13:50'}
            ],
            'вторник': [
                {'number': 1, 'subject': 'Физика', 'time': '08:00-08:40'},
                {'number': 2, 'subject': 'Русский язык', 'time': '08:50-09:30'},
                {'number': 3, 'subject': 'Алгебра', 'time': '09:40-10:20'},
                {'number': 4, 'subject': 'Информатика', 'time': '10:40-11:20'},
                {'number': 5, 'subject': 'Биология', 'time': '11:30-12:10'},
                {'number': 6, 'subject': 'Английский язык', 'time': '12:20-13:00'},
                {'number': 7, 'subject': 'Английский язык', 'time': '13:10-13:50'}
            ],
            'среда': [
                {'number': 1, 'subject': 'Геометрия', 'time': '08:00-08:40'},
                {'number': 2, 'subject': 'Физика', 'time': '08:50-09:30'},
                {'number': 3, 'subject': 'История', 'time': '09:40-10:20'},
                {'number': 4, 'subject': 'Физкультура', 'time': '10:40-11:20'},
                {'number': 5, 'subject': 'Русский язык', 'time': '11:30-12:10'},
                {'number': 6, 'subject': 'Алгебра', 'time': '12:20-13:00'},
                {'number': 7, 'subject': 'Литература', 'time': '13:10-13:50'}
            ],
            'четверг': [
                {'number': 1, 'subject': 'Россия-мои горизонты', 'time': '08:00-08:40'},
                {'number': 2, 'subject': 'ТВИС', 'time': '08:50-09:30'},
                {'number': 3, 'subject': 'География', 'time': '09:40-10:20'},
                {'number': 4, 'subject': 'Физкультура', 'time': '10:40-11:20'},
                {'number': 5, 'subject': 'Русский язык', 'time': '11:30-12:10'},
                {'number': 6, 'subject': 'Изо', 'time': '12:20-13:00'},
                {'number': 7, 'subject': 'ОФГ', 'time': '13:10-13:50'}
            ],
            'пятница': [
                {'number': 1, 'subject': 'История', 'time': '08:00-08:40'},
                {'number': 2, 'subject': 'Алгебра', 'time': '08:50-09:30'},
                {'number': 3, 'subject': 'География', 'time': '09:40-10:20'},
                {'number': 4, 'subject': 'Английский язык', 'time': '10:40-11:20'},
                {'number': 5, 'subject': 'История', 'time': '11:30-12:10'},
                {'number': 6, 'subject': 'Геометрия', 'time': '12:20-13:00'}
            ]
        }
        self.day_names = {
            'понедельник': 'ПОНЕДЕЛЬНИК',
            'вторник': 'ВТОРНИК',
            'среда': 'СРЕДА',
            'четверг': 'ЧЕТВЕРГ',
            'пятница': 'ПЯТНИЦА'
        }
# bot_v1_full.py — Part 2/3 (продолжение)
class SchoolBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.schedule_manager = ScheduleManager()
        self.parser = SmartHomeworkParser()
        self.application = Application.builder().token(token).build()
        self._setup_handlers()
        self._setup_cleanup_task()

    def _setup_handlers(self):
        handlers = [
            CommandHandler("start", self.start_command),
            CommandHandler("bind", self.bind_command),
            CommandHandler("all_post", self.all_post_command),
            CommandHandler("confirm_post", self.confirm_post_command),
            CommandHandler("dz", self.homework_command),
            CommandHandler("add_dz", self.add_homework_command),
            CommandHandler("parse_dz", self.parse_homework_command),
            CommandHandler("clear_dz", self.clear_homework_command),
            CommandHandler("help", self.help_command),
            CommandHandler("cleanup_now", self.cleanup_now_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message),
            MessageHandler(filters.PHOTO, self.handle_photo)
        ]
        for handler in handlers:
            self.application.add_handler(handler)

    def _setup_cleanup_task(self):
        try:
            if hasattr(self.application, 'job_queue') and self.application.job_queue:
                async def cleanup_and_notify(context: ContextTypes.DEFAULT_TYPE):
                    try:
                        cleaned_subjects = self.db.cleanup_old_homework()
                        if cleaned_subjects:
                            today = datetime.datetime.now().strftime("%d.%m.%Y")
                            message = f"🗑️ *Ежедневный отчет об очистке ДЗ*\n\n"
                            message += f"*Дата:* {today}\n"
                            message += f"*Очищены ДЗ по предметам:*\n"
                            for subject in cleaned_subjects:
                                message += f"• {subject}\n"
                            message += f"\n*Всего предметов:* {len(cleaned_subjects)}"
                            try:
                                await context.bot.send_message(chat_id=ADMIN_ID, text=message, parse_mode='Markdown')
                                logger.info(f"Отправлен отчет об очистке ДЗ админу: {cleaned_subjects}")
                            except Exception as e:
                                logger.error(f"Ошибка отправки отчета админу: {e}")
                    except Exception as e:
                        logger.error(f"Ошибка в задаче очистки: {e}")
                # Запуск каждый день в 18:00
                self.application.job_queue.run_daily(cleanup_and_notify, time=datetime.time(hour=18, minute=0, second=0), name="daily_cleanup")
                logger.info("JobQueue настроен для ежедневной очистки ДЗ")
            else:
                logger.warning("JobQueue недоступен — ежедневная очистка не будет работать.")
        except Exception as e:
            logger.error(f"Ошибка настройки JobQueue: {e}")

    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN_ID

    # ---------------- Команды ----------------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if update.effective_chat.type == "private" and not self.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return
        keyboard = [["📅 Расписание на сегодня", "➡️ Следующий урок"], ["📚 Домашние задания"]]
        if self.is_admin(user_id):
            keyboard.append(["➕ Добавить ДЗ", "📄 Спарсить ДЗ"])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("🤖 Школьный Органайзер\n\nВыберите действие из меню ниже:", reply_markup=reply_markup)

    async def bind_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type == "private":
            await update.message.reply_text("❌ Эту команду нужно использовать в группе!")
            return
        group_id = update.effective_chat.id
        if self.db.add_bound_group(group_id):
            await update.message.reply_text("✅ Бот успешно привязан к этой группе!")
        else:
            await update.message.reply_text("❌ Ошибка при привязке бота!")

    async def all_post_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Эту команду можно использовать только в личных сообщениях с ботом!")
            return
        if not context.args:
            await update.message.reply_text("❌ Напишите текст сообщения после команды: /all_post Ваш текст")
            return
        post_text = " ".join(context.args)
        context.user_data['pending_all_post'] = {'text': post_text, 'user_id': update.effective_user.id}
        await update.message.reply_text("✅ Сообщение сохранено! Теперь перейди в групповой чат и напиши команду /confirm_post чтобы отправить уведомление всем участникам.")

    async def confirm_post_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type == "private":
            await update.message.reply_text("❌ Эту команду нужно использовать в группе!")
            return
        pending_post = context.user_data.get('pending_all_post')
        if not pending_post:
            await update.message.reply_text("❌ Нет сохраненного сообщения. Сначала используй /all_post в ЛС")
            return
        group_id = update.effective_chat.id
        post_text = pending_post['text']
        try:
            await context.bot.send_message(chat_id=group_id, text=f"📢 *ВНИМАНИЕ*\n\n{post_text}", parse_mode='Markdown')
            del context.user_data['pending_all_post']
            await update.message.reply_text("✅ Сообщение успешно отправлено в группу!")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            await update.message.reply_text(f"❌ Ошибка при отправке сообщения: {str(e)}")

    # ---------------- Фото ----------------
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Фото можно прикреплять только в ЛС с ботом!")
            return
        photo_file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        context.user_data['pending_photo'] = {'file_id': photo_file_id, 'caption': caption}
        await update.message.reply_text("📸 Фото сохранено! Теперь отправь предмет и задание в формате:\n`Предмет - Задание`\nИли просто напиши ДЗ в чате — я распознаю.", parse_mode='Markdown')

    # ---------------- Добавление ДЗ (команда) ----------------
    async def add_homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для добавления ДЗ.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Добавлять ДЗ можно только в ЛС с ботом!")
            return

        photo_file_id = None
        caption = ""
        if 'pending_photo' in context.user_data:
            photo_file_id = context.user_data['pending_photo']['file_id']
            caption = context.user_data['pending_photo'].get('caption', '')
        # Текст ДЗ — из caption, из аргументов или из текста сообщения
        homework_text = ""
        if caption:
            homework_text = caption
        elif update.message.text:
            full_text = update.message.text
            if full_text.startswith('/add_dz'):
                homework_text = full_text[8:].strip()
            else:
                homework_text = full_text
        elif context.args:
            homework_text = " ".join(context.args)

        if photo_file_id and not homework_text.strip():
            await update.message.reply_text("❌ Напиши предмет и задание в подписи к фото!")
            return

        homework_list = self.parser.parse_any_format(homework_text)
        if not homework_list:
            await update.message.reply_text("❌ Не удалось распознать ДЗ в тексте. Попробуй другой формат или используй /parse_dz")
            return

        added_count = 0
        results = []
        for idx, (subject, task) in enumerate(homework_list):
            # fuzzy-correct subject (parser уже возвращает normalized subject)
            next_lesson = self.schedule_manager.find_next_lesson(subject)
            if not next_lesson:
                results.append(f"❌ *{subject}* — урок не найден в расписании")
                continue
            success = self.db.add_homework(subject, task, next_lesson['time'], next_lesson['day'], photo_file_id if idx == 0 else None)
            if success:
                added_count += 1
                day_name = self.schedule_manager.day_names[next_lesson['day']]
                photo_mark = " 📸" if photo_file_id and idx == 0 else ""
                results.append(f"✅ *{subject}*{photo_mark} → {day_name} ({next_lesson['time']})")
            else:
                results.append(f"❌ *{subject}* — ошибка базы данных")

        if 'pending_photo' in context.user_data:
            del context.user_data['pending_photo']

        result_text = "📋 *Результат добавления ДЗ:*\n\n" + "\n".join(results)
        if added_count > 0:
            result_text += f"\n\n🎯 *Успешно добавлено: {added_count} заданий!*"
            result_text += "\n🗑️ *Каждое ДЗ удалится после своего урока*"
        await update.message.reply_text(result_text, parse_mode='Markdown')

    # ---------------- Парсинг (команда) ----------------
    async def parse_homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Эта команда только для ЛС!")
            return
        if not context.args:
            await update.message.reply_text("📄 *Умный парсинг ДЗ*\n\nСкопируй текст с ДЗ и отправь командой:\n`/parse_dz [твой_текст]`", parse_mode='Markdown')
            return
        text_to_parse = " ".join(context.args)
        homework_list = self.parser.parse_any_format(text_to_parse)
        if not homework_list:
            await update.message.reply_text("❌ Не удалось найти ДЗ в тексте.")
            return
        added_count = 0
        results = []
        for subject, homework_text in homework_list:
            next_lesson = self.schedule_manager.find_next_lesson(subject)
            if next_lesson:
                success = self.db.add_homework(subject, homework_text, next_lesson['time'], next_lesson['day'])
                if success:
                    added_count += 1
                    day_name = self.schedule_manager.day_names[next_lesson['day']]
                    results.append(f"✅ *{subject}* → {day_name} ({next_lesson['time']})")
        report_text = "📋 *Результат парсинга ДЗ:*\n\n"
        for subject, homework_text in homework_list:
            report_text += f"📖 *{subject}:*\n`{homework_text}`\n\n"
        report_text += f"🎯 *Успешно добавлено: {added_count} заданий*\n"
        report_text += "🗑️ *Каждое ДЗ удалится после своего урока*"
        await update.message.reply_text(report_text, parse_mode='Markdown')

    # ---------------- Ручная очистка ----------------
    async def cleanup_now_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        try:
            cleaned_subjects = self.db.cleanup_old_homework()
            if cleaned_subjects:
                today = datetime.datetime.now().strftime("%d.%m.%Y")
                message = f"🗑️ *Ручная очистка ДЗ*\n\n"
                message += f"*Дата:* {today}\n"
                message += f"*Очищены ДЗ по предметам:*\n"
                for subject in cleaned_subjects:
                    message += f"• {subject}\n"
                message += f"\n*Всего предметов:* {len(cleaned_subjects)}"
                await update.message.reply_text(message, parse_mode='Markdown')
            else:
                await update.message.reply_text("✅ Нечего очищать - все ДЗ актуальны!")
        except Exception as e:
            logger.error(f"Ошибка при ручной очистке: {e}")
            await update.message.reply_text("❌ Ошибка при очистке ДЗ!")

    # ---------------- Help ----------------
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = "🤖 *ДОСТУПНЫЕ КОМАНДЫ*\n\n"
        if update.effective_chat.type == "private":
            help_text += "📝 *Основные команды:*\n"
            help_text += "• /start - Запустить бота\n"
            help_text += "• /dz - Показать домашние задания\n"
            help_text += "• /help - Показать это сообщение\n"
            if self.is_admin(update.effective_user.id):
                help_text += "\n👑 *Команды администратора:*\n"
                help_text += "• /add_dz - Добавить ДЗ\n"
                help_text += "• /parse_dz - Спарсить ДЗ из текста\n"
                help_text += "• /clear_dz - Очистить все ДЗ\n"
                help_text += "• /all_post - Сделать объявление для всех\n"
                help_text += "• /bind - Привязать бота к группе\n"
                help_text += "• /cleanup_now - Очистить прошедшие ДЗ\n"
        else:
            help_text += "👥 *Команды для групп:*\n"
            help_text += "• /dz - Показать домашние задания\n"
            if self.is_admin(update.effective_user.id):
                help_text += "• /confirm_post - Подтвердить отправку объявления\n"
                help_text += "• /bind - Привязать бота к группе\n"
        help_text += "\n🎯 *Бот автоматически удаляет ДЗ после уроков*"
        help_text += "\n⏰ *КД команды /dz: 4 часа*"
        await update.message.reply_text(help_text, parse_mode='Markdown')

    # ---------------- Показать ДЗ ----------------
    async def homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        cooldown_left = self.db.check_cooldown(user_id)
        if cooldown_left and not self.is_admin(user_id):
            await update.message.reply_text(f"⏰ Следующая проверка ДЗ будет доступна через {cooldown_left}\nКД команды /dz: 4 часа на человека")
            return
        if not self.is_admin(user_id):
            self.db.set_cooldown(user_id)
        self.db.cleanup_old_homework()
        homework_list = self.db.get_current_homework()
        if not homework_list:
            await update.message.reply_text("📚 На ближайшее время домашних заданий нет!")
            return
        homework_by_day = {}
        for subject, task, lesson_time, due_date, photo_file_id in homework_list:
            if due_date not in homework_by_day:
                homework_by_day[due_date] = []
            homework_by_day[due_date].append((subject, task, lesson_time, photo_file_id))
        homework_text = "📚 *ДОМАШНИЕ ЗАДАНИЯ*\n\n"
        days_display_order = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница']
        for day in days_display_order:
            if day in homework_by_day:
                day_name = self.schedule_manager.day_names[day]
                homework_text += f"*🗓️ {day_name.upper()}*\n"
                for subject, task, lesson_time, photo_file_id in homework_by_day[day]:
                    photo_mark = " 📸" if photo_file_id else ""
                    homework_text += f"▫️ *{subject}*{photo_mark} ({lesson_time})\n"
                    homework_text += f"```\n{task}\n```\n\n"
                    if photo_file_id:
                        try:
                            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_file_id, caption=f"*{subject}* ({lesson_time})", parse_mode='Markdown')
                        except Exception as e:
                            logger.error(f"Ошибка отправки фото: {e}")
                            homework_text += "❌ *Фото не загрузилось*\n\n"
                homework_text += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        if not self.is_admin(user_id):
            homework_text += "\n⏰ *Следующая проверка ДЗ через 4 часа*"
        await update.message.reply_text(homework_text, parse_mode='Markdown')
    # ---------------- Очистка БД полностью ----------------
    async def clear_homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Эта команда только для ЛС!")
            return
        success = self.db.clear_all_homework()
        if success:
            await update.message.reply_text("🗑️ Все домашние задания очищены!")
        else:
            await update.message.reply_text("❌ Ошибка при очистке ДЗ!")

    # ---------------- Авто-ответы по тексту в ЛС ----------------
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()

        # Игнорируем обычных юзеров в ЛС
        if update.effective_chat.type == "private" and not self.is_admin(user_id):
            return

        # Популярные кнопки
        if text == "📅 Расписание на сегодня":
            schedule_text = self.schedule_manager.get_today_schedule()
            await update.message.reply_text(schedule_text)
            return

        if text == "➡️ Следующий урок":
            next_lesson = self.schedule_manager.get_next_lesson()
            await update.message.reply_text(next_lesson)
            return

        if text == "📚 Домашние задания":
            await self.homework_command(update, context)
            return

        # Автоматическое добавление ДЗ простым текстом
        if self.is_admin(user_id):
            hw = self.parser.parse_any_format(text)
            if hw:
                logger.info(f"Auto-detected homework from plain text: {hw}")
                await self.add_homework_from_plain(update, context, hw)
                return

        await update.message.reply_text("🤖 Я не понял сообщение. Используйте кнопки или команды /help")

    async def add_homework_from_plain(self, update: Update, context: ContextTypes.DEFAULT_TYPE, homework_list):
        """Используется для авто-парсинга текста в ЛС"""
        added_count = 0
        results = []
        for subject, task in homework_list:
            next_lesson = self.schedule_manager.find_next_lesson(subject)
            if not next_lesson:
                results.append(f"❌ *{subject}* — урок не найден в расписании")
                continue
            success = self.db.add_homework(subject, task, next_lesson['time'], next_lesson['day'])
            if success:
                added_count += 1
                day_name = self.schedule_manager.day_names[next_lesson['day']]
                results.append(f"✅ *{subject}* → {day_name} ({next_lesson['time']})")

        result_text = "📌 *Автоматическое добавление ДЗ:*\n\n" + "\n".join(results)
        await update.message.reply_text(result_text, parse_mode='Markdown')

    # ---------------- Запуск ----------------
    def run(self):
        print("🤖 Бот запущен...")
        self.application.run_polling()


# ============================ ЗАПУСК ============================
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ОШИБКА: BOT_TOKEN не установлен в переменных окружения!")
        exit(1)

    print("🚀 Запуск SchoolBot...")
    bot = SchoolBot(BOT_TOKEN)
    bot.run()
