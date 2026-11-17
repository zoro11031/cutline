"""Small shared Qt form helpers and a main-thread result dispatcher."""

import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMenuBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStyle,
                               QToolButton, QVBoxLayout, QWidget)


WORKSPACE_STYLE = """
QWidget#workspace { background: palette(window); }
QLabel[role="title"] { font-size: 19px; font-weight: 650; }
QLabel[role="heading"] { font-weight: 650; }
QLabel[role="metric"] { padding: 3px 7px; }
QFrame#workspaceBar {
    background: palette(base);
    border: 1px solid palette(midlight);
    border-radius: 9px;
}
QFrame#workspaceTools { border-bottom: 1px solid palette(midlight); }
QScrollArea[role="settings"], QWidget[role="settingsPage"] { background: palette(window); }
QTabWidget[role="settings"]::pane { border: 0; border-top: 1px solid palette(midlight); }
QTabWidget[role="settings"] QTabBar::tab {
    padding: 8px 10px; margin: 0 1px; border-bottom: 2px solid transparent;
}
QTabWidget[role="settings"] QTabBar::tab:selected {
    background: palette(base); border-bottom-color: palette(highlight); font-weight: 650;
}
QTabWidget[role="settings"] QTabBar::tab:hover:!selected { background: palette(alternate-base); }
QGroupBox[role="card"] {
    background: palette(base); border: 1px solid palette(midlight);
    border-radius: 8px; margin-top: 9px; font-weight: 650;
}
QGroupBox[role="card"]::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    background: palette(base); border-radius: 3px; left: 11px; padding: 1px 5px;
}
QGroupBox[role="card"] QLabel { font-weight: normal; }
QGroupBox[role="card"] QAbstractSpinBox, QGroupBox[role="card"] QComboBox,
QGroupBox[role="card"] QLineEdit, QGroupBox[role="card"] QPushButton { min-height: 23px; }
QCheckBox::indicator:unchecked {
    width: 12px; height: 12px; background: palette(base); border: 1px solid palette(mid); border-radius: 3px;
}
QCheckBox::indicator:unchecked:hover { border-color: palette(highlight); }
QPushButton[role="workspaceTab"] {
    background: palette(alternate-base); border: 1px solid palette(midlight);
    border-radius: 6px; padding: 5px 17px; font-weight: 650;
}
QPushButton[role="workspaceTab"]:hover, QPushButton[role="workspaceTab"]:focus {
    border-color: palette(highlight);
}
QPushButton[role="workspaceTab"][active="true"] {
    background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight);
}
QPushButton[role="workspaceTool"], QToolButton[role="workspaceTool"] {
    background: palette(button); border: 1px solid palette(midlight);
    border-radius: 5px; padding: 4px 8px;
}
QPushButton[role="workspaceTool"]:hover, QPushButton[role="workspaceTool"]:focus,
QToolButton[role="workspaceTool"]:hover, QToolButton[role="workspaceTool"]:focus {
    border-color: palette(highlight);
}
QToolButton[role="workspaceTool"]:disabled { color: palette(mid); }
QPushButton[role="holder"]:checked {
    background: palette(highlight); color: palette(highlighted-text); border-radius: 5px;
}
QPushButton:disabled { color: palette(mid); }
QPushButton[role="primary"] {
    background: palette(highlight); color: palette(highlighted-text); font-weight: 650;
    border: 1px solid palette(highlight); border-radius: 6px; padding: 6px 14px;
}
QPushButton[role="primary"]:hover, QPushButton[role="primary"]:focus {
    border-color: palette(highlighted-text);
}
QPushButton[role="primary"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="primary"]:disabled {
    background: palette(button); color: palette(mid); border-color: palette(midlight);
}
QPlainTextEdit[role="diagnostics"] { font-family: monospace; }
QToolButton[role="disclosure"] {
    border: 1px solid palette(midlight); border-radius: 6px; padding: 5px 8px;
    background: palette(base); font-weight: 600;
}
QToolButton[role="disclosure"]:hover, QToolButton[role="disclosure"]:focus {
    border-color: palette(highlight);
}
QSlider[role="cutSlider"]::groove:horizontal {
    height: 4px; border-radius: 2px; background: palette(midlight);
}
QSlider[role="cutSlider"]::sub-page:horizontal { border-radius: 2px; background: palette(highlight); }
QSlider[role="cutSlider"]::handle:horizontal {
    width: 12px; border: 1px solid palette(highlight); border-radius: 7px;
    margin: -5px 0; background: palette(highlight);
}
QSlider[role="cutSlider"]::handle:horizontal:focus,
QSlider[role="cutSlider"]::handle:horizontal:hover { border: 1px solid palette(window-text); }
QSlider[role="cutSlider"]::handle:horizontal:disabled,
QSlider[role="cutSlider"]::sub-page:horizontal:disabled { background: palette(mid); }
QMenuBar { padding: 2px 4px; }
QMenuBar::item { padding: 5px 10px; }
QMenuBar#workspaceMenu { background: transparent; padding: 0; }
QSplitter::handle { background: transparent; width: 12px; }
QSplitter::handle:horizontal {
    background: palette(midlight);
    border-left: 5px solid palette(window);
    border-right: 5px solid palette(window);
}
QSplitter::handle:hover { background: palette(midlight); }
QSplitter::handle:horizontal:hover { background: palette(highlight); }
"""


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


def button(label, callback, *, primary=False, icon=None):
    widget = QPushButton(label)
    if primary:
        widget.setProperty("role", "primary")
    if icon:
        widget.setIcon(themed_icon(widget, *icon))
    widget.clicked.connect(lambda: callback())
    return widget


def themed_icon(widget, name, fallback):
    return QIcon.fromTheme(name, widget.style().standardIcon(fallback))


def styled_label(text="", role="heading"):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    return label


def bar(parent, margins=(12, 6, 12, 6)):
    surface = QFrame(parent)
    surface.setObjectName("workspaceBar")
    layout = QHBoxLayout(surface)
    layout.setContentsMargins(*margins)
    layout.setSpacing(8)
    return surface, layout


def workspace_layout(parent):
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(14, 10, 14, 12)
    layout.setSpacing(9)
    return layout


def workspace_tools(parent):
    surface, layout = bar(parent, (2, 0, 2, 0))
    surface.setObjectName("workspaceTools")
    return surface, layout


def workspace_tool_button(action):
    widget = QToolButton()
    widget.setDefaultAction(action)
    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    widget.setProperty("role", "workspaceTool")
    return widget


def workspace_header(parent, design, send, *, active):
    header, top = bar(parent, (6, 4, 8, 4))
    menu = QMenuBar(header)
    menu.setObjectName("workspaceMenu")
    menu.setNativeMenuBar(False)
    top.addWidget(menu)
    top.addStretch(1)
    switch = QWidget(header)
    row = QHBoxLayout(switch)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    buttons = []
    for name, callback in (("Design", design), ("Send", send)):
        item = button(name, callback)
        item.setProperty("role", "workspaceTab")
        item.setProperty("active", name == active)
        if name == active:
            item.setAccessibleDescription("Current workspace")
        row.addWidget(item)
        buttons.append(item)
    top.addWidget(switch)
    return header, menu, *buttons


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
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setProperty("role", "settings")
    area.setWidgetResizable(True)
    area.setWidget(content)
    return area


class Page(QWidget):
    def __init__(self):
        super().__init__()
        self.setProperty("role", "settingsPage")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(6, 9, 9, 12)
        self.body.setSpacing(12)

    def group(self, title):
        box = QGroupBox(title)
        box.setProperty("role", "card")
        self.body.addWidget(box)
        form = Form(box)
        form.setContentsMargins(12, 15, 12, 10)
        return form

    def section(self):
        section = Page()
        section.body.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(section)
        return section

    def advanced(self, title="Advanced Options"):
        toggle = QToolButton(text=title, checkable=True, arrowType=Qt.ArrowType.RightArrow,
                             toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setProperty("role", "disclosure")
        self.body.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        section = self.section()
        section.disclosure = toggle
        section.hide()
        toggle.toggled.connect(section.setVisible)
        toggle.toggled.connect(lambda shown: toggle.setArrowType(
            Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow))
        return section

    def finish(self):
        self.body.addStretch(1)
        return scroll(self)


class Note(QLabel):
    """A wrapping label that reserves room for every line inside a scrolling form."""

    def __init__(self, text):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def setText(self, text):
        super().setText(text)
        self.resize_contents()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_contents()

    def resize_contents(self):
        if self.width() > 0:
            self.setMinimumHeight(max(0, self.heightForWidth(self.width())))


class Form(QFormLayout):
    def __init__(self, widget):
        super().__init__(widget)
        self.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.setHorizontalSpacing(10)
        self.setVerticalSpacing(7)

    def choice(self, label, choices, selected=None, changed=None):
        field = QComboBox()
        field.setMinimumContentsLength(11)
        field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        set_choices(field, choices, selected)
        if changed:
            field.currentIndexChanged.connect(lambda: changed())
        self.addRow(label, field)
        return field

    def number(self, label, value, minimum, maximum, step=1, decimals=0, changed=None, *,
               slider=False, special_text=""):
        field = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            field.setDecimals(decimals)
        field.setRange(minimum, maximum)
        field.setSingleStep(step)
        field.setValue(value)
        field.setKeyboardTracking(False)
        if special_text:
            field.setSpecialValueText(special_text)
        if changed:
            field.valueChanged.connect(lambda: changed())
        if slider:
            row = QWidget()
            content = QHBoxLayout(row)
            content.setContentsMargins(0, 0, 0, 0)
            content.setSpacing(9)
            track = QSlider(Qt.Orientation.Horizontal)
            track.setProperty("role", "cutSlider")
            track.setAccessibleName(label)
            field.setAccessibleName(f"{label} value")
            track.setRange(minimum, maximum)
            track.setSingleStep(step)
            track.setValue(value)
            track.setTracking(False)
            track.valueChanged.connect(field.setValue)
            field.valueChanged.connect(track.setValue)
            content.addWidget(track, 1)
            field.setMinimumWidth(76)
            field.setSizePolicy(QSizePolicy.Policy.Fixed, field.sizePolicy().verticalPolicy())
            content.addWidget(field)
            field.slider = track
            self.addRow(label, row)
        else:
            self.addRow(label, field)
        return field

    def check(self, label, value=False, changed=None):
        field = QCheckBox(label)
        field.setChecked(value)
        if changed:
            field.toggled.connect(lambda: changed())
        self.addRow(field)
        return field

    def holders(self, changed):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        field = QSpinBox(row)
        field.setRange(1, 2)
        field.hide()
        picker = QButtonGroup(row)
        for slot in (1, 2):
            tab = QPushButton(f"Tool Holder {slot}", checkable=True)
            tab.setProperty("role", "holder")
            tab.setMinimumHeight(28)
            picker.addButton(tab, slot)
            layout.addWidget(tab, 1)
        picker.button(1).setChecked(True)
        picker.idClicked.connect(field.setValue)
        field.valueChanged.connect(lambda slot: picker.button(slot).setChecked(True))
        field.valueChanged.connect(lambda: changed())
        field.holders = picker
        self.addRow(row)
        return field

    def text(self, label, value="", changed=None):
        field = QLineEdit(value)
        if changed:
            field.editingFinished.connect(changed)
        self.addRow(label, field)
        return field

    def note(self, text=""):
        field = Note(text)
        self.addRow(field)
        return field

    def push(self, label, callback):
        field = button(label, callback)
        self.addRow(field)
        return field

    def actions(self, *buttons):
        row = QHBoxLayout()
        row.setSpacing(6)
        for widget in buttons:
            row.addWidget(widget, 1)
        self.addRow(row)
