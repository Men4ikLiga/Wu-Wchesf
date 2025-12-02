import asyncio
import sqlite3
import datetime
import os
import logging
from typing import List, Dict, Optional, Tuple
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ============================ НАСТРОЙКА ЛОГГИРОВАНИЯ ============================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================ КОНФИГУРАЦИЯ ============================
ADMIN_ID = 6193109213
DZ_COOLDOWN = 4 * 60 * 60
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# ============================ УМНЫЙ ПАРСЕР ТЕКСТА ============================
class SmartHomeworkParser:
    def __init__(self):
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

    def parse_any_format(self, text: str) -> List[Tuple[str, str]]:
        homework_list = []
        lines = text.split('\n')
        current_subject = None
        homework_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            subject_in_line = self._find_subject_in_line(line)
            if subject_in_line:
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
        if current_subject and homework_lines:
            homework_text = ' '.join(homework_lines).strip()
            if homework_text:
                homework_list.append((current_subject, homework_text))
        return homework_list

    def _find_subject_in_line(self, line: str) -> Optional[str]:
        line_lower = line.lower()
        for subject, keywords in self.subject_keywords.items():
            for keyword in keywords:
                if (line_lower.startswith(keyword) or 
                    f" {keyword} " in line_lower or 
                    f" {keyword}:" in line_lower or
                    f" {keyword}-" in line_lower):
                    return subject
        return None

    def _extract_homework_from_line(self, line: str, subject: str) -> str:
        line_lower = line.lower()
        for keyword in self.subject_keywords[subject]:
            if line_lower.startswith(keyword):
                return line[len(keyword):].strip(' :-\–')
            subject_pos = line_lower.find(keyword)
            if subject_pos != -1:
                after_subject = line[subject_pos + len(keyword):].strip(' :-\–')
                return after_subject
        return line

# ============================ БАЗА ДАННЫХ ============================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('school_bot.db', check_same_thread=False)
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
            logger.error(f"Ошибка базы данных при добавлении группы: {e}")
            return False
    
    def get_bound_group(self) -> Optional[int]:
        try:
            cursor = self.conn.execute('SELECT group_id FROM bound_groups LIMIT 1')
            result = cursor.fetchone()
            return result[0] if result else None
        except sqlite3.Error as e:
            logger.error(f"Ошибка базы данных при получении группы: {e}")
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
            logger.error(f"Ошибка базы данных при добавлении ДЗ: {e}")
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
            logger.error(f"Ошибка базы данных при получении ДЗ: {e}")
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
            logger.info(f"Автоочистка ДЗ. Очищено: {len(cleaned_subjects)} предметов, {len(cleaned_photo_ids)} фото")
            return list(set(cleaned_subjects))
        except sqlite3.Error as e:
            logger.error(f"Ошибка базы данных при очистке ДЗ: {e}")
            return []
    
    def clear_all_homework(self) -> bool:
        try:
            self.conn.execute('DELETE FROM homework')
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка базы данных при очистке всех ДЗ: {e}")
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
            logger.error(f"Ошибка базы данных при проверке кд: {e}")
            return None
    
    def set_cooldown(self, user_id: int) -> bool:
        try:
            cooldown_until = datetime.datetime.now() + datetime.timedelta(seconds=DZ_COOLDOWN)
            self.conn.execute('INSERT OR REPLACE INTO cooldowns (user_id, cooldown_until) VALUES (?, ?)', (user_id, cooldown_until.isoformat()))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка базы данных при установке кд: {e}")
            return False

# ============================ МЕНЕДЖЕР РАСПИСАНИЯ ============================
class ScheduleManager:
    def __init__(self):
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
    
    def get_current_day(self) -> str:
        days = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
        return days[datetime.datetime.now().weekday()]
    
    def get_today_schedule(self) -> str:
        today = self.get_current_day()
        if today not in self.schedule:
            return "📅 Сегодня уроков нет!"
        schedule_text = f"📅 {self.day_names[today].upper()}:\n\n"
        for lesson in self.schedule[today]:
            schedule_text += f"{lesson['number']}. {lesson['subject']} ({lesson['time']})\n"
        return schedule_text
    
    def get_next_lesson(self) -> str:
        today = self.get_current_day()
        if today not in self.schedule:
            return "⏰ Сегодня уроков нет!"
        now = datetime.datetime.now().time()
        current_time = now.strftime("%H:%M")
        for lesson in self.schedule[today]:
            start_time = lesson['time'].split('-')[0]
            if current_time < start_time:
                time_until = self._calculate_time_until(start_time)
                return f"⏰ Следующий урок:\n{lesson['subject']} ({lesson['time']})\nДо начала: {time_until}"
        return "⏰ Уроки на сегодня закончились!"
    
    def _calculate_time_until(self, start_time: str) -> str:
        now = datetime.datetime.now()
        start = datetime.datetime.strptime(start_time, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
        if start < now:
            start += datetime.timedelta(days=1)
        diff = start - now
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        if hours > 0:
            return f"{hours}ч {minutes}м"
        return f"{minutes} минут"
    
    def find_next_lesson(self, subject: str) -> Optional[Dict]:
        today = self.get_current_day()
        days_order = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница']
        normalized_subject = self._normalize_subject_name(subject)
        current_index = days_order.index(today) if today in days_order else 0
        now = datetime.datetime.now().time()
        current_time = now.strftime("%H:%M")
        if today in self.schedule:
            for lesson in self.schedule[today]:
                if self._subject_matches(lesson['subject'], normalized_subject):
                    lesson_start_time = lesson['time'].split('-')[0]
                    if current_time < lesson_start_time:
                        return {'day': today, 'time': lesson['time'], 'subject': lesson['subject']}
        for i in range(1, 5):
            next_day_index = (current_index + i) % 5
            day = days_order[next_day_index]
            if day in self.schedule:
                for lesson in self.schedule[day]:
                    if self._subject_matches(lesson['subject'], normalized_subject):
                        return {'day': day, 'time': lesson['time'], 'subject': lesson['subject']}
        if 'понедельник' in self.schedule:
            for lesson in self.schedule['понедельник']:
                if self._subject_matches(lesson['subject'], normalized_subject):
                    return {'day': 'понедельник', 'time': lesson['time'], 'subject': lesson['subject']}
        return None

    def _normalize_subject_name(self, subject: str) -> str:
        subject_lower = subject.lower()
        mappings = {
            'труд': 'технология',
            'физра': 'физкультура',
            'физ-ра': 'физкультура',
            'английский': 'английский язык',
            'литра': 'литература',
            'изо': 'изобразительное искусство',
            'информатика и икт': 'информатика',
            'тв': 'твис',
            'финграм': 'офг',
            'основы финансовой грамотности': 'офг',
            'разговоры о важном': 'ров',
            'русский родной': 'рмг'
        }
        return mappings.get(subject_lower, subject_lower)

    def _subject_matches(self, schedule_subject: str, user_subject: str) -> bool:
        schedule_clean = schedule_subject.lower()
        user_clean = user_subject.lower()
        if schedule_clean == user_clean:
            return True
        if user_clean in schedule_clean or schedule_clean in user_clean:
            return True
        special_matches = {
            'технология': ['труд'],
            'физкультура': ['физра', 'физ-ра'],
            'английский язык': ['английский'],
            'литература': ['литра'],
            'изобразительное искусство': ['изо'],
            'информатика': ['информатика и икт'],
            'твис': ['тв'],
            'офг': ['финграм', 'основы финансовой грамотности'],
            'ров': ['разговоры о важном'],
            'рмг': ['русский родной']
        }
        for main_subject, variants in special_matches.items():
            if (schedule_clean == main_subject and user_clean in variants) or (user_clean == main_subject and schedule_clean in variants):
                return True
        return False

# ============================ ОСНОВНОЙ КЛАСС БОТА ============================
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
                self.application.job_queue.run_daily(cleanup_and_notify, time=datetime.time(hour=18, minute=0, second=0), name="daily_cleanup")
                logger.info("✅ JobQueue настроен для ежедневной очистки ДЗ")
            else:
                logger.warning("⚠️ JobQueue недоступен. Ежедневная очистка не будет работать автоматически.")
        except Exception as e:
            logger.error(f"❌ Ошибка настройки JobQueue: {e}")
    
    def is_admin(self, user_id: int) -> bool:
        return user_id == ADMIN_ID
    
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
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Фото можно прикреплять только в ЛС с ботом!")
            return
        photo_file_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        context.user_data['pending_photo'] = {'file_id': photo_file_id, 'caption': caption}
        await update.message.reply_text("📸 Фото сохранено! Теперь отправь предмет и задание в формате:\n`Предмет - Задание`\n\nИли используй команду /add_dz с текстом")
    
    async def add_homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для добавления ДЗ.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Добавлять ДЗ можно только в ЛС с ботом!")
            return
        photo_file_id = None
        if 'pending_photo' in context.user_data:
            photo_file_id = context.user_data['pending_photo']['file_id']
            caption = context.user_data['pending_photo']['caption']
            if caption:
                homework_text = caption
            else:
                homework_text = ""
        elif update.message.text:
            full_text = update.message.text
            if full_text.startswith('/add_dz'):
                homework_text = full_text[8:].strip()
            else:
                homework_text = full_text
        elif context.args:
            homework_text = " ".join(context.args)
        else:
            await update.message.reply_text("📝 *Добавление домашних заданий*\n\nОтправь текст ДЗ или фото с подписью.\n📸 *Фото автоматически прикрепится к ДЗ*\n\nФормат текста:\n```\nЛитература - Написать сочинение\nМатематика - упр 25-26\n```", parse_mode='Markdown')
            return
        if photo_file_id and not homework_text.strip():
            await update.message.reply_text("❌ Напиши предмет и задание в подписи к фото!")
            return
        homework_list = self.parser.parse_any_format(homework_text)
        if not homework_list:
            await update.message.reply_text("❌ Не удалось распознать ДЗ в тексте.\nПопробуй другой формат или используй /parse_dz")
            return
        added_count = 0
        results = []
        for subject, task in homework_list:
            next_lesson = self.schedule_manager.find_next_lesson(subject)
            if next_lesson:
                success = self.db.add_homework(subject, task, next_lesson['time'], next_lesson['day'], photo_file_id if subject == homework_list[0][0] else None)
                if success:
                    added_count += 1
                    day_name = self.schedule_manager.day_names[next_lesson['day']]
                    photo_mark = " 📸" if photo_file_id and subject == homework_list[0][0] else ""
                    results.append(f"✅ *{subject}*{photo_mark} → {day_name} ({next_lesson['time']})")
                else:
                    results.append(f"❌ *{subject}* - ошибка базы данных")
            else:
                results.append(f"❌ *{subject}* - урок не найден в расписании")
        if 'pending_photo' in context.user_data:
            del context.user_data['pending_photo']
        result_text = "📋 *Результат добавления ДЗ:*\n\n" + "\n".join(results)
        if added_count > 0:
            result_text += f"\n\n🎯 *Успешно добавлено: {added_count} заданий!*\n"
            if photo_file_id:
                result_text += "📸 *Фото прикреплено к первому предмету*\n"
            result_text += "🗑️ *Каждое ДЗ удалится после своего урока*"
        await update.message.reply_text(result_text, parse_mode='Markdown')
    
    async def parse_homework_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет прав для этой команды.")
            return
        if update.effective_chat.type != "private":
            await update.message.reply_text("❌ Эта команда только для ЛС!")
            return
        if not context.args:
            await update.message.reply_text("📄 *Умный парсинг ДЗ*\n\nСкопируй текст с ДЗ и отправь командой:\n`/parse_dz [твой_текст]`\n\n🎯 *Бот работает с ЛЮБЫМ форматом:*\n• Предмет: задание\n• Предмет - задание\n• Просто список предметов и заданий\n\n📝 *Пример:*\n`/parse_dz Математика: упр 25-26 Русский - сочинение Физика задачи 1-5`", parse_mode='Markdown')
            return
        text_to_parse = " ".join(context.args)
        homework_list = self.parser.parse_any_format(text_to_parse)
        if not homework_list:
            await update.message.reply_text("❌ Не удалось найти ДЗ в тексте.\nПопробуй другой формат или используй /add_dz")
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
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        if update.effective_chat.type != "private" and text.startswith('/'):
            await update.message.reply_text("🤖 *Доступные команды в группе:*\n\n• /dz - Показать домашние задания\n• /help - Показать все команды\n\n🎯 Бот автоматически удаляет ДЗ после уроков", parse_mode='Markdown')
            return
        if update.effective_chat.type == "private" and not self.is_admin(user_id):
            return
        if text == "📅 Расписание на сегодня":
            schedule_text = self.schedule_manager.get_today_schedule()
            await update.message.reply_text(schedule_text)
        elif text == "➡️ Следующий урок":
            next_lesson = self.schedule_manager.get_next_lesson()
            await update.message.reply_text(next_lesson)
        elif text == "📚 Домашние задания":
            await self.homework_command(update, context)
        elif text == "➕ Добавить ДЗ" and self.is_admin(user_id):
            await self.add_homework_command(update, context)
        elif text == "📄 Спарсить ДЗ" and self.is_admin(user_id):
            await update.message.reply_text("📄 Отправь текст с ДЗ командой:\n`/parse_dz [твой_текст]`\n\nИли просто скопируй и вставь текст с ДЗ, бот сам всё распознает!", parse_mode='Markdown')
    
    def run(self):
        print("🤖 Бот запущен...")
        self.application.run_polling()

# ============================ ЗАПУСК БОТА ============================
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ ОШИБКА: Переменная окружения BOT_TOKEN не установлена!")
        print("📝 Установи на Railway: Settings → Variables → BOT_TOKEN")
        exit(1)
    print("✅ BOT_TOKEN найден, запуск бота...")
    bot = SchoolBot(BOT_TOKEN)
    bot.run()
