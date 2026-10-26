import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QMediaPlayer
from action_audio import ActionAudio

APP=QCoreApplication.instance() or QCoreApplication([])


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'sound.wav').write_bytes(b'fixture')
        self.item={'audioFile':'sound.wav'}
        with patch('action_audio.QAudioOutput'), patch('action_audio.QMediaPlayer') as factory:
            self.audio=ActionAudio();self.player=factory.return_value
        self.player.mediaStatus.return_value=QMediaPlayer.MediaStatus.LoadedMedia
        self.player.duration.return_value=5000

    def test_only_manual_enabled_original_speed(self):
        for manual,enabled,speed in [(False,True,1),(True,False,1),(True,True,.75)]:
            self.assertFalse(self.audio.start(self.root,self.item,manual=manual,enabled=enabled,speed=speed))
        self.player.play.assert_not_called()
        self.assertFalse(self.audio.start(self.root,{'audioFile':'../outside.wav'},manual=True,enabled=True,speed=1))

    def test_late_load_starts_at_current_animation_time_and_corrects_drift(self):
        self.audio.start(self.root,self.item,manual=True,enabled=True,speed=1)
        self.audio.sync(.8,enabled=True,speed=1)
        self.player.setPosition.assert_called_with(800);self.player.play.assert_called_once()
        self.player.position.return_value=850
        self.audio.sync(1.2,enabled=True,speed=1)
        self.player.setPosition.assert_called_with(1200)
        self.player.play.assert_called_once()

    def test_mute_cancel_and_end_do_not_restart(self):
        for options in [dict(enabled=False,speed=1),dict(enabled=True,speed=.75),dict(enabled=True,speed=1,ended=True)]:
            self.audio.start(self.root,self.item,manual=True,enabled=True,speed=1)
            self.audio.sync(1,**options)
            self.assertFalse(self.audio.active)
            self.audio.sync(2,enabled=True,speed=1)
        self.player.play.assert_not_called()


if __name__=='__main__':unittest.main()
