import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pet_care import PetCare


class CareTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now = 1000
        self.path = Path(self.folder.name) / 'care.sqlite3'
        self.care = PetCare(self.path, clock=lambda: self.now)

    def tokens(self, total, **kwargs):
        return self.care.update(dict(all_total=total, source_id='local', **kwargs))

    def test_decay_boundary_and_fraction_survive_restart(self):
        self.assertEqual(self.care.update()['fullness'], 100)
        self.now += 299
        self.assertEqual(self.care.update()['fullness'], 100)
        self.care = PetCare(self.path, clock=lambda: self.now)
        self.now += 1
        self.assertEqual(self.care.update()['fullness'], 99)
        self.now += 601
        self.assertEqual(self.care.update()['fullness'], 97)

    def test_offline_decay_clamps_zero(self):
        self.now += 300 * 200
        self.care = PetCare(self.path, clock=lambda: self.now)
        state = self.care.update(claim_hunger=True)
        self.assertEqual(state['fullness'], 0)
        self.assertTrue(state['notify'])
        self.assertFalse(self.care.update(claim_hunger=True)['notify'])

    def test_threshold_once_until_fed_above_thirty(self):
        self.tokens(0)
        self.tokens(2_000_000)
        self.now += 69 * 300
        self.assertFalse(self.care.update(claim_hunger=True)['notify'])
        self.now += 300
        self.assertTrue(self.care.update()['pending'])
        self.care = PetCare(self.path, clock=lambda: self.now)
        self.assertTrue(self.care.update(claim_hunger=True)['notify'])
        self.assertFalse(self.care.update(claim_hunger=True)['notify'])
        self.assertEqual(self.care.update(feed=True)['fullness'], 60)
        self.now += 30 * 300
        self.assertTrue(self.care.update(claim_hunger=True)['notify'])

    def test_hunger_does_not_rearm_when_bread_only_reaches_thirty(self):
        self.tokens(0);self.tokens(1_000_000)
        self.now += 100 * 300
        self.care.update(claim_hunger=True)
        self.assertEqual(self.care.update(feed=True)['fullness'], 30)
        self.assertFalse(self.care.update(claim_hunger=True)['notify'])

    def test_baseline_rewards_remainder_and_restart_dedup(self):
        self.assertEqual(self.tokens(9_000_000)['bread'], 0)
        self.assertEqual(self.tokens(9_999_999)['bread'], 0)
        self.assertEqual(self.tokens(10_000_000)['bread'], 1)
        state = self.tokens(12_250_000)
        self.assertEqual((state['bread'], state['remainder']), (3, 250_000))
        self.care = PetCare(self.path, clock=lambda: self.now)
        self.assertEqual(self.tokens(12_250_000)['bread'], 3)
        self.assertEqual(self.tokens(11_000_000)['bread'], 3)
        self.assertEqual(self.tokens(13_000_000)['bread'], 4)

    def test_partial_and_unavailable_snapshots_do_not_change_baseline(self):
        self.assertFalse(self.tokens(1_000_000, partial=True)['initialized'])
        self.tokens(2_000_000)
        for flag in ('partial', 'unavailable', 'refresh_failed', 'format_unknown'):
            self.assertEqual(self.tokens(4_000_000, **{flag: True})['bread'], 0)
        self.assertEqual(self.tokens(4_000_000)['bread'], 2)

    def test_invalid_counters_and_monthly_total_ignored(self):
        for value in (True, -1, '1000000', 3.5, 2**63, None):
            self.assertFalse(self.tokens(value)['initialized'])
        self.assertFalse(self.care.update(dict(total=1_000_000, source_id='local'))['initialized'])

    def test_source_switch_has_separate_baseline(self):
        self.tokens(10_000_000)
        self.care.update(dict(all_total=50_000_000, source_id='other'))
        self.assertEqual(self.tokens(10_500_000)['bread'], 0)
        state = self.care.update(dict(all_total=50_500_000, source_id='other'))
        self.assertEqual((state['bread'], state['remainder']), (1, 0))

    def test_full_or_empty_inventory_does_not_waste_bread(self):
        self.tokens(0);self.tokens(1_000_000)
        self.assertFalse(self.care.update(feed=True)['fed'])
        self.now += 300
        state = self.care.update(feed=True)
        self.assertEqual((state['fullness'], state['bread'], state['fed']), (100, 0, True))
        self.now += 300
        self.assertEqual(self.care.update(feed=True)['fullness'], 99)

    def test_clock_rollback_does_not_refill_or_double_charge(self):
        self.now += 600
        self.assertEqual(self.care.update()['fullness'], 98)
        self.now -= 600
        self.assertEqual(self.care.update()['fullness'], 98)
        self.now += 900
        self.assertEqual(self.care.update()['fullness'], 97)

    def test_concurrent_snapshots_and_feeding_are_atomic(self):
        self.tokens(0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.tokens(1_000_000), range(2)))
        self.assertEqual(self.care.update()['bread'], 1)
        self.now += 80 * 300
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(lambda _: self.care.update(feed=True), range(2)))
        self.assertEqual(sum(s['fed'] for s in states), 1)
        self.assertEqual(self.care.update()['fullness'], 50)

    def test_locked_store_does_not_spend_or_award(self):
        self.tokens(0)
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(sqlite3.OperationalError):
                self.tokens(1_000_000)
        self.assertEqual(self.tokens(1_000_000)['bread'], 1)

    def test_valid_recent_records_reward_even_when_old_logs_are_unreadable(self):
        snapshot=dict(source_id='local',reward_since=self.care.reward_since,
                      readable_files=1,partial=True,format_unknown=True,
                      all_total=99_000_000,reward_total=1_250_000)
        state=self.care.update(snapshot)
        self.assertEqual((state['bread'],state['remainder']),(1,250_000))
        self.care=PetCare(self.path,clock=lambda:self.now)
        snapshot['all_total']+=20_000_000  # recovery of old records is not a reward
        self.assertEqual(self.care.update(snapshot)['bread'],1)
        snapshot['reward_total']+=750_000
        self.assertEqual(self.care.update(snapshot)['bread'],2)

    def test_wrong_window_or_no_readable_files_does_not_reward(self):
        snapshot=dict(source_id='local',reward_since=self.care.reward_since-1,
                      readable_files=1,all_total=99_000_000,reward_total=2_000_000)
        self.assertFalse(self.care.update(snapshot)['initialized'])
        snapshot.update(reward_since=self.care.reward_since,readable_files=0)
        self.assertFalse(self.care.update(snapshot)['initialized'])


if __name__ == '__main__':
    unittest.main()
