"""Persistent hunger and token rewards; never stores conversation text.

Transactions make repeated token snapshots and concurrent feeding idempotent.
Live rewards use a fixed activation timestamp. Legacy snapshots without a
time window start at their first complete counter (no historical gifts).
"""
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DECAY_SECONDS = 300
TOKENS_PER_BREAD = 1_000_000
BREAD_FULLNESS = 30
HUNGRY_MESSAGE = '主人～ 我好饿～'


class PetCare:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS care (id INTEGER PRIMARY KEY CHECK(id=1), '
                       'fullness INTEGER, decay_at REAL, bread INTEGER, remainder INTEGER, '
                       'hungry INTEGER, pending INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, highwater INTEGER)')
            db.execute('INSERT OR IGNORE INTO care VALUES (1,100,?,0,0,0,0)', (self.clock(),))
            db.execute('CREATE TABLE IF NOT EXISTS reward_window (id INTEGER PRIMARY KEY CHECK(id=1), since REAL)')
            db.execute('INSERT OR IGNORE INTO reward_window SELECT 1,decay_at FROM care WHERE id=1')
            self.reward_since = db.execute('SELECT since FROM reward_window WHERE id=1').fetchone()[0]

    def connect(self):
        # Short bound on GUI contention; failed operations are retried by the UI.
        db = sqlite3.connect(self.path, timeout=0.05)
        db.row_factory = sqlite3.Row
        return db

    def update(self, snapshot=None, feed=False, claim_hunger=False):
        now = self.clock()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            state = dict(db.execute('SELECT * FROM care WHERE id=1').fetchone())
            before = state.copy()
            elapsed = max(0, int((now - state['decay_at']) // DECAY_SECONDS))
            if elapsed:
                state['decay_at'] += elapsed * DECAY_SECONDS
                state['fullness'] = max(0, state['fullness'] - elapsed)
            if state['fullness'] <= 30 and not state['hungry']:
                state['hungry'] = state['pending'] = 1

            awarded = 0
            snapshot = snapshot or {}
            total = snapshot.get('all_total')
            source = snapshot.get('source_id')
            windowed = (snapshot.get('reward_since') == self.reward_since
                        and snapshot.get('readable_files', 0) > 0 and 'reward_total' in snapshot)
            if windowed:
                total = snapshot['reward_total']
                if isinstance(source, str):source += ':since:' + str(self.reward_since)
            valid = (isinstance(total, int) and not isinstance(total, bool) and 0 <= total < 2**62
                     and isinstance(source, str) and bool(source)
                     and not any(snapshot.get(k) for k in ('unavailable', 'refresh_failed'))
                     and (windowed or not any(snapshot.get(k) for k in ('partial', 'format_unknown'))))
            # A stale snapshot for a different activation window cannot create a
            # fallback baseline or count recovered historical records.
            if 'reward_since' in snapshot and not windowed:valid = False
            if valid:
                row = db.execute('SELECT highwater FROM sources WHERE id=?', (source,)).fetchone()
                if row is None:
                    initial = 0 if windowed else total
                    db.execute('INSERT INTO sources VALUES (?,?)', (source, initial))
                    highwater = initial
                else:
                    highwater = row['highwater']
                if total > highwater:
                    awarded, state['remainder'] = divmod(state['remainder'] + total - highwater,
                                                       TOKENS_PER_BREAD)
                    state['bread'] += awarded
                    db.execute('UPDATE sources SET highwater=? WHERE id=?', (total, source))

            fed = False
            if feed and state['bread'] > 0 and state['fullness'] < 100:
                state['bread'] -= 1
                state['fullness'] = min(100, state['fullness'] + BREAD_FULLNESS)
                fed = True
            if state['fullness'] > 30:
                state['hungry'] = state['pending'] = 0
            notify = bool(claim_hunger and state['pending'])
            if notify:
                state['pending'] = 0
            if state != before:
                db.execute('UPDATE care SET fullness=:fullness, decay_at=:decay_at, bread=:bread, '
                           'remainder=:remainder, hungry=:hungry, pending=:pending WHERE id=1', state)
            initialized = bool(db.execute('SELECT 1 FROM sources LIMIT 1').fetchone())
        return dict(fullness=state['fullness'], bread=state['bread'], remainder=state['remainder'],
                    initialized=initialized, pending=bool(state['pending']), notify=notify,
                    awarded=awarded, fed=fed)
