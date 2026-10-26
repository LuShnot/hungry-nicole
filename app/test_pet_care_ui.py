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

    def test_bread_animation_only_after_successful_feeding(self):
        item = dict(id='17', file='animations/17.webp')
        self.pet.manifest['feedAnimation'] = item
        self.usage(5_000_000);self.usage(6_000_000)
        calls = []
        def chosen(action, **kwargs):
            calls.append((action, kwargs, self.pet._feeding_bread))
        with patch.object(self.pet, 'choose', side_effect=chosen):
            self.pet.update_care(feed=True)  # full: preserve bread, no animation
            self.assertEqual(calls, [])
            self.assertEqual(self.pet.care_state['bread'], 1)
            self.now += 60 * 300
            menu = QMenu();self.pet.build_menu(menu)
            food = next(a.menu() for a in menu.actions() if a.text() == '食物')
            bread = next(a for a in food.actions() if a.objectName() == 'feed_bread')
            bread.trigger()
            self.assertEqual(calls, [(item, {'manual': True}, True)])
            self.assertFalse(self.pet._feeding_bread)
            self.assertEqual(self.pet.care_state['fullness'], 70)
            self.assertEqual(self.pet.care_state['bread'], 0)
            bread.trigger()  # stale action: no second animation without bread
            self.assertEqual(len(calls), 1)

    def test_bread_excluded_from_generic_actions_even_for_legacy_manifest(self):
        item = dict(id='17', file='animations/17.webp')
        self.pet.variants.append(item)
        for items in self.pet.action_groups().values():
            self.assertNotIn(item, items)
        with patch.object(self.pet, 'play') as play:
            self.pet.choose(item, interactive=True)
            self.pet.choose(item, manual=True)
            play.assert_not_called()
        self.assertFalse(self.pet.play(item, 'variant'))

    def test_macos_passive_windows_do_not_raise_or_accept_keys(self):
        import sys
        from PySide6.QtCore import Qt
        if sys.platform != 'darwin':self.skipTest('macOS window policy')
        self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        self.assertTrue(self.pet.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(self.pet.bubble.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        with patch.object(self.pet.bubble, 'raise_') as raised:
            self.pet.position_bubble()
            raised.assert_not_called()
        self.pet.bubble._editing_flags(True)
        self.assertFalse(self.pet.bubble.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.pet.bubble._editing_flags(False)
        self.assertTrue(self.pet.bubble.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus)

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
