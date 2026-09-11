"""Resizable view of the same original 176 × 220 QEMU framebuffer."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .gui import PhonePanel


class DisplayWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('W800i · Enlarged screen')
        self.setWindowFlag(Qt.WindowType.Window, True)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel('Screen size'))
        self.scale = QComboBox()
        for factor in (1, 2, 3, 4, 5, 6):
            self.scale.addItem(f'{factor}× · {176 * factor} × {220 * factor}', factor)
        self.scale.setCurrentIndex(2)
        toolbar.addWidget(self.scale)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.screen = PhonePanel(screen_only=True)
        self.screen.setAccessibleName('Enlarged W800i screen and keyboard')
        layout.addWidget(self.screen, 1)
        hint = QLabel('Resize freely · Original 4:5 aspect ratio · Phone keyboard controls apply')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.scale.activated.connect(self.apply_scale)
        self.initial_size = False

    def apply_scale(self, *_):
        self.layout().activate()
        factor = self.scale.currentData()
        self.resize(176 * factor + self.width() - self.screen.width(),
                    220 * factor + self.height() - self.screen.height())
        self.screen.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.initial_size:
            self.apply_scale()
            self.initial_size = True
        self.screen.setFocus()

    def closeEvent(self, event):
        self.screen.release_all()
        super().closeEvent(event)

    def reject(self):
        self.screen.release_all()
        super().reject()
