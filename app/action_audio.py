"""One-shot local sidecar audio, aligned to the animation presentation clock."""
from pathlib import Path
from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class ActionAudio(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.output = QAudioOutput(self)
        self.output.setVolume(.7)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.setLoops(1)
        self.active = False
        self.pending = False
        self.error = ''
        self.player.errorOccurred.connect(self.failed)

    def failed(self, *args):
        self.error = self.player.errorString()
        self.stop()

    def stop(self):
        self.active = self.pending = False
        self.player.stop()
        self.player.setSource(QUrl())

    def start(self, root, item, *, manual, enabled, speed):
        self.stop()
        if not manual or not enabled or speed != 1 or not item.get('audioFile'):
            return False
        root = Path(root).resolve()
        path = (root / item['audioFile']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != '.wav':
            self.error = '动作音轨缺失或路径无效'
            return False
        self.error = ''
        self.active = self.pending = True
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        return True

    def sync(self, seconds, *, enabled, speed, ended=False):
        if not self.active:
            return
        if not enabled or speed != 1 or ended:
            self.stop()
            return
        ms = round(seconds * 1000)
        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop()
            return
        if self.pending:
            if self.player.mediaStatus() not in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
                return
            if self.player.duration() and ms >= self.player.duration():
                self.stop()
                return
            self.player.setPosition(ms)
            self.player.play()
            self.pending = False
        elif abs(self.player.position() - ms) > 150:
            self.player.setPosition(ms)
