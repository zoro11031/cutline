"""Small shared Qt form helpers and a main-thread result dispatcher."""

import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QBoxLayout, QButtonGroup, QCheckBox,
                               QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLayout,
                               QLabel, QLineEdit, QMenu, QMenuBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget, QStyle,
                               QStyleOptionToolButton, QStylePainter, QTabBar, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from .design_tools import PANEL_GROUPS, WORKSPACE_TOOL_GROUPS as TOOL_GROUPS, WORKSPACE_TOOL_NAMES as TOOL_NAMES, \
    WORKSPACE_TOOLS as TOOLS

_WORKSPACE_STYLE = """
QWidget#workspace { background: @panel; }
QLabel[role="title"] { font-size: 16px; font-weight: 650; }
QLabel[role="heading"] { font-weight: 650; }
QLabel[role="metric"] { padding: 4px 8px; }
QFrame#workspaceBar {
    background: @chrome;
    border: 0;
    border-bottom: 1px solid @hairline;
    border-radius: 0;
}
QFrame#workspaceFooter {
    background: @chrome;
    border: 0;
    border-top: 1px solid @hairline;
    border-radius: 0;
}
QFrame#workspaceTools {
    background: @toolbar;
    border: 0;
    border-bottom: 1px solid @hairline;
    border-radius: 0;
}
QFrame#designCommands {
    background: @toolbar;
    border: 0;
    border-bottom: 1px solid @hairline;
    border-radius: 0;
}
QFrame#designRibbon, QFrame#designToolOptions {
    background: transparent;
    border: 0;
    border-radius: 0;
}
QSplitter#designSidebar {
    background: @panel;
    border: 0;
    border-radius: 0;
}
QFrame#designDock, QFrame#designObjectsPane {
    background: @panel;
    border: 0;
}
QFrame#designObjectsPane {
    border-top: 1px solid @hairline;
}
QFrame#designToolRail {
    background: @toolbar;
    border: 0;
    border-right: 1px solid @hairline;
}
QFrame#designPanelRail {
    background: @toolbar;
    border: 0;
    border-left: 1px solid @hairline;
}
QFrame[role="designDivider"] { background: @hairline; }
QToolButton[role="designRibbon"], QToolButton[role="designRibbonMenu"], QToolButton[role="compactMenu"] {
    background: @input_bg;
    border: 1px solid @hairline;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 28px;
}
QToolButton[role="designRibbonMenu"], QToolButton[role="compactMenu"] {
    padding: 4px 20px 4px 8px;
}
QToolButton[role="designRibbonMenu"]::menu-indicator, QToolButton[role="compactMenu"]::menu-indicator {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    right: 6px;
    width: 8px;
}
QToolButton[role="designRibbon"]:hover, QToolButton[role="designRibbonMenu"]:hover,
QToolButton[role="compactMenu"]:hover {
    border-color: @accent;
    background: @hover_surface;
}
QToolButton[role="designRibbon"]:focus, QToolButton[role="designRibbonMenu"]:focus,
QToolButton[role="compactMenu"]:focus {
    border: 2px solid @accent;
}
QToolButton[role="designRibbon"]:disabled, QToolButton[role="designRibbonMenu"]:disabled,
QToolButton[role="compactMenu"]:disabled {
    background: @disabled_button;
    color: @disabled_text;
}
QPushButton[role="compactTool"] {
    background: @input_bg;
    border: 1px solid @hairline;
    border-radius: 4px;
    min-height: 28px;
    padding: 4px 10px;
}
QPushButton[role="compactTool"]:hover { border-color: @accent; background: @hover_surface; }
QPushButton[role="compactTool"]:focus { border: 2px solid @accent; }
QLabel[role="saveBadge"] {
    padding: 2px 8px;
    border-radius: 4px;
    background: @hover_surface;
    color: palette(window-text);
}
QLabel[role="saveBadge"][dirty="true"] { font-weight: 600; color: @accent_label; }
QFrame#designNotice {
    background: @hover_surface;
    border: 0;
    border-bottom: 1px solid @accent;
    border-radius: 0;
}
QFrame#designNotice[level="warning"] { border-bottom-color: @destructive; }
QScrollArea[role="settings"], QWidget[role="settingsPage"] { background: @panel; }
QTabWidget[role="settings"]::pane { border: 0; border-top: 1px solid @hairline; }
QTabWidget[role="settings"] QTabBar::tab {
    padding: 8px 12px;
    margin: 0 2px;
    border-bottom: 2px solid transparent;
}
QTabWidget[role="settings"] QTabBar::tab:selected {
    background: @chrome;
    border-bottom-color: @accent;
    font-weight: 650;
}
QTabWidget[role="settings"] QTabBar::tab:hover:!selected { background: @hover_surface; }
QGroupBox[role="card"] {
    background: @card;
    border: 1px solid @hairline;
    border-radius: 6px;
    margin-top: 10px;
    font-weight: 650;
}
QGroupBox[role="card"]::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    background: @card;
    border-radius: 3px;
    left: 12px;
    padding: 1px 6px;
}
QGroupBox[role="card"] QLabel { font-weight: normal; }
QGroupBox[role="card"] QAbstractSpinBox, QGroupBox[role="card"] QComboBox,
QGroupBox[role="card"] QLineEdit, QGroupBox[role="card"] QPushButton { min-height: 28px; }
QCheckBox::indicator:unchecked {
    width: 14px;
    height: 14px;
    background: @input_bg;
    border: 1px solid @check_edge;
    border-radius: 3px;
}
QCheckBox::indicator:unchecked:hover { border-color: @accent; }
QPushButton[role="workspaceTab"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 18px;
    min-height: 28px;
    font-weight: 650;
}
QPushButton[role="workspaceTab"]:hover {
    background: @hover_surface;
    border-color: @hairline;
}
QPushButton[role="workspaceTab"]:focus {
    border-color: @accent;
}
QPushButton[role="workspaceTab"][active="true"] {
    background: @accent;
    color: @accent_text;
    border-color: @accent;
}
QPushButton[role="workspaceTool"], QToolButton[role="workspaceTool"] {
    background: @input_bg;
    border: 1px solid @hairline;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 28px;
}
QPushButton[role="workspaceTool"]:hover, QPushButton[role="workspaceTool"]:focus,
QToolButton[role="workspaceTool"]:hover, QToolButton[role="workspaceTool"]:focus {
    border-color: @accent;
    background: @hover_surface;
}
QToolButton[role="workspaceTool"]:disabled { background: @disabled_button; color: @disabled_text; }
QToolButton[role="designTool"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
}
QToolButton[role="designTool"]:hover {
    background: @hover_surface;
    border-color: @hairline;
}
QToolButton[role="designTool"]:checked, QToolButton[role="designTool"]:pressed {
    background: @accent;
    border-color: @accent;
}
QToolButton[role="designTool"]:focus {
    border: 2px solid @accent;
}
QToolButton[role="designTool"]::menu-indicator { image: none; }
QToolButton[role="panelTab"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
}
QToolButton[role="panelTab"]:hover {
    background: @hover_surface;
    border-color: @hairline;
}
QToolButton[role="panelTab"]:checked, QToolButton[role="panelTab"]:pressed {
    background: @accent;
    border-color: @accent;
}
QToolButton[role="panelTab"]:focus {
    border: 2px solid @accent;
}
QToolButton[role="panelHeaderAction"] {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 2px;
}
QToolButton[role="panelHeaderAction"]:hover {
    background: @hover_surface;
    border-color: @hairline;
}
QToolButton[role="panelHeaderAction"]:focus {
    border-color: @accent;
}
QFrame#designDocument { border: 0; }
QTabBar[role="document"]::tab {
    padding: 6px 14px;
    border: 0;
    border-bottom: 2px solid @accent;
    background: @chrome;
}
QLabel[role="panelTitle"] { font-weight: 650; padding: 2px 0; }
QLabel[role="dockSection"] { font-weight: 600; padding-top: 8px; padding-bottom: 2px; color: @muted_text; }
QWidget[role="dockBody"] QAbstractSpinBox, QWidget[role="dockBody"] QComboBox,
QWidget[role="dockBody"] QLineEdit, QWidget[role="dockBody"] QPushButton,
QFrame#designCommands QAbstractSpinBox, QFrame#designCommands QComboBox,
QFrame#designCommands QLineEdit {
    min-height: 28px;
}
QWidget[role="dockBody"] QLineEdit,
QFrame#designCommands QLineEdit {
    background: @input_bg;
    color: palette(text);
    border: 1px solid @hairline;
    border-radius: 4px;
    padding: 0 6px;
}
QAbstractSpinBox QLineEdit,
QWidget[role="dockBody"] QAbstractSpinBox QLineEdit,
QFrame#designCommands QAbstractSpinBox QLineEdit {
    background: transparent;
    color: palette(text);
    border: 0;
    padding: 0;
    min-height: 0;
}
QWidget[role="dockBody"] QLineEdit:focus,
QFrame#designCommands QLineEdit:focus {
    border-color: @accent;
}
QWidget[role="dockBody"] QPushButton {
    background: @input_bg;
    border: 1px solid @hairline;
    border-radius: 4px;
    padding: 4px 10px;
}
QWidget[role="dockBody"] QPushButton:hover {
    background: @hover_surface;
    border-color: @accent;
}
QWidget[role="dockBody"] QPushButton:checked {
    background: @accent;
    color: @accent_text;
    border-color: @accent;
}
QListWidget[role="designObjects"] {
    background: @panel;
    border: 0;
    border-radius: 0;
    outline: 0;
}
QListWidget[role="designObjects"]::item {
    border-radius: 4px;
    padding: 2px 4px;
}
QListWidget[role="designObjects"]::item:selected {
    background: @design_selection;
    color: palette(text);
}
QListWidget[role="designObjects"]::item:hover:!selected {
    background: @hover_surface;
}
QPushButton[role="holder"]:checked {
    background: @accent;
    color: @accent_text;
    border-radius: 4px;
}
QPushButton:disabled { background: @disabled_button; color: @disabled_text; }
QComboBox:disabled { background: @disabled_button; color: @disabled_text; }
QPushButton[role="primary"] {
    background: @accent;
    color: @accent_text;
    font-weight: 650;
    border: 1px solid @accent;
    border-radius: 5px;
    padding: 6px 16px;
}
QPushButton[role="primary"]:hover, QPushButton[role="primary"]:focus {
    background: @accent_hover;
    border-color: @accent_text;
}
QPushButton[role="primary"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="primary"]:disabled {
    background: @disabled_button;
    color: @disabled_text;
    border-color: @hairline;
}
QPushButton[role="secondary"] {
    background: @input_bg;
    color: palette(button-text);
    border: 1px solid @hairline;
    border-radius: 5px;
    padding: 6px 14px;
}
QPushButton[role="secondary"]:hover, QPushButton[role="secondary"]:focus {
    background: @hover_surface;
    border-color: @accent;
}
QPushButton[role="secondary"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="secondary"]:disabled {
    background: @disabled_button;
    color: @disabled_text;
    border-color: @hairline;
}
QPushButton[role="destructive"] {
    background: @destructive;
    color: @destructive_text;
    font-weight: 650;
    border: 1px solid @destructive_edge;
    border-radius: 5px;
    padding: 6px 14px;
}
QPushButton[role="destructive"]:hover, QPushButton[role="destructive"]:focus {
    background: @destructive_edge;
    border-color: @destructive_text;
}
QPushButton[role="destructive"]:pressed { padding-top: 7px; padding-bottom: 5px; }
QPushButton[role="destructive"]:disabled {
    background: @disabled_button;
    color: @disabled_text;
    border-color: @hairline;
}
QLabel[role="warning"] { color: @destructive; font-weight: 600; }
QPlainTextEdit[role="diagnostics"] { font-family: monospace; }
QToolButton[role="disclosure"] {
    border: 1px solid @hairline;
    border-radius: 4px;
    padding: 5px 10px;
    min-height: 28px;
    background: @input_bg;
    font-weight: 600;
}
QToolButton[role="disclosure"]:hover, QToolButton[role="disclosure"]:focus {
    border-color: @accent;
    background: @hover_surface;
}
QSlider[role="cutSlider"]::groove:horizontal {
    height: 4px;
    border-radius: 2px;
    background: @hairline;
}
QSlider[role="cutSlider"]::sub-page:horizontal { border-radius: 2px; background: @accent; }
QSlider[role="cutSlider"]::handle:horizontal {
    width: 12px;
    border: 1px solid @accent;
    border-radius: 7px;
    margin: -5px 0;
    background: @accent;
}
QSlider[role="cutSlider"]::handle:horizontal:focus,
QSlider[role="cutSlider"]::handle:horizontal:hover { border: 1px solid palette(window-text); }
QSlider[role="cutSlider"]::handle:horizontal:disabled,
QSlider[role="cutSlider"]::sub-page:horizontal:disabled { background: palette(mid); }
QMenuBar { padding: 2px 4px; }
QMenuBar::item { padding: 6px 10px; }
QMenuBar#workspaceMenu { background: transparent; padding: 0; }
QMenuBar#workspaceMenu::item {
    background: transparent;
    color: palette(window-text);
    border-radius: 4px;
    padding: 6px 10px;
}
QMenuBar#workspaceMenu::item:selected,
QMenuBar#workspaceMenu::item:pressed { background: @hover_surface; }
QSplitter::handle { background: @hairline; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter::handle:hover { background: @accent; }
"""


def _mix(first, second, ratio):
    return QColor.fromRgbF(*(a * (1 - ratio) + b * ratio
                             for a, b in zip(first.getRgbF()[:3], second.getRgbF()[:3])))


def accent_color(palette):
    """Restrained deeper slate-blue / blue-violet accent adapted to light or dark themes."""
    window = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
    return QColor("#3d5599") if window.lightnessF() > 0.5 else QColor("#4c64ad")


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
    window = palette.color(active, role.Window)
    base = palette.color(active, role.Base)
    text = palette.color(active, role.WindowText)
    button = palette.color(active, role.Button)
    dark = window.lightnessF() <= 0.5

    label = _legible(palette.color(active, role.ButtonText), button)
    subdued = _mix(button, label, 0.72)
    disabled = subdued if _contrast(subdued, button) >= 4.5 else label

    accent = accent_color(palette)
    accent_hover = accent.lighter(114) if dark else accent.darker(110)
    accent_text = _legible(QColor("white"), accent)
    accent_label = _mix(accent, QColor("white"), 0.35) if dark else accent

    chrome = _mix(window, QColor("#000000"), 0.10) if dark else _mix(window, QColor("#ffffff"), 0.55)
    toolbar = _mix(window, QColor("#ffffff"), 0.03) if dark else _mix(window, QColor("#ffffff"), 0.25)
    panel = window
    input_bg = _mix(window, QColor("#000000"), 0.20) if dark else base
    hover_surface = _mix(window, text, 0.10 if dark else 0.06)
    hairline = _mix(window, text, 0.16 if dark else 0.18)
    muted_text = _mix(window, text, 0.70)
    selection = _mix(panel, accent, 0.34 if dark else 0.20)

    destructive = QColor("#c0392b") if not dark else QColor("#e05c54")
    destructive_text = _legible(QColor("white"), destructive)
    destructive_edge = destructive.darker(125) if not dark else destructive.lighter(115)

    return (_WORKSPACE_STYLE
            .replace("@accent_hover", accent_hover.name())
            .replace("@accent_text", accent_text.name())
            .replace("@accent_label", accent_label.name())
            .replace("@accent", accent.name())
            .replace("@chrome", chrome.name())
            .replace("@toolbar", toolbar.name())
            .replace("@panel", panel.name())
            .replace("@input_bg", input_bg.name())
            .replace("@hover_surface", hover_surface.name())
            .replace("@hairline", hairline.name())
            .replace("@muted_text", muted_text.name())
            .replace("@design_selection", selection.name())
            .replace("@destructive_text", destructive_text.name())
            .replace("@destructive_edge", destructive_edge.name())
            .replace("@destructive", destructive.name())
            .replace("@card", _card_surface(base, palette.color(active, role.Text)).name())
            .replace("@check_edge", _control_edge(input_bg, palette.color(active, role.Text)).name())
            .replace("@disabled_button", button.name())
            .replace("@disabled_text", disabled.name()))


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


def bar(parent, margins=(12, 8, 12, 8), *, name="workspaceBar"):
    surface = QFrame(parent)
    surface.setObjectName(name)
    layout = QHBoxLayout(surface)
    layout.setContentsMargins(*margins)
    layout.setSpacing(8)
    return surface, layout


def footer_bar_widget(parent, margins=(12, 6, 12, 6)):
    return bar(parent, margins, name="workspaceFooter")


def workspace_layout(parent):
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return layout


def workspace_tools(parent):
    surface, layout = bar(parent, (12, 6, 12, 6), name="workspaceTools")
    return surface, layout


def workspace_header(parent, design, send, *, active, document=None):
    header, top = bar(parent, (8, 6, 12, 6))
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
        divider.setFixedSize(1, 24)
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
    painter.setPen(QPen(color, 1.75, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(icon_outline(tool.strokes))
    painter.end()
    return QIcon(pixmap)


class RibbonButton(QToolButton):
    """Icon+text button whose icon follows light/dark palette changes."""

    def __init__(self, tool, icon_size=18):
        super().__init__()
        self._tool = tool
        self._icon_size = icon_size
        self.setIconSize(QSize(icon_size, icon_size))
        self.refresh_icon()

    def refresh_icon(self):
        self.setIcon(design_icon(self._tool, self.palette().buttonText().color(), size=self._icon_size))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_icon()


def ribbon_button(tool, callback=None, *, label=None, menu=None, tip=""):
    """Icon+text button for the Design command and options bar."""
    widget = RibbonButton(tool, icon_size=18)
    widget.setProperty("role", "designRibbonMenu" if menu is not None else "designRibbon")
    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    widget.setText(label or tool.name)
    widget.setAccessibleName(tool.name)
    widget.setToolTip(tip or tool.tooltip)
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    if menu is not None:
        widget.setMenu(menu)
        widget.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    elif callback is not None:
        widget.clicked.connect(lambda _checked=False: callback())
    return widget


class DesignIconButton(QToolButton):
    def __init__(self, tool, *, checkable=False, action=None, size=36, icon_size=None, role="designTool"):
        super().__init__()
        if action is not None:
            self.setDefaultAction(action)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("role", role)
        self.setFixedSize(size, size)
        self._icon_size = icon_size if icon_size is not None else max(16, min(22, size - 14))
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
        if not self.isEnabled():
            color = _mix(palette.button().color(), palette.buttonText().color(), 0.45)
        elif self.isChecked() or self.isDown():
            color = _legible(QColor("white"), accent_color(palette))
        else:
            color = palette.buttonText().color()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon_px = min(self._icon_size, self.width() - 8, self.height() - 8)
        scale = icon_px / 24
        painter.translate((self.width() - 24 * scale) / 2, (self.height() - 24 * scale) / 2)
        painter.scale(scale, scale)
        painter.setPen(QPen(color, 1.75, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(self.outline)
        if self.flyout:
            # Small filled triangle in the bottom-right corner indicating a tool flyout.
            painter.resetTransform()
            w, h = self.width(), self.height()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(w - 3, h - 3), QPointF(w - 3, h - 8), QPointF(w - 8, h - 3)]))
        painter.end()


def rail_scroll(widget, width):
    """Wrap a vertical icon rail so compact windows can shrink below its full height."""
    area = QScrollArea()
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFixedWidth(width)
    area.setMinimumHeight(0)
    area.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)
    area.setWidget(widget)
    return area


class ToolStrip(QFrame):
    """A left drawing toolbox with grouped tools and flyout sub-menus."""

    def __init__(self, *, changed):
        super().__init__()
        self.setAccessibleName("Design toolbox")
        self.setObjectName("designToolRail")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
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
                divider = design_divider()
                divider.setContentsMargins(4, 2, 4, 2)
                layout.addWidget(divider)
            primary = DesignIconButton(tools[0], checkable=True, size=38, icon_size=22)
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
        """Right-click, the menu key or a held press opens the group."""
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
            self.choices[tool.name].setIcon(design_icon(tool, color, size=18))

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
    """One inspector page whose heading sits in the dock header."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setAccessibleName(title)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setProperty("role", "dockBody")
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setContentsMargins(12, 4, 12, 10)
        self.layout_.setSpacing(8)
        outer.addWidget(self.body)

    def form(self, title=None):
        """A well-spaced form, optionally under a section heading."""
        if title:
            caption = styled_label(title, role="dockSection")
            self.layout_.addWidget(caption)
        holder = QWidget()
        form = Form(holder)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(12)
        self.layout_.addWidget(holder)
        return form

    def row(self, *widgets, stretch=True):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for widget in widgets:
            row.addWidget(widget)
        if stretch:
            row.addStretch()
        self.layout_.addWidget(holder)
        return holder


class OriginGrid(QWidget):
    """Interactive 3x3 visual 9-point transform origin selector."""

    picked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Transform origin")
        self.setToolTip("Choose the 9-point anchor for rotation and transforms")
        self._active = (0.5, 0.5)
        self.buttons = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for row, vertical in enumerate(("Top", "Middle", "Bottom")):
            for col, horizontal in enumerate(("left", "center", "right")):
                key = (col / 2, row / 2)
                cell = QToolButton()
                cell.setCheckable(True)
                cell.setFixedSize(16, 16)
                label = f"{vertical} {horizontal}"
                cell.setAccessibleName(label)
                cell.setToolTip(label)
                cell.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                cell.clicked.connect(lambda _checked=False, point=key: self.select(*point, emit=True))
                grid.addWidget(cell, row, col)
                self.buttons[key] = cell
        self.select(0.5, 0.5, emit=False)

    def select(self, x, y, *, emit=False):
        self._active = (x, y)
        self._apply_styles()
        if emit:
            self.picked.emit(x, y)

    def active_origin(self):
        return self._active

    def _apply_styles(self):
        palette = self.palette()
        accent = accent_color(palette)
        dark = palette.window().color().lightnessF() <= 0.5
        idle = _mix(palette.window().color(), palette.windowText().color(), 0.22 if dark else 0.16)
        hover = _mix(idle, accent, 0.5)
        for point, cell in self.buttons.items():
            checked = point == self._active
            cell.setChecked(checked)
            bg = accent.name() if checked else idle.name()
            cell.setStyleSheet(
                f"QToolButton {{ background: {bg}; border: 0; border-radius: 2px; }}"
                f"QToolButton:hover {{ background: {accent.name() if checked else hover.name()}; }}"
                f"QToolButton:focus {{ border: 1px solid {palette.windowText().color().name()}; }}"
            )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange, QEvent.Type.ApplicationPaletteChange):
            self._apply_styles()


class PanelRail(QFrame):
    """Persistent vertical icon switcher along the far-right edge of Design."""

    panel_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("designPanelRail")
        self.setAccessibleName("Design panels")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.rail_layout = QVBoxLayout(self)
        self.rail_layout.setContentsMargins(4, 8, 4, 8)
        self.rail_layout.setSpacing(4)
        self.buttons = {}
        self._group_for = {key: idx for idx, group in enumerate(PANEL_GROUPS) for key in group}
        self._last_group = None
        self._stretch_added = False

    def add_button(self, key, title, icon):
        if self._stretch_added:
            item = self.rail_layout.takeAt(self.rail_layout.count() - 1)
            del item
            self._stretch_added = False
        group = self._group_for.get(key)
        if self._last_group is not None and group != self._last_group:
            divider = design_divider()
            self.rail_layout.addWidget(divider)
        self._last_group = group
        widget = DesignIconButton(icon, checkable=True, size=36, icon_size=20, role="panelTab")
        widget.setAccessibleName(title)
        widget.setToolTip(f"{title}\n{icon.hint}" if icon.hint else title)
        widget.clicked.connect(lambda _checked=False, name=key: self.panel_clicked.emit(name))
        self.buttons[key] = widget
        self.rail_layout.addWidget(widget)
        self.rail_layout.addStretch(1)
        self._stretch_added = True
        return widget

    def set_active(self, key, *, sidebar_open=True):
        for name, widget in self.buttons.items():
            widget.setChecked(sidebar_open and name == key)


class PanelDock(QFrame):
    """Properties inspector controlled by the persistent far-right PanelRail."""

    changed = Signal()
    sidebar_toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("designDock")
        self.panels = {}
        self._order = []
        self._titles = {}
        self._current_key = None
        self._sidebar_open = True
        self.rail = PanelRail()
        self.rail.panel_clicked.connect(self._rail_clicked)
        self.rail_scroll = rail_scroll(self.rail, 46)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header_frame = QFrame()
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(12, 8, 8, 8)
        header.setSpacing(8)
        self.heading = styled_label("", role="panelTitle")
        self.heading.setAccessibleName("Active panel")
        header.addWidget(self.heading, 1)
        self.disclosure = QToolButton(checkable=True, checked=True, arrowType=Qt.ArrowType.DownArrow)
        self.disclosure.setProperty("role", "panelHeaderAction")
        self.disclosure.setFixedSize(28, 28)
        self.disclosure.setAccessibleName("Show properties")
        self.disclosure.setToolTip("Collapse or expand properties")
        self.disclosure.toggled.connect(self.show_properties)
        header.addWidget(self.disclosure)
        layout.addWidget(header_frame)

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

    def show_properties(self, expanded):
        self.pages.setVisible(expanded)
        self.disclosure.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.setMaximumHeight(16777215 if expanded else self.layout().minimumSize().height())

    def add(self, key, title, icon):
        panel = DockPanel(title)
        self.panels[key] = panel
        self._order.append(key)
        self._titles[key] = title
        scroll_area = QScrollArea()
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(panel)
        self.pages.addWidget(scroll_area)
        self.rail.add_button(key, title, icon)
        if self._current_key is None:
            self._select_panel(key, emit=False)
        return panel

    def _rail_clicked(self, key):
        if self._sidebar_open and key == self._current_key:
            self.set_sidebar_open(False)
            self.sidebar_toggled.emit(False)
            return
        was_closed = not self._sidebar_open
        self._sidebar_open = True
        self.disclosure.setChecked(True)
        self._select_panel(key, emit=True)
        if was_closed:
            self.sidebar_toggled.emit(True)

    def _select_panel(self, key, *, emit=True):
        if key not in self.panels:
            return
        index = self._order.index(key)
        self._current_key = key
        self.pages.setCurrentIndex(index)
        self.heading.setText(self._titles[key])
        self.rail.set_active(key, sidebar_open=self._sidebar_open)
        if emit:
            self.changed.emit()

    def set_sidebar_open(self, open_):
        self._sidebar_open = bool(open_)
        self.rail.set_active(self._current_key, sidebar_open=self._sidebar_open)

    def open(self, key):
        if key not in self.panels:
            return
        was_closed = not self._sidebar_open
        self._sidebar_open = True
        self.disclosure.setChecked(True)
        self._select_panel(key, emit=True)
        if was_closed:
            self.sidebar_toggled.emit(True)

    def current_key(self):
        return self._current_key

    def current_title(self):
        return self._titles.get(self._current_key, "")

    def shown(self):
        return [self._current_key] if (self._current_key is not None and self._sidebar_open) else []


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
