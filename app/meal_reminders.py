"""Local daily reminders with durable per-slot deduplication (no network)."""
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

MEAL_MESSAGE = '主人～吃饭了喵～'
MEAL_TIMES = {'meal_lunch_time': '11:40', 'meal_dinner_time': '17:20'}
GRACE_SECONDS = 300


def valid_time(value):
    return isinstance(value, str) and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) is not None


class MealReminders:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS delivered (day TEXT, slot TEXT, PRIMARY KEY(day, slot))')
            self.delivered = set(db.execute('SELECT day, slot FROM delivered'))

    def connect(self):
        return sqlite3.connect(self.path, timeout=0.05)

    def claim(self, settings, now=None, available=True):
        if not settings.get('meal_reminders', True) or not available:
            return False
        now = now or datetime.now()
        day = now.date().isoformat()
        due = []
        for slot, default in MEAL_TIMES.items():
            value = settings.get(slot, default)
            if not valid_time(value):
                value = default
            hour, minute = map(int, value.split(':'))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if 0 <= (now - scheduled).total_seconds() < GRACE_SECONDS and (day, slot) not in self.delivered:
                due.append((day, slot))
        if not due:
            return False  # No disk access on ordinary timer ticks.
        with closing(self.connect()) as db, db:
            before = db.total_changes
            db.executemany('INSERT OR IGNORE INTO delivered VALUES (?,?)', due)
            claimed = db.total_changes > before
        self.delivered.update(due)
        return claimed
