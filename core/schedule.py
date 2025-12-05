# core/schedule.py
import datetime
from typing import Optional, Dict
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from core import subjects as subj_mod
from core import homework as hw_mod

class ScheduleManager:
    def __init__(self):
        # times are "HH:MM" start times
        self.schedule = {
            'понедельник': [
                {'number':1,'subject':'Ров','time':'08:00'},
                {'number':2,'subject':'Русский','time':'08:50'},
                {'number':3,'subject':'Физкультура','time':'09:40'},
                {'number':4,'subject':'Технология','time':'10:40'},
                {'number':5,'subject':'Технология','time':'11:30'},
                {'number':6,'subject':'Русский','time':'12:20'},
                {'number':7,'subject':'Музыка','time':'13:10'}
            ],
            'вторник': [
                {'number':1,'subject':'Физика','time':'08:00'},
                {'number':2,'subject':'Русский','time':'08:50'},
                {'number':3,'subject':'Алгебра','time':'09:40'},
                {'number':4,'subject':'Информатика','time':'10:40'},
                {'number':5,'subject':'Биология','time':'11:30'},
                {'number':6,'subject':'Английский','time':'12:20'},
                {'number':7,'subject':'Английский','time':'13:10'}
            ],
            'среда': [
                {'number':1,'subject':'Геометрия','time':'08:00'},
                {'number':2,'subject':'Физика','time':'08:50'},
                {'number':3,'subject':'История','time':'09:40'},
                {'number':4,'subject':'Физкультура','time':'10:40'},
                {'number':5,'subject':'Русский','time':'11:30'},
                {'number':6,'subject':'Алгебра','time':'12:20'},
                {'number':7,'subject':'Литература','time':'13:10'}
            ],
            'четверг': [
                {'number':1,'subject':'Россия-мои горизонты','time':'08:00'},
                {'number':2,'subject':'ТВИС','time':'08:50'},
                {'number':3,'subject':'География','time':'09:40'},
                {'number':4,'subject':'Физкультура','time':'10:40'},
                {'number':5,'subject':'Русский','time':'11:30'},
                {'number':6,'subject':'Изо','time':'12:20'},
                {'number':7,'subject':'ОФГ','time':'13:10'}
            ],
            'пятница': [
                {'number':1,'subject':'История','time':'08:00'},
                {'number':2,'subject':'Алгебра','time':'08:50'},
                {'number':3,'subject':'География','time':'09:40'},
                {'number':4,'subject':'Английский','time':'10:40'},
                {'number':5,'subject':'История','time':'11:30'},
                {'number':6,'subject':'Геометрия','time':'12:20'}
            ]
        }

    def get_current_day(self) -> str:
        days = ['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье']
        return days[datetime.datetime.now().weekday()]

    def find_next_lesson(self, subject: str) -> Optional[Dict]:
        subj = subject.lower()
        today = self.get_current_day()
        now = datetime.datetime.now().time()
        if today in self.schedule:
            for lesson in self.schedule[today]:
                ls_subj = lesson['subject'].lower()
                h,m = map(int, lesson['time'].split(':'))
                lesson_time = datetime.time(h,m)
                if (subj in ls_subj or ls_subj in subj) and now < lesson_time:
                    return {'day': today, 'time': lesson['time'], 'subject': lesson['subject']}
        # search future days
        order = ['понедельник','вторник','среда','четверг','пятница']
        if today in order:
            idx = order.index(today)
        else:
            idx = 0
        for i in range(1, len(order)):
            d = order[(idx + i) % len(order)]
            if d in self.schedule:
                for lesson in self.schedule[d]:
                    ls_subj = lesson['subject'].lower()
                    if subj in ls_subj or ls_subj in subj:
                        return {'day': d, 'time': lesson['time'], 'subject': lesson['subject']}
        return None

    def get_today_schedule_text(self) -> str:
        d = self.get_current_day()
        if d not in self.schedule:
            return "Сегодня уроков нет."
        text = f"📅 Расписание на {d}:\n"
        for l in self.schedule[d]:
            text += f"{l['number']}. {l['subject']} — {l['time']}\n"
        return text

    def get_next_lesson_text(self) -> str:
        d = self.get_current_day()
        if d not in self.schedule:
            return "Нет уроков."
        now = datetime.datetime.now().time()
        for l in self.schedule[d]:
            st = datetime.time(*map(int, l['time'].split(':')))
            if now < st:
                return f"Следующий урок: {l['subject']} в {l['time']}"
        return "Уроки сегодня закончились."

schedule_manager = ScheduleManager()

# scheduler for cleanup
_scheduler = AsyncIOScheduler()

def cleanup_job():
    # uses hw_mod to clean passed lessons
    cleaned = hw_mod_cleanup()
    # optionally notify owner/admins via bot - this is done in bot.py via Task if needed
    return cleaned

def hw_mod_cleanup():
    # auto-clean: delete rows where day == today and time <= now
    now = datetime.datetime.now()
    today = schedule_manager.get_current_day()
    cur_time = now.strftime("%H:%M")
    # directly operate on DB in homework module (import inside to avoid circular)
    import sqlite3
    from core import homework as hw
    conn = sqlite3.connect(hw.DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT id, subject, photo_file_id FROM homework WHERE day=? AND time<=?", (today, cur_time))
    rows = c.fetchall()
    ids = [r[0] for r in rows]
    subjects = list(set([r[1] for r in rows]))
    photo_ids = [r[2] for r in rows if r[2]]
    if ids:
        q = "DELETE FROM homework WHERE id IN ({})".format(','.join('?'*len(ids)))
        c.execute(q, ids)
        conn.commit()
    conn.close()
    # return cleaned subjects list
    return subjects

def start_cleanup_scheduler(app):
    # schedule to run every 10 minutes
    if not _scheduler.running:
        _scheduler.add_job(lambda: asyncio.get_event_loop().create_task(_notify_cleanup(app)), 'interval', minutes=10, next_run_time=datetime.datetime.now())
        _scheduler.start()

async def _notify_cleanup(app):
    cleaned = hw_mod_cleanup()
    if cleaned and app:
        try:
            text = "🗑️ Авто-очистка: удалены ДЗ по предметам:\n" + "\n".join("• " + s for s in cleaned)
            # notify owner
            await app.bot.send_message(chat_id=config.OWNER_ID, text=text)
        except Exception:
            pass
