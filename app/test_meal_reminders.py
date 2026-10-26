import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from desktop_core import normalized
from meal_reminders import MealReminders


class MealReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'reminders.sqlite3'
        self.reminders = MealReminders(self.path)
        self.settings = normalized({})

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, hour, minute, day=26):
        return datetime(2026, 10, day, hour, minute, 10)

    def test_default_times_once_per_slot_survive_restart_and_repeat_tomorrow(self):
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 39)))
        self.assertTrue(self.reminders.claim(self.settings, self.at(11, 40)))
        self.reminders = MealReminders(self.path)
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 40)))
        self.assertTrue(self.reminders.claim(self.settings, self.at(17, 20)))
        self.assertTrue(self.reminders.claim(self.settings, self.at(11, 40, 27)))

    def test_disabled_or_hidden_defers_but_old_missed_reminders_are_not_replayed(self):
        self.assertFalse(self.reminders.claim(dict(self.settings, meal_reminders=False), self.at(11, 40)))
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 40), available=False))
        self.assertTrue(self.reminders.claim(self.settings, self.at(11, 43)))
        self.assertFalse(self.reminders.claim(self.settings, self.at(17, 25)))

    def test_custom_midnight_and_identical_times_coalesce(self):
        self.settings.update(meal_lunch_time='00:00', meal_dinner_time='00:00')
        self.assertTrue(self.reminders.claim(self.settings, self.at(0, 0)))
        self.assertFalse(self.reminders.claim(self.settings, self.at(0, 1)))
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 40)))
        self.assertTrue(self.reminders.claim(self.settings, self.at(0, 0, 27)))

    def test_editing_delivered_slot_or_clock_rollback_does_not_repeat(self):
        self.assertTrue(self.reminders.claim(self.settings, self.at(11, 40)))
        self.settings['meal_lunch_time'] = '11:42'
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 42)))
        self.settings['meal_lunch_time'] = '11:40'
        self.assertFalse(self.reminders.claim(self.settings, self.at(11, 40)))

    def test_idle_ticks_do_not_access_database_and_two_instances_deduplicate(self):
        other = MealReminders(self.path)
        with patch.object(self.reminders, 'connect', side_effect=AssertionError('idle database access')):
            for _ in range(100):
                self.assertFalse(self.reminders.claim(self.settings, self.at(10, 0)))
        self.assertTrue(self.reminders.claim(self.settings, self.at(11, 40)))
        self.assertFalse(other.claim(self.settings, self.at(11, 40)))

    def test_invalid_saved_times_fall_back_without_changing_other_settings(self):
        for value in ('25:00', '12:60', '9:30', '', None, 1140):
            settings = normalized(dict(meal_lunch_time=value, meal_dinner_time=value, sound=False))
            self.assertEqual(settings['meal_lunch_time'], '11:40')
            self.assertEqual(settings['meal_dinner_time'], '17:20')
            self.assertFalse(settings['sound'])


if __name__ == '__main__':
    unittest.main()
