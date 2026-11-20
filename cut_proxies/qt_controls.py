"""Small shared Qt form helpers and a main-thread result dispatcher."""

import threading

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPalette, QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QBoxLayout, QButtonGroup, QCheckBox,
                               QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLayout,
                               QLabel, QLineEdit, QMenuBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QStyle,
                               QTabBar, QToolButton, QVBoxLayout, QWidget)


_WORKSPACE_STYLE = """
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
    background: palette(highlight); color: @accent_text; border-color: palette(highlight);
}
QPushButton[role="workspaceTool"], QToolButton[role="workspaceTool"] {
    background: palette(button); border: 1px solid palette(midlight);
    border-radius: 5px; padding: 4px 8px;
}
QPushButton[role="workspaceTool"]:hover, QPushButton[role="workspaceTool"]:focus,
QToolButton[role="workspaceTool"]:hover, QToolButton[role="workspaceTool"]:focus {
    border-color: palette(highlight);
}
QToolButton[role="workspaceTool"]:disabled { background: @disabled_button; color: @disabled_text; }
QPushButton[role="holder"]:checked {
    background: palette(highlight); color: @accent_text; border-radius: 5px;
}
QPushButton:disabled { background: @disabled_button; color: @disabled_text; }
QComboBox:disabled { background: @disabled_button; color: @disabled_text; }
QPushButton[role="primary"] {
    background: palette(highlight); color: @accent_text; font-weight: 650;
    border: 1px solid palette(highlight); border-radius: 6px; padding: 6px 14px;
}
QPushButton[role="primary"]:hover, QPushButton[role="primary"]:focus {
    border-color: @accent_text;
}
QPushButton[role="primary"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="primary"]:disabled {
    background: @disabled_button; color: @disabled_text; border-color: palette(midlight);
}
QPushButton[role="secondary"] {
    background: palette(button); color: palette(button-text);
    border: 1px solid palette(midlight); border-radius: 6px; padding: 6px 14px;
}
QPushButton[role="secondary"]:hover, QPushButton[role="secondary"]:focus {
    border-color: palette(highlight);
}
QPushButton[role="secondary"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="secondary"]:disabled {
    background: @disabled_button; color: @disabled_text; border-color: palette(midlight);
}
QPushButton[role="destructive"] {
    background: @destructive; color: @destructive_text; font-weight: 650;
    border: 1px solid @destructive_edge; border-radius: 6px; padding: 6px 14px;
}
QPushButton[role="destructive"]:hover, QPushButton[role="destructive"]:focus {
    background: @destructive_edge; border-color: @destructive_text;
}
QPushButton[role="destructive"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="destructive"]:disabled {
    background: @disabled_button; color: @disabled_text; border-color: palette(midlight);
}
QLabel[role="warning"] { color: @destructive; font-weight: 600; }
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
QMenuBar#workspaceMenu::item {
    background: transparent; color: palette(window-text);
    border-radius: 5px; padding: 5px 10px;
}
QMenuBar#workspaceMenu::item:selected,
QMenuBar#workspaceMenu::item:pressed { background: palette(alternate-base); }
QSplitter::handle { background: transparent; width: 12px; }
QSplitter::handle:horizontal {
    background: palette(midlight);
    border-left: 5px solid palette(window);
    border-right: 5px solid palette(window);
}
QSplitter::handle:hover { background: palette(midlight); }
QSplitter::handle:horizontal:hover { background: palette(highlight); }
"""


def _contrast(first, second):
    def luminance(color):
        def linear(channel):
            return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

        return sum(weight * linear(value) for weight, value in zip((0.2126, 0.7152, 0.0722), color.getRgbF()))

    dark, light = sorted((luminance(first), luminance(second)))
    return (light + 0.05) / (dark + 0.05)


def _legible(preferred, background):
    if _contrast(preferred, background) >= 4.5:
        return preferred
    return max((QColor("black"), QColor("white")), key=lambda color: _contrast(color, background))


def workspace_style(palette):
    active, role = QPalette.ColorGroup.Active, QPalette.ColorRole
    button = palette.color(active, role.Button)
    label = _legible(palette.color(active, role.ButtonText), button)
    subdued = QColor.fromRgbF(*(surface + (text - surface) * 0.72
                                for surface, text in zip(button.getRgbF()[:3], label.getRgbF()[:3])))
    disabled = subdued if _contrast(subdued, button) >= 4.5 else label
    accent = _legible(palette.color(active, role.HighlightedText), palette.color(active, role.Highlight))
    # A warning red the theme's own text can sit on, rather than a fixed pair
    # that can vanish into a dark or a high-contrast palette.
    window = palette.color(active, role.Window)
    destructive = QColor("#c0392b") if window.lightnessF() > 0.5 else QColor("#e05c54")
    destructive_text = _legible(palette.color(active, role.HighlightedText), destructive)
    destructive_edge = destructive.darker(125) if window.lightnessF() > 0.5 else destructive.lighter(115)
    return (_WORKSPACE_STYLE.replace("@accent_text", accent.name())
            .replace("@destructive_text", destructive_text.name())
            .replace("@destructive_edge", destructive_edge.name())
            .replace("@destructive", destructive.name())
            .replace("@disabled_button", button.name()).replace("@disabled_text", disabled.name()))


class SettingsWheel(QObject):
    """Route the wheel to the pane, including when a settings field has focus."""

    def __init__(self, window):
        super().__init__(window)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, widget, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        if isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QAbstractSpinBox):
            widget = widget.parentWidget()
        if not isinstance(widget, (QComboBox, QAbstractSpinBox, QSlider)) or not self.parent().isAncestorOf(widget):
            return False
        ancestor = widget.parentWidget()
        while ancestor is not None and not isinstance(ancestor, QAbstractScrollArea):
            ancestor = ancestor.parentWidget()
        if ancestor is not None:
            viewport = ancestor.viewport()
            position = viewport.mapFromGlobal(event.globalPosition())
            wheel = QWheelEvent(QPointF(position), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                                event.source(), event.pointingDevice())
            QApplication.sendEvent(viewport, wheel)
        return True


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


def button(label, callback, *, primary=False, role=None, icon=None):
    widget = QPushButton(label)
    if primary:
        widget.setProperty("role", "primary")
    elif role:
        widget.setProperty("role", role)
    widget.setMinimumHeight(32)
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
        # Checkable, so assistive technology hears which workspace is current.
        # Each workspace has its own header, so its state never changes:
        # a click switches workspace and the check is put back afterwards.
        item = button(name, callback)
        item.setCheckable(True)
        item.setChecked(name == active)
        item.clicked.connect(lambda _checked=False, item=item, keep=name == active: item.setChecked(keep))
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
    # Panels wrap and stack instead: a horizontal scrollbar hides controls
    # at the compact widths this has to work at.
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


class Page(QWidget):
    def __init__(self):
        super().__init__()
        self.setProperty("role", "settingsPage")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(6, 9, 9, 12)
        self.body.setSpacing(12)

    def group(self, title, *, action=None):
        box = QGroupBox(title)
        box.setProperty("role", "card")
        self.body.addWidget(box)
        form = Form(box)
        form.setContentsMargins(12, 15, 12, 10)
        if action is not None:
            form.header_action(action)
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


class AdaptiveActions(QWidget):
    """Keep action labels intact when a settings pane gets narrow."""

    def __init__(self, buttons):
        super().__init__()
        self.buttons = buttons
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        for item in buttons:
            self.row.addWidget(item, 1)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(max(item.minimumSizeHint().width() for item in self.buttons))
        return hint

    def event(self, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            needed = sum(item.minimumSizeHint().width() for item in self.buttons)
            needed += self.row.spacing() * (len(self.buttons) - 1)
            direction = (QBoxLayout.Direction.TopToBottom if self.width() < needed
                         else QBoxLayout.Direction.LeftToRight)
            if self.row.direction() != direction:
                self.row.setDirection(direction)
                self.updateGeometry()
        return super().event(event)


class CheckCaption(QLabel):
    def __init__(self, label, checkbox):
        super().__init__(label)
        self.checkbox = checkbox
        self.setWordWrap(True)
        self.setBuddy(checkbox)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setEnabled(checkbox.isEnabled())
        checkbox.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.checkbox and event.type() == QEvent.Type.EnabledChange:
            self.setEnabled(self.checkbox.isEnabled())
        return super().eventFilter(watched, event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.checkbox.setFocus(Qt.FocusReason.MouseFocusReason)
            self.checkbox.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class Form(QFormLayout):
    def __init__(self, widget):
        super().__init__(widget)
        self.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.setHorizontalSpacing(10)
        self.setVerticalSpacing(7)

    def header_action(self, widget):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.addStretch(1)
        layout.addWidget(widget)
        self.addRow(row)
        return widget

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
        """A checkbox whose label wraps: QCheckBox text never does, so a long
        one is clipped at the widths the inspector has to work at."""
        field = QCheckBox(label)
        field.setChecked(value)
        if changed:
            field.toggled.connect(lambda: changed())
        if len(label) > 14:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            field.setText("")
            field.setAccessibleName(label)
            caption = CheckCaption(label, field)
            layout.addWidget(field)
            layout.addWidget(caption, 1)
            field.caption = caption
            self.addRow(row)
            return field
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


class ResponsiveInspector(QWidget):
    def __init__(self):
        super().__init__()
        self.setProperty("role", "settings")
        self.setAccessibleName("Design properties")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 0)
        header_layout.setSpacing(0)

        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        header_layout.addWidget(self.tabs)

        self.combo = QComboBox()
        self.combo.setAccessibleName("Design properties section")
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        self.combo.hide()
        header_layout.addWidget(self.combo)

        layout.addWidget(header)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

    def addTab(self, widget, label):
        self.tabs.addTab(label)
        self.combo.addItem(label)
        self.stack.addWidget(widget)

    def currentIndex(self):
        return self.stack.currentIndex()

    def setCurrentIndex(self, index):
        self.stack.setCurrentIndex(index)
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)

    def currentWidget(self):
        return self.stack.currentWidget()

    def count(self):
        return self.stack.count()

    def widget(self, index):
        return self.stack.widget(index)

    def setDocumentMode(self, enabled):
        self.tabs.setDocumentMode(enabled)

    def tabBar(self):
        return self.tabs

    def setUsesScrollButtons(self, enabled):
        self.tabs.setUsesScrollButtons(enabled)

    def _on_tab_changed(self, index):
        self.setCurrentIndex(index)

    def _on_combo_changed(self, index):
        self.setCurrentIndex(index)

    #: Hysteresis, so a width that sits exactly on the threshold cannot flip
    #: between the two presentations on every resize.
    SWITCH_MARGIN = 24

    def natural_tab_width(self):
        """What the tabs want, not what they were stretched to.

        `setExpanding(True)` makes `tabRect` fill the bar, so measuring it
        compares the bar against itself and always reads as too narrow.
        """
        return sum(self.tabs.tabSizeHint(i).width() for i in range(self.tabs.count()))

    def wants_combo(self, width):
        needed = self.natural_tab_width() + 16
        if self.combo.isVisibleTo(self):
            return width < needed + self.SWITCH_MARGIN
        return width < needed

    def apply_width(self, width):
        narrow = self.wants_combo(width)
        if narrow == self.combo.isVisibleTo(self):
            return narrow
        self.tabs.setVisible(not narrow)
        self.combo.setVisible(narrow)
        return narrow

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_width(self.width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_width(self.width())
