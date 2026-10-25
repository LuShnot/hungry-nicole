import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMenu
from desktop import DesktopPet
from pet_care import HUNGRY_MESSAGE

APP = QApplication.instance() or QApplication([])


class CareUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch(m+'.'+k, root / ('settings.json' if k == 'CONFIG' else ''))
                        for m in ('desktop', 'enhanced') for k in ('CONFIG', 'STATE_DIR')]
        for p in self.patches:p.start()
        self.pet = DesktopPet(monitor=False)
        self.pet.apply_settings(dict(wandering=False, random_actions=False), save=False)
        self.pet.bubble.size_transition.complete()
        APP.processEvents()
        self.now = self.pet.care.clock()
        self.pet.care.clock = lambda: self.now

    def tearDown(self):
        self.pet.close();APP.processEvents()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def usage(self, total):
        self.pet.on_activity(dict(kind='token_usage', snapshot=dict(all_total=total, source_id='test')))

    def test_both_menus_offer_food_and_real_snapshot_feeds_once(self):
        self.usage(5_000_000);self.usage(6_000_000);self.usage(6_000_000)
        self.now += 60 * 300
        for tray in (False, True):
            menu = QMenu();self.pet.build_menu(menu, tray=tray)
            food = next(a.menu() for a in menu.actions() if a.text() == '食物')
            self.assertIn('40%', food.actions()[0].text())
            feed = next(a for a in food.actions() if a.objectName() == 'feed_bread')
            self.assertTrue(feed.isEnabled())
        feed.trigger()
        self.assertEqual(self.pet.care_state['bread'], 0)
        self.assertEqual(self.pet.care_state['fullness'], 70)
        feed.trigger()  # stale menu / double click cannot spend twice
        self.assertEqual(self.pet.care_state['fullness'], 70)

    def test_hidden_bubble_defers_hunger_and_does_not_reopen(self):
        self.pet.apply_settings({'bubble': False}, save=False)
        self.now += 70 * 300
        self.pet.update_care()
        self.assertTrue(self.pet.care_state['pending'])
        self.assertFalse(self.pet.bubble.isVisible())
        self.pet.apply_settings({'bubble': True}, save=False)
        self.pet.bubble.size_transition.complete();APP.processEvents()
        self.pet.status_hold = 0
        self.pet.update_care()
        self.assertEqual(self.pet.bubble.status, HUNGRY_MESSAGE)
        self.assertFalse(self.pet.care_state['pending'])

    def test_completion_priority_defers_hunger(self):
        self.pet.apply_surface_visibility();self.pet.bubble.size_transition.complete();APP.processEvents()
        self.pet.message_priority = 10
        self.pet.status_hold = float('inf')
        self.now += 70 * 300
        self.pet.update_care()
        self.assertTrue(self.pet.care_state['pending'])
        self.pet.status_hold = 0;self.pet.update_care()
        self.assertEqual(self.pet.bubble.status, HUNGRY_MESSAGE)


if __name__ == '__main__':
    unittest.main()
