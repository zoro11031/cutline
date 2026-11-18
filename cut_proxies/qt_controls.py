"""Small shared Qt form helpers and a main-thread result dispatcher."""

import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)


class Tasks(QObject):
    result = Signal(object, object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.result.connect(self.deliver)

    def run(self, work, callback):
        def execute():
            try:
                value = work()
            except Exception as error:
                self.result.emit(callback, None, error)
            else:
                self.result.emit(callback, value, None)

        threading.Thread(target=execute, daemon=True).start()

    @Slot(object, object, object)
    def deliver(self, callback, value, error):
        callback(value, error)


def button(label, callback):
    widget = QPushButton(label)
    widget.clicked.connect(lambda: callback())
    return widget


def set_choices(widget, choices, selected=None):
    current = widget.currentData() if selected is None else selected
    blocked = widget.blockSignals(True)
    widget.clear()
    items = choices.items() if hasattr(choices, "items") else ((v, str(v)) for v in choices)
    for key, title in items:
        widget.addItem(title, key)
    widget.setCurrentIndex(max(0, widget.findData(current)))
    widget.blockSignals(blocked)


def scroll(content):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


class Page(QWidget):
    def __init__(self):
        super().__init__()
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(10)

    def group(self, title):
        box = QGroupBox(title)
        self.body.addWidget(box)
        return Form(box)

    def finish(self):
        self.body.addStretch(1)
        return scroll(self)


class Form(QFormLayout):
    def choice(self, label, choices, selected=None, changed=None):
        field = QComboBox()
        set_choices(field, choices, selected)
        if changed:
            field.currentIndexChanged.connect(lambda: changed())
        self.addRow(label, field)
        return field

    def number(self, label, value, minimum, maximum, step=1, decimals=0, changed=None):
        field = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            field.setDecimals(decimals)
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setValue(value)
        field.setKeyboardTracking(False)
        if changed:
            field.valueChanged.connect(lambda: changed())
        self.addRow(label, field)
        return field

    def check(self, label, value=False, changed=None):
        field = QCheckBox(label)
        field.setChecked(value)
        if changed:
            field.toggled.connect(lambda: changed())
        self.addRow(field)
        return field

    def text(self, label, value="", changed=None):
        field = QLineEdit(value)
        if changed:
            field.editingFinished.connect(changed)
        self.addRow(label, field)
        return field

    def note(self, text=""):
        field = QLabel(text)
        field.setWordWrap(True)
        self.addRow(field)
        return field

    def push(self, label, callback):
        field = button(label, callback)
        self.addRow(field)
        return field
