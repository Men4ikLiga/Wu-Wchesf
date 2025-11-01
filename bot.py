import logging
from dotenv import load_dotenv
load_dotenv()
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, timedelta
import json
import os

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("Токен бота не найден! Убедитесь, что переменная окружения BOT_TOKEN или TELEGRAM_BOT_TOKEN установлена.")

# Расписание уроков
SCHEDULE = {
    0: ["Ров", "Русский язык", "физра", "Технология", "Технология", "Русский язык", "Музыка"],
    1: ["Физика", "Русский", "Алгебра", "Информатика", "Биология", "Английский/Технология", "Английский/Технология"],
    2: ["Геометрия", "Физика", "История", "Физра", "Русский язык", "Алгебра", "Литра"],
    3: ["РМГ", "ТВИС", "География", "Физра", "Русский", "Изо", "ОФГ"],
    4: ["История", "Алгебра", "География", "Английский", "История", "Геометрия"]
}

# Точное время начала уроков (8:00 + 40 мин урок + 10 мин перемена, кроме 3->4 = 20 мин)
LESSON_TIMES = [
    datetime.strptime("08:00", "%H:%M").time(),   # 1 урок
    datetime.strptime("08:50", "%H:%M").time(),   # 2 урок  
    datetime.strptime("09:40", "%H:%M").time(),   # 3 урок
    datetime.strptime("10:30", "%H:%M").time(),   # 4 урок (перемена 20 мин)
    datetime.strptime("11:30", "%H:%M").time(),   # 5 урок
    datetime.strptime("12:20", "%H:%M").time(),   # 6 урок
    datetime.strptime("13:10", "%H:%M").time()    # 7 урок
]

class HomeworkBot:
    def __init__(self):
        self.homework_file = "homework.json"
        self.homework = self.load_homework()
    
    def load_homework(self):
        if os.path.exists(self.homework_file):
            with open(self.homework_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save_homework(self):
        with open(self.homework_file, 'w', encoding='utf-8') as f:
            json.dump(self.homework, f, ensure_ascii=False, indent=2)
    
    def get_next_lesson_date(self, subject):
        today = datetime.now()
        current_day = today.weekday()
        current_time = today.time()
        
        # Проверяем сегодняшние уроки
        if current_day in SCHEDULE:
            today_lessons = SCHEDULE[current_day]
            for i, lesson in enumerate(today_lessons):
                if lesson == subject:
                    lesson_time = LESSON_TIMES[i]
                    # Если урок еще не прошел сегодня
                    if current_time < lesson_time:
                        return today.date().isoformat()
        
        # Ищем следующий день с этим предметом
        days_ahead = 1
        while days_ahead <= 7:
            next_day = (current_day + days_ahead) % 7
            if next_day in SCHEDULE and subject in SCHEDULE[next_day]:
                next_date = today + timedelta(days=days_ahead)
                return next_date.date().isoformat()
            days_ahead += 1
        
        return None
    
    def cleanup_old_homework(self):
        today = datetime.now().date()
        current_day = today.weekday()
        current_time = datetime.now().time()
        
        for subject in list(self.homework.keys()):
            hw_date_str = self.homework[subject].get('date')
            if hw_date_str:
                hw_date = datetime.fromisoformat(hw_date_str).date()
                if hw_date < today:
                    del self.homework[subject]
                elif hw_date == today:
                    # Удаляем если урок уже прошел сегодня
                    if current_day in SCHEDULE:
                        today_lessons = SCHEDULE[current_day]
                        if subject in today_lessons:
                            lesson_index = today_lessons.index(subject)
                            if lesson_index < len(LESSON_TIMES) and current_time > LESSON_TIMES[lesson_index]:
                                del self.homework[subject]
    
    async def add_dz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        if user_id not in self.homework:
            self.homework[user_id] = {}
        
        current_subject = None
        lines = update.message.text.split('\n')
        
        for line in lines[1:]:  # Пропускаем команду /add_dz
            line = line.strip()
            if not line:
                continue
                
            # Проверяем, является ли строка названием предмета
            is_subject = False
            for day_lessons in SCHEDULE.values():
                for lesson in day_lessons:
                    if lesson in line:
                        current_subject = lesson
                        is_subject = True
                        break
                if is_subject:
                    break
            
            if not is_subject and current_subject and line:
                # Это домашнее задание для текущего предмета
                next_date = self.get_next_lesson_date(current_subject)
                if next_date:
                    self.homework[user_id][current_subject] = {
                        'task': line,
                        'date': next_date
                    }
        
        self.save_homework()
        await update.message.reply_text("Домашнее задание добавлено!")
    
    async def show_dz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        self.cleanup_old_homework()
        self.save_homework()
        
        if user_id not in self.homework or not self.homework[user_id]:
            await update.message.reply_text("Нет домашних заданий!")
            return
        
        response = "📚 Ваши домашние задания:\n\n"
        for subject, hw in self.homework[user_id].items():
            date_obj = datetime.fromisoformat(hw['date']).date()
            today = datetime.now().date()
            
            if date_obj == today:
                date_str = "Сегодня"
            elif date_obj == today + timedelta(days=1):
                date_str = "Завтра"
            else:
                date_str = date_obj.strftime("%d.%m")
                
            response += f"📖 {subject} ({date_str}):\n{hw['task']}\n\n"
        
        await update.message.reply_text(response)
    
    async def clear_dz(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if user_id in self.homework:
            self.homework[user_id] = {}
            self.save_homework()
        await update.message.reply_text("Все домашние задания очищены!")

# Создание и настройка бота
def main():
    bot = HomeworkBot()
    
    # Используем правильную переменную с токеном
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("add_dz", bot.add_dz))
    application.add_handler(CommandHandler("dz", bot.show_dz))
    application.add_handler(CommandHandler("clear", bot.clear_dz))
    
    application.run_polling()

if __name__ == '__main__':
    main()
