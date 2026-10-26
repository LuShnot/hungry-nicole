"""A passive text notice independent of Codex's status panel."""
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QLabel
from typography import ui_font
from ui_theme import SURFACE, INK, BORDER
from meal_reminders import MEAL_MESSAGE


class MealNotice(QLabel):
    def __init__(self, pet):
        super().__init__(MEAL_MESSAGE, None)
        self.pet = pet
        self.setFont(ui_font(14))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f'QLabel{{color:{INK};background:{SURFACE};border:1px solid {BORDER};border-radius:12px;padding:12px 16px;}}')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        pet.installEventFilter(self)
        pet.bubble.installEventFilter(self)

    def present(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        if self.pet.settings.get('on_top', True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        anchor = self.pet.bubble if self.pet.bubble.isVisible() else self.pet
        rect = anchor.frameGeometry()
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.adjustSize()
        x = max(area.left(), min(rect.center().x() - self.width() // 2, area.right() - self.width() + 1))
        y = rect.top() - self.height() - 8
        if y < area.top():
            y = rect.bottom() + 8
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(QPoint(x, y))
        self.show()  # Never activate/raise or navigate the status panel.
        self.timer.start(12000)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Close):
            self.hide()
        return False

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
