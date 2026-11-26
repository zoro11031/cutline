"""Small shared Qt form helpers and a main-thread result dispatcher."""

import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QBoxLayout, QButtonGroup, QCheckBox,
                               QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLayout,
                               QLabel, QLineEdit, QMenu, QMenuBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QStyle,
                               QStyleOptionToolButton, QStylePainter, QTabBar, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from .design_tools import WORKSPACE_TOOL_GROUPS as TOOL_GROUPS, WORKSPACE_TOOL_NAMES as TOOL_NAMES, \
    WORKSPACE_TOOLS as TOOLS

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
QFrame#designCommands, QSplitter#designSidebar {
    background: palette(base); border: 1px solid palette(midlight); border-radius: 3px;
}
QFrame#designToolOptions { border-top: 1px solid palette(midlight); }
QToolButton[role="designRibbon"] {
    background: palette(button); border: 1px solid palette(midlight);
    border-radius: 5px; padding: 2px 6px; min-height: 22px;
}
QToolButton[role="designRibbon"]:hover, QToolButton[role="designRibbon"]:focus { border-color: palette(highlight); }
QToolButton[role="designRibbon"]:disabled { background: @disabled_button; color: @disabled_text; }
QPushButton[role="compactTool"] { min-height: 22px; padding: 2px 6px; }
QLabel[role="saveBadge"] {
    font-size: 11px; padding: 2px 6px; border-radius: 4px;
    background: palette(alternate-base); color: palette(window-text);
}
QLabel[role="saveBadge"][dirty="true"] { font-weight: 600; color: palette(highlight); }
QFrame#designNotice { background: palette(alternate-base); border: 1px solid palette(highlight); border-radius: 5px; }
QFrame#designNotice[level="warning"] { border-color: @destructive; }
QFrame#designObjectsPane { border-top: 1px solid palette(midlight); }
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
    background: @card; border: 1px solid palette(midlight);
    border-radius: 8px; margin-top: 9px; font-weight: 650;
}
QGroupBox[role="card"]::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    background: @card; border-radius: 3px; left: 11px; padding: 1px 5px;
}
QGroupBox[role="card"] QLabel { font-weight: normal; }
QGroupBox[role="card"] QAbstractSpinBox, QGroupBox[role="card"] QComboBox,
QGroupBox[role="card"] QLineEdit, QGroupBox[role="card"] QPushButton { min-height: 23px; }
QCheckBox::indicator:unchecked {
    width: 12px; height: 12px; background: palette(base); border: 1px solid @check_edge; border-radius: 3px;
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
QFrame[designHeader="true"] QPushButton[role="workspaceTab"][active="true"] {
    background: @design_tool_active; color: white; border-color: @design_tool_active;
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
QFrame#designToolRail { border-right: 1px solid palette(midlight); }
QFrame[role="designDivider"] { background: palette(midlight); }
QToolButton[role="designTool"] {
    background: transparent; border: 2px solid transparent; border-radius: 6px;
}
QToolButton[role="designTool"]:hover { background: palette(alternate-base); }
QToolButton[role="designTool"]:checked, QToolButton[role="designTool"]:pressed { background: @design_tool_active; }
QToolButton[role="designTool"]:focus { border-color: palette(button-text); }
QToolButton[role="designTool"]::menu-indicator { image: none; }
QFrame#designDocument { border: 1px solid palette(midlight); }
QTabBar[role="document"]::tab {
    padding: 5px 14px; border: 0; border-bottom: 2px solid palette(highlight); background: palette(base);
}
QLabel[role="panelTitle"] { font-weight: 600; padding: 2px 0 4px 2px; }
QLabel[role="dockSection"] { font-size: 11px; font-weight: 600; padding-top: 3px; }
QWidget[role="dockBody"] QAbstractSpinBox, QWidget[role="dockBody"] QComboBox,
QWidget[role="dockBody"] QLineEdit, QWidget[role="dockBody"] QPushButton { min-height: 22px; }
QWidget[role="dockBody"] QPushButton { padding: 2px 8px; }
QListWidget[role="designObjects"] { border: 0; border-radius: 5px; }
QListWidget[role="designObjects"]::item { padding: 4px 8px; border-radius: 4px; }
QListWidget[role="designObjects"]::item:selected {
    background: @design_selection; color: palette(text);
}
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


def _control_edge(surface, text):
    """An outline between the surface and its text, just far enough toward
    the text to reach the 3:1 that a control's boundary needs. A theme's
    own `mid` colour falls well short of that in many dark palettes."""
    for amount in (0.45, 0.55, 0.65, 0.8, 1.0):
        edge = QColor.fromRgbF(*(a + (b - a) * amount for a, b in zip(surface.getRgbF()[:3], text.getRgbF()[:3])))
        if _contrast(edge, surface) >= 3:
            return edge
    return text


def _card_surface(base, text):
    """A card a step off the field colour, so fields drawn in `base` read as
    fields inside it in light and dark themes alike."""
    step = 0.035 if base.lightnessF() > 0.5 else 0.07
    return QColor.fromRgbF(*(a + (b - a) * step for a, b in zip(base.getRgbF()[:3], text.getRgbF()[:3])))


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
    surface = palette.color(active, role.Base)
    indigo = QColor("#435aa2")
    selection = QColor.fromRgbF(*(a * 0.72 + b * 0.28
                                  for a, b in zip(surface.getRgbF()[:3], indigo.getRgbF()[:3])))
    return (_WORKSPACE_STYLE.replace("@accent_text", accent.name())
            .replace("@design_selection", selection.name())
            .replace("@design_tool_active", indigo.name())
            .replace("@destructive_text", destructive_text.name())
            .replace("@destructive_edge", destructive_edge.name())
            .replace("@destructive", destructive.name())
            .replace("@card", _card_surface(palette.color(active, role.Base),
                                            palette.color(active, role.Text)).name())
            .replace("@check_edge", _control_edge(palette.color(active, role.Base),
                                                  palette.color(active, role.Text)).name())
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


def workspace_header(parent, design, send, *, active, document=None):
    header, top = bar(parent, (6, 4, 8, 4))
    menu = QMenuBar(header)
    menu.setObjectName("workspaceMenu")
    menu.setNativeMenuBar(False)
    top.addWidget(menu)
    if document is None:
        top.addStretch(1)
    else:
        top.addWidget(document, 1)
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


def design_divider(*, vertical=False):
    divider = QFrame()
    divider.setProperty("role", "designDivider")
    if vertical:
        divider.setFixedSize(1, 22)
    else:
        divider.setFixedHeight(1)
    return divider


def icon_outline(strokes):
    outline = QPainterPath()
    for first, *rest in strokes:
        outline.moveTo(*first)
        for point in rest:
            outline.lineTo(*point)
    return outline


def design_icon(tool, color, size=18):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(icon_outline(tool.strokes))
    painter.end()
    return QIcon(pixmap)


def ribbon_button(tool, callback=None, *, label=None, menu=None, tip=""):
    """Compact icon+text button for the Design command ribbon."""
    widget = QToolButton()
    widget.setProperty("role", "designRibbon")
    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    widget.setText(label or tool.name)
    widget.setAccessibleName(tool.name)
    widget.setToolTip(tip or tool.tooltip)
    widget.setIconSize(QSize(15, 15))
    widget.setIcon(design_icon(tool, widget.palette().buttonText().color(), size=15))
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if menu is not None:
        widget.setMenu(menu)
        widget.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    elif callback is not None:
        widget.clicked.connect(lambda _checked=False: callback())
    return widget


class DesignIconButton(QToolButton):
    def __init__(self, tool, *, checkable=False, action=None, size=34):
        super().__init__()
        if action is not None:
            self.setDefaultAction(action)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("role", "designTool")
        self.setFixedSize(size, size)
        self.flyout = False
        self.set_tool(tool)
        if action is not None:
            self.setToolTip(action.toolTip())

    def set_tool(self, tool):
        self.setText(tool.name)
        self.setAccessibleName(tool.name)
        self.setAccessibleDescription(tool.hint)
        self.setToolTip(tool.tooltip)
        self.outline = icon_outline(tool.strokes)
        self.update()

    def paintEvent(self, _event):
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        palette = option.palette
        color = (_legible(QColor("white"), QColor("#435aa2"))
                 if self.isChecked() or self.isDown() else palette.buttonText().color())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(1.0, (self.width() - 6) / 24)
        painter.translate((self.width() - 24 * scale) / 2, (self.height() - 24 * scale) / 2)
        painter.scale(scale, scale)
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(self.outline)
        if self.flyout:
            # Photoshop's mark for a tool with others behind it: a small
            # filled triangle in the bottom-right corner.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(25.5, 25.5), QPointF(25.5, 21), QPointF(21, 25.5)]))
        painter.end()


class ToolStrip(QFrame):
    """A toolbox with split buttons for groups of related tools."""

    def __init__(self, *, changed):
        super().__init__()
        self.setAccessibleName("Design toolbox")
        self.setObjectName("designToolRail")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 0, 4, 0)
        layout.setSpacing(1)
        self.group = QButtonGroup(self)
        self.slots = {}
        self.menus = {}
        self.show_group = {}
        self.choices = {}
        self._buttons_by_tool = {}
        self._last_choice = {}
        self._index = 0
        self._changed = changed
        self.active_label = styled_label(TOOL_NAMES[0], role="heading")
        for title, tools in TOOL_GROUPS:
            if title in ("Shape tools", "Cut tools", "View tools"):
                layout.addWidget(design_divider())
            primary = DesignIconButton(tools[0], checkable=True, size=30)
            self.group.addButton(primary)
            self.slots[title] = primary
            self._last_choice[primary] = TOOL_NAMES.index(tools[0].name)
            primary.clicked.connect(lambda _checked, button=primary: self.setCurrentIndex(self._last_choice[button]))
            for tool in tools:
                self._buttons_by_tool[tool.name] = primary
            if len(tools) > 1:
                self.add_flyout(title, primary, tools)
            layout.addWidget(primary)
        layout.addStretch(1)
        self.slots["Select"].setChecked(True)

    def add_flyout(self, title, primary, tools):
        """Right-click, the menu key or a held press opens the group, as in Photoshop."""
        menu = QMenu(primary)
        menu.setAccessibleName(title)
        self.menus[title] = menu
        for tool in tools:
            name = f"{tool.name}\t{tool.shortcut}" if tool.shortcut else tool.name
            command = menu.addAction(name)
            command.setCheckable(True)
            command.setChecked(tool is tools[0])
            command.triggered.connect(lambda _checked, name=tool.name: self.setCurrentText(name))
            self.choices[tool.name] = command
        menu.aboutToShow.connect(lambda: self.color_menu_icons(tools, menu.palette().text().color()))
        primary.flyout = True
        primary.setAccessibleDescription(f"{primary.accessibleDescription()} Right-click for more {title.lower()}.")
        self.show_group[title] = show_menu = lambda: menu.popup(primary.mapToGlobal(QPoint(primary.width(), 0)))
        primary.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        primary.customContextMenuRequested.connect(lambda _point: show_menu())
        hold = QTimer(primary)
        hold.setSingleShot(True)
        hold.setInterval(450)
        primary.pressed.connect(hold.start)
        primary.released.connect(hold.stop)

        def show_on_hold():
            primary.setDown(False)
            show_menu()

        hold.timeout.connect(show_on_hold)

    def color_menu_icons(self, tools, color):
        for tool in tools:
            self.choices[tool.name].setIcon(design_icon(tool, color))

    def button_for(self, tool):
        return self._buttons_by_tool[tool]

    def currentData(self):
        return TOOL_NAMES[self.currentIndex()]

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        if 0 <= index < len(TOOLS):
            tool = TOOLS[index]
            primary = self.button_for(tool.name)
            last = self._last_choice[primary]
            if last != index:
                self.choices[TOOLS[last].name].setChecked(False)
                primary.set_tool(tool)
                self._last_choice[primary] = index
            if tool.name in self.choices:
                self.choices[tool.name].setChecked(True)
            self._index = index
            primary.setChecked(True)
            self.active_label.setText(tool.name)
            self._changed()

    def setCurrentText(self, text):
        idx = self.findData(text)
        if idx >= 0:
            self.setCurrentIndex(idx)

    def findData(self, text):
        try:
            return TOOL_NAMES.index(text)
        except ValueError:
            return -1


class DockPanel(QFrame):
    """One inspector page with a title supplied by the panel chooser."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setAccessibleName(title)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setProperty("role", "dockBody")
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setContentsMargins(8, 4, 8, 8)
        self.layout_.setSpacing(4)
        outer.addWidget(self.body)

    def form(self, title=None):
        """A dense form, optionally under a small section caption."""
        if title:
            caption = styled_label(title, role="dockSection")
            self.layout_.addWidget(caption)
        holder = QWidget()
        form = Form(holder)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(3)
        form.setHorizontalSpacing(8)
        self.layout_.addWidget(holder)
        return form

    def row(self, *widgets, stretch=True):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        for widget in widgets:
            row.addWidget(widget)
        if stretch:
            row.addStretch()
        self.layout_.addWidget(holder)
        return holder


class PanelDock(QFrame):
    """A labeled picker and one scrollable inspector page at a time."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("designDock")
        self.panels = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 0)
        layout.setSpacing(4)
        self.picker = QComboBox()
        self.picker.setAccessibleName("Design panel")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(self.picker, 1)
        self.disclosure = QToolButton(checkable=True, checked=True, arrowType=Qt.ArrowType.DownArrow)
        self.disclosure.setAccessibleName("Show properties")
        self.disclosure.setToolTip("Collapse or expand properties")
        self.disclosure.toggled.connect(self.show_properties)
        header.addWidget(self.disclosure)
        layout.addLayout(header)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.picker.currentIndexChanged.connect(self.switch_panel)

    def show_properties(self, expanded):
        self.pages.setVisible(expanded)
        self.disclosure.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.setMaximumHeight(16777215 if expanded else self.layout().minimumSize().height())

    def add(self, key, title, icon):
        panel = DockPanel(title)
        self.panels[key] = panel
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.pages.addWidget(scroll)
        self.picker.addItem(design_icon(icon, self.palette().text().color()), title, key)
        return panel

    def switch_panel(self, index):
        self.pages.setCurrentIndex(index)
        self.changed.emit()

    def open(self, key):
        self.disclosure.setChecked(True)
        self.picker.setCurrentIndex(self.picker.findData(key))

    def shown(self):
        key = self.picker.currentData()
        return [key] if key is not None else []


# Studio's basic colour menu, a plain palette that reads on screen and in print.
BASIC_COLORS = ("#000000", "#595959", "#a6a6a6", "#ffffff", "#d0202e", "#f26b1d", "#f7c600", "#3fa34d",
                "#1f6f3a", "#1f8fd1", "#244b9a", "#6b3fa0", "#d23c8f", "#8b5a2b", "#00a99d", "#e0313f")


class ColorSwatch(QToolButton):
    """A colour well: the current colour (hatched when clear) with Studio's
    basic colours, Clear, Pick from Canvas and More Colors in its menu."""

    picked = Signal(str)
    pick_from_canvas = Signal()

    def __init__(self, label):
        super().__init__()
        self.label = label
        self.color, self.mixed = "", False
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setFixedSize(52, 24)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        grid_holder = QWidget()
        grid = QHBoxLayout(grid_holder)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(3)
        columns = [QVBoxLayout() for _ in range(8)]
        for column in columns:
            column.setSpacing(3)
            grid.addLayout(column)
        self.swatches = {}
        for i, value in enumerate(("", *BASIC_COLORS[:15])):
            swatch = QToolButton()
            swatch.setFixedSize(20, 20)
            swatch.setAccessibleName(value or "Clear")
            swatch.setToolTip(value or "Clear")
            swatch.setStyleSheet(f"background: {value}; border: 1px solid palette(mid);" if value else
                                 "background: qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0.45 white, "
                                 "stop:0.5 #d0202e, stop:0.55 white); border: 1px solid palette(mid);")
            swatch.clicked.connect(lambda _checked=False, value=value: (menu.close(), self.choose(value)))
            self.swatches[value] = swatch
            columns[i % 8].addWidget(swatch)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(grid_holder)
        menu.addAction(holder)
        menu.addSeparator()
        menu.addAction("Pick from Canvas", self.pick_from_canvas.emit)
        menu.addAction("More Colors…", self.more)
        self.setMenu(menu)

    def choose(self, value):
        self.set_color(value)
        self.picked.emit(value)

    def more(self):
        from PySide6.QtWidgets import QColorDialog

        chosen = QColorDialog.getColor(QColor(self.color or "#313945"), self, self.label)
        if chosen.isValid():
            self.choose(chosen.name())

    def set_color(self, value, mixed=False):
        self.color, self.mixed = value or "", mixed
        self.setAccessibleDescription("Mixed" if mixed else value or "Clear")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        well = QRectF(5, 5, self.width() - 18, self.height() - 10)
        painter.setPen(QPen(self.palette().mid().color(), 1))
        if self.mixed:
            painter.setBrush(self.palette().base())
            painter.drawRect(well)
            painter.drawText(well, Qt.AlignmentFlag.AlignCenter, "…")
        elif self.color:
            painter.setBrush(QColor(self.color))
            painter.drawRect(well)
        else:
            painter.setBrush(QColor("white"))
            painter.drawRect(well)
            painter.setPen(QPen(QColor("#d0202e"), 1.4))
            painter.drawLine(well.bottomLeft(), well.topRight())
        painter.end()
