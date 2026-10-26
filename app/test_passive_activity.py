"""Task and quota bursts must not navigate a user's status panel."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget
from compact_bubble import CompactBubble
APP=QApplication.instance() or QApplication([])

class PassiveActivityTests(unittest.TestCase):
    def setUp(self):
        self.b=CompactBubble();self.b.show();APP.processEvents()
    def tearDown(self):
        self.b.close();self.b.deleteLater();APP.processEvents()
    def settle(self):
        self.b.size_transition.complete();self.b.size_transition.release_landing();APP.processEvents()
    def test_tool_bursts_and_presence_blips_preserve_open_or_compact_mode(self):
        b=self.b
        for compact in (False,True):
            if compact:b.evaluate_mode(b.last_task_update+91);self.settle()
            original=b.size()
            with patch.object(b.size_transition,'begin',wraps=b.size_transition.begin) as begin:
                for state in ['working','thinking','responding','working','thinking']*5:
                    b.codex_open=False;b.evaluate_mode()
                    b.task_updated(dict(state=state,session='test',turn='one'))
                    b.status_indicator.observe(dict(state=state,active=1))
                    b.message('工具执行中' if state=='working' else '思考中')
                    b.quota([dict(name='一周',remaining=80)])
                    APP.processEvents()
                    self.assertEqual(b.auto_compact,compact)
                    self.assertEqual(b.size(),original)
                    self.assertTrue(b.isVisible())
                begin.assert_not_called()
            b.task_updated(dict(kind='complete',state='complete'))
    def test_passive_native_focus_flag_and_explicit_editor_entry(self):
        b=self.b
        self.assertTrue(b.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        b.show_page('chat',QWidget());self.settle()
        self.assertFalse(b.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        b.task_updated(dict(state='working'));self.assertEqual(b.panel_page,'chat')
        b.return_to_status();self.settle()
        self.assertTrue(b.windowHandle().flags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(b.isVisible())

class PresenceTests(unittest.TestCase):
    def test_macos_bundle_and_cli_are_distinguished(self):
        from types import SimpleNamespace
        from desktop_services import codex_desktop_open
        for exe,expected in [('/Applications/Codex.app/Contents/MacOS/Codex',True),('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT',True),('/usr/local/bin/codex',False)]:
            process=SimpleNamespace(info={'name':Path(exe).name},exe=lambda:exe)
            with patch('desktop_services.sys.platform','darwin'),patch('psutil.process_iter',return_value=[process]):
                self.assertEqual(codex_desktop_open(),expected)
    def test_unreadable_process_is_unknown_not_closed(self):
        import psutil
        from unittest.mock import Mock
        from desktop_services import codex_desktop_open
        process=Mock(info={'name':'Codex'});process.exe.side_effect=psutil.AccessDenied(123)
        with patch('desktop_services.sys.platform','darwin'),patch('psutil.process_iter',return_value=[process]):
            self.assertIsNone(codex_desktop_open())

if __name__=='__main__':unittest.main()
