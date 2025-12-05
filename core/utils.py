# core/utils.py
from typing import List, Tuple

def format_homework(rows: List[Tuple]) -> str:
    if not rows:
        return "📚 ДЗ не найдено."
    out = "📚 *ДОМАШНИЕ ЗАДАНИЯ*\n\n"
    # group by day
    groups = {}
    for r in rows:
        hid, subj, task, day, time, photo, created = r
        groups.setdefault(day, []).append(r)
    days_order = ['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье']
    for d in days_order:
        if d in groups:
            out += f"*🗓 {d.upper()}*\n"
            for hid, subj, task, day, time, photo, created in groups[d]:
                pm = " 📸" if photo else ""
                out += f"▫️ *{subj}*{pm} ({time})\n```\n{task}\n```\n\n"
            out += "━━━━━━━━━━━━━━━━\n\n"
    return out
