"""Full-window GTK design workspace and shape inspector."""

from dataclasses import dataclass, replace
import locale
import math
from pathlib import Path
import threading

import cairo
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from . import config, design_text, editor_model, geometry, graphtec, importer, path_edit, regmarks, sticker_assets, sticker_print, sticker_trace, template
from .design_tools import (ALIGNMENT_ROWS, CLICK_PATH_TOOLS, COMMAND_ICONS, OBJECT_ICONS, PANEL_ICONS, ROTATE_MODES, SCALE_MODES,
                           GTK_TOOL_GROUPS as TOOL_GROUPS, GTK_TOOL_KEYS as TOOL_KEYS, GTK_TOOL_NAMES as TOOL_NAMES,
                           GTK_TOOLS as TOOLS, TRANSFORM_FIELDS, TRANSFORM_ICONS, TRANSFORM_MODES,
                           RotationHandle, constrain_angle, contextual_hint, rotation_handle, scale_from_corner,
                           segment_measurement)
from .editor_canvas import DesignCanvas, draw_strokes
from .editor_model import (ACTIONS, ALIGNMENTS,
                           History, Registration, Shape,
                           load_project, save_project)
from .gtk_controls import (choose_inkscape_page, disclosure, menu_item, protect_settings_wheel, workspace_header, workspace_tools,
                           zoom_controls)


@dataclass
class Drag:
    mode: str
    x: float
    y: float
    scale: float
    indices: tuple[int, ...] = ()
    points: list | None = None
    vertex: tuple[int, int] | None = None
    node: tuple[path_edit.Nodes, str, int, int | None] | None = None
    start_pan: tuple[float, float] | None = None
    rotation: RotationHandle | None = None
    angle: float = 0
    scale_anchor: tuple[float, float] | None = None


def panel(width):
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                   margin_top=8, margin_bottom=12, margin_start=12, margin_end=12)
    scroll.set_size_request(width, -1)
    scroll.set_child(body)
    return scroll, body


def draw_tool_icon(area, context, width, height, tool):
    color = area.get_style_context().get_color()
    context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
    scale = min(width, height, 24) / 24
    context.translate((width - 24 * scale) / 2, (height - 24 * scale) / 2)
    context.scale(scale, scale)
    context.set_line_width(1.75)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    draw_strokes(context, tool.strokes)


def design_icon_button(tool, *, toggle=False, size=36, icon_size=20):
    button_type = Gtk.ToggleButton if toggle else Gtk.Button
    button = button_type(css_classes=["image-button", "flat"])
    button.set_size_request(size, size)
    set_tool_button_icon(button, tool, icon_size=icon_size)
    return button


def tool_image(tool, size=20):
    icon = Gtk.DrawingArea(content_width=size, content_height=size)
    icon.set_draw_func(draw_tool_icon, tool)
    return icon


def ribbon_button(tool, label_text, callback, *, tip=''):
    """Icon+label button for the GTK Design command ribbon."""
    button = Gtk.Button()
    content = Gtk.Box(spacing=6)
    content.append(tool_image(tool, 18))
    content.append(Gtk.Label(label=label_text))
    button.set_child(content)
    button.set_tooltip_text(tip or tool.tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label_text])
    button.connect('clicked', callback)
    return button


def set_tool_button_icon(button, tool, *, icon_size=20):
    button.set_tooltip_text(tool.tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                           [tool.name, tool.hint])
    if tool.shortcut:
        button.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], [tool.shortcut])
    else:
        button.reset_property(Gtk.AccessibleProperty.KEY_SHORTCUTS)
    button.set_child(tool_image(tool, icon_size))


class GtkToolStrip(Gtk.Box):
    """A left drawing toolbox with grouped tools and flyouts."""

    def __init__(self, *, changed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         margin_start=4, margin_end=4, margin_top=8, margin_bottom=8)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Design toolbox"])
        self.slots = {}
        self.menus = {}
        self.menu_buttons = {}
        self.choices = {}
        self._checks = {}
        self._buttons_by_tool = {}
        self._last_choice = {}
        self._selected = 0
        self._changed = changed
        self.active_label = Gtk.Label(label=TOOL_NAMES[0], xalign=0, css_classes=["heading"])
        for title, tools in TOOL_GROUPS:
            if title in ("Shape tools", "Cut tools", "View tools"):
                self.append(Gtk.Separator(margin_top=2, margin_bottom=2))
            row = Gtk.Box(spacing=0)
            primary = design_icon_button(tools[0], toggle=True, size=38, icon_size=22)
            if self.slots:
                primary.set_group(self.slots["Select"])
            self.slots[title] = primary
            self._last_choice[primary] = TOOL_NAMES.index(tools[0].name)
            primary.connect("clicked", lambda button: self.set_selected(self._last_choice[button]))
            row.append(primary)
            for tool in tools:
                self._buttons_by_tool[tool.name] = primary
            if len(tools) > 1:
                self.add_flyout(title, row, primary, tools)
            self.append(row)
        self.slots["Select"].set_active(True)

    def add_flyout(self, title, row, primary, tools):
        menu = Gtk.Popover(position=Gtk.PositionType.RIGHT)
        menu.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.menus[title] = menu
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu.set_child(choices)
        for tool in tools:
            entry = Gtk.Box(spacing=10)
            entry.append(tool_image(tool))
            entry.append(Gtk.Label(label=tool.name, xalign=0, hexpand=True))
            if tool.shortcut:
                entry.append(Gtk.Label(label=tool.shortcut, css_classes=["dim-label", "caption"]))
            check = Gtk.Label(label="✓", opacity=1 if tool is tools[0] else 0)
            entry.append(check)
            self._checks[tool.name] = check
            command = Gtk.Button(child=entry, css_classes=["flat"], tooltip_text=tool.tooltip)
            command.update_property([Gtk.AccessibleProperty.LABEL], [tool.name])
            command.connect("clicked", lambda _button, name=tool.name: self.choose(menu, name))
            self.choices[tool.name] = command
            choices.append(command)
        arrow = Gtk.MenuButton(icon_name="pan-end-symbolic", direction=Gtk.ArrowType.RIGHT,
                               css_classes=["flat", "scc-tool-flyout"], tooltip_text=title)
        arrow.update_property([Gtk.AccessibleProperty.LABEL], [title])
        arrow.set_popover(menu)
        self.menu_buttons[title] = arrow
        row.append(arrow)
        context = Gtk.GestureClick(button=3)
        context.connect("pressed", lambda *_args: arrow.popup())
        primary.add_controller(context)
        hold = Gtk.GestureLongPress(button=1)

        def show_on_hold(gesture, _x, _y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            arrow.popup()

        hold.connect("pressed", show_on_hold)
        primary.add_controller(hold)

    def choose(self, menu, name):
        menu.popdown()
        self.set_selected(TOOL_NAMES.index(name))

    def button_for(self, tool):
        return self._buttons_by_tool[tool]

    def get_selected(self) -> int:
        return self._selected

    def set_selected(self, index: int):
        if 0 <= index < len(TOOLS):
            tool = TOOLS[index]
            primary = self.button_for(tool.name)
            last = self._last_choice[primary]
            if last != index:
                self._checks[TOOLS[last].name].set_opacity(0)
                self._checks[tool.name].set_opacity(1)
                set_tool_button_icon(primary, tool)
                self._last_choice[primary] = index
            self._selected = index
            primary.set_active(True)
            self.active_label.set_label(tool.name)
            self._changed()


class DesignWorkspace:
    def __init__(self, parent, project, open_send, on_review, *, cutter=None):
        self.view = Adw.ToolbarView()
        self.parent = parent
        self.history = History(project)
        self.saved = project
        self.project_path = None
        self.source_name = None
        self.on_review = on_review
        self.loading = False
        self._restoring_ui = True
        self._ui_state = config.load_design_ui()
        self.unit = self._ui_state['units']
        self._saved_sidebar_width = self._ui_state['sidebar_width']
        self._sidebar_open = not self._ui_state['sidebar_collapsed']
        self.space_pan = False
        self.drag = None
        self.draft = None
        self.marquee = None
        self.drawing = False
        self.custom_page = False
        self.polygon = []
        self.selected_node = None
        self.measurement = None
        self.rotation_centre = None
        self.knife = None
        self.eraser_stroke = []
        self.collapsed_groups = set()
        self.cutter = cutter or graphtec.device_by_name("Silhouette Cameo 4")
        self.last_registration = project.registration or Registration.from_marks(
            regmarks.marks(project.width, project.height))

        header, self.design_workspace, self.send_workspace = workspace_header(
            lambda *_: self.canvas.grab_focus(), open_send, active="Design")
        self.view.add_top_bar(header)
        self.shortcuts = shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        self.view.add_controller(shortcuts)

        command_style = dict(margin_start=0, margin_end=0, margin_bottom=0, css_classes=['scc-design-commands'])
        if hasattr(Adw, 'WrapBox'):
            self.commands = Adw.WrapBox(wrap_policy=Adw.WrapPolicy.NATURAL, **command_style)
        else:
            self.commands = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, **command_style)
        self.view.add_top_bar(self.commands)
        self.tools_bar = toolbar = workspace_tools(margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.commands.append(toolbar)
        doc_tools = Gtk.Box(spacing=4)
        self.icon_button(doc_tools, 'new', self.new_project)
        self.icon_button(doc_tools, 'open', self.open_project)
        self.save_button = self.icon_button(doc_tools, 'save', self.save)
        self.save_button.set_tooltip_text('Save (Ctrl+S)\nSave the design.')
        self.import_button = self.icon_button(doc_tools, 'import', lambda *_: self.import_vector(None))
        self.import_button.set_tooltip_text('Import Artwork… (Ctrl+I)\nImport an SVG, DXF, or Studio3 file.')
        toolbar.append(doc_tools)
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6))
        edit_tools = Gtk.Box(spacing=4)
        self.undo_button = self.icon_button(edit_tools, 'undo', lambda *_: self.change_history(self.history.undo))
        self.undo_button.set_tooltip_text('Undo (Ctrl+Z)\nUndo the last change.')
        self.redo_button = self.icon_button(edit_tools, 'redo', lambda *_: self.change_history(self.history.redo))
        self.redo_button.set_tooltip_text('Redo (Ctrl+Y)\nRedo the last change.')
        self.duplicate_ribbon_button = self.icon_button(edit_tools, 'duplicate', self.duplicate_shape)
        self.duplicate_ribbon_button.set_tooltip_text('Duplicate (Ctrl+D)\nDuplicate the selected objects.')
        self.delete_ribbon_button = self.icon_button(edit_tools, 'delete', self.delete_shape)
        self.delete_ribbon_button.set_tooltip_text('Delete (Delete)\nDelete the selected objects.')
        toolbar.append(edit_tools)
        self.selection_separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6)
        toolbar.append(self.selection_separator)
        self.selection_tools = Gtk.Box(spacing=4, visible=False)
        selection_inline = Gtk.Box(spacing=4)
        self.group_ribbon_button = self.icon_button(selection_inline, 'group', lambda *_: self.group_selected())
        self.group_ribbon_button.set_tooltip_text('Group selected objects (Ctrl+G)')
        self.ungroup_ribbon_button = self.icon_button(selection_inline, 'ungroup',
                                                      lambda *_: self.group_selected(release=True))
        self.ungroup_ribbon_button.set_tooltip_text('Ungroup selected objects (Ctrl+Shift+G)')
        align_box = Gtk.Box(spacing=4)
        align_box.append(tool_image(COMMAND_ICONS['align'], 18))
        self.align_button = Gtk.MenuButton(child=align_box, css_classes=['flat'],
                                           tooltip_text='Align or space selected objects')
        self.align_button.set_size_request(32, 32)
        self.align_button.update_property([Gtk.AccessibleProperty.LABEL], ['Align'])
        align_popover = Gtk.Popover()
        align_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.ribbon_align_buttons = {}
        for icons in ALIGNMENT_ROWS:
            for icon in icons:
                def run_align(_btn, option=icon.name):
                    align_popover.popdown()
                    self.align_objects(option)

                item = ribbon_button(icon, icon.name, run_align, tip=icon.name)
                item.add_css_class('flat')
                align_list.append(item)
                self.ribbon_align_buttons[icon.name] = item
        align_popover.set_child(align_list)
        self.align_button.set_popover(align_popover)
        selection_inline.append(self.align_button)
        compact_menu = Gio.Menu()
        self.selection_menu = Gtk.MenuButton(label='Selection', menu_model=compact_menu,
                                             halign=Gtk.Align.START, hexpand=False)
        self.compact_selection = compact_menu
        self.selection_squeezer = Adw.Squeezer(transition_type=Adw.SqueezerTransitionType.NONE,
                                               halign=Gtk.Align.START, hexpand=False)
        self.selection_squeezer.add(selection_inline)
        self.selection_squeezer.add(self.selection_menu)
        self.selection_tools.append(self.selection_squeezer)
        self.selection_squeezer.connect('notify::visible-child', self.selection_presentation)
        self.trace_button = self.icon_button(self.selection_tools, 'trace', lambda *_: self.trace_sticker())
        self.edit_text_ribbon_button = ribbon_button(COMMAND_ICONS['text'], 'Edit Text…',
                                                     lambda *_: self.text_dialog(), tip='Edit the selected text')
        self.selection_tools.append(self.edit_text_ribbon_button)
        toolbar.append(self.selection_tools)
        document = Gtk.Box(spacing=6)
        self.document_title = Gtk.Label(width_chars=1, max_width_chars=28,
                                        ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=['heading'])
        document.append(self.document_title)
        self.save_state = Gtk.Label(css_classes=['caption', 'dim-label'], visible=False)
        document.append(self.save_state)
        header.set_title_widget(document)

        fit_popover = Gtk.Popover()
        fit_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, cb in (('Fit to Page', lambda: self.canvas.fit_page()),
                          ('Fit to Mat', lambda: self.canvas.fit_mat()),
                          ('Fit to Selection', lambda: self.canvas.fit_selection()),
                          ('Fit All', lambda: self.fit(None))):
            item = Gtk.Button(label=label, css_classes=['flat'])
            item.connect('clicked', lambda _b, fn=cb: (fit_popover.popdown(), fn()))
            fit_list.append(item)
        fit_popover.set_child(fit_list)
        fit_button = Gtk.MenuButton(child=tool_image(COMMAND_ICONS['fit'], 18), css_classes=['flat'],
                                    popover=fit_popover, tooltip_text='Fit view (Page, Mat, Selection)')
        fit_button.set_size_request(30, 30)
        fit_button.update_property([Gtk.AccessibleProperty.LABEL], ['Fit to view'])
        zoom, self.zoom_readout = zoom_controls(lambda factor: self.canvas.zoom_by(factor),
                                                lambda: self.fit(None), fit_button=fit_button)
        self.tools = TOOL_NAMES
        self.tool = GtkToolStrip(changed=self.tool_changed)
        self.tool.add_css_class('scc-design-rail')
        self.place_image_button = design_icon_button(COMMAND_ICONS['image'], size=38, icon_size=22)
        self.place_image_button.set_halign(Gtk.Align.START)
        self.place_image_button.connect('clicked', lambda *_: self.place_sticker())
        self.tool.insert_child_after(self.place_image_button, self.tool.slots['Type'].get_parent())
        self.clipboard = ()
        self.options_bar = self.tool_options_bar()
        self.commands.append(self.options_bar)
        self.exporting_stickers = False

        self.canvas = DesignCanvas(self)
        self.canvas.set_cursor_from_name('default')
        properties = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.inspector = Gtk.Stack(vexpand=True)
        self._panels = (
            ('page', 'Page Setup', self.page_panel),
            ('transform', 'Transform', self.transform_panel),
            ('replicate', 'Replicate', self.replicate_panel),
            ('line', 'Line and Fill', self.style_panel),
        )
        for key, label, build in self._panels:
            scroll, content = panel(260)
            build(content)
            self.inspector.add_named(scroll, label)
        self.option_snap.set_active(self.snap.get_active())
        self.option_snap.connect('toggled', lambda widget: self.snap.set_active(widget.get_active()))
        self.snap.connect('toggled', lambda widget: self.option_snap.set_active(widget.get_active()))

        # Persistent far-right vertical icon panel switcher (replaces dropdown)
        self.panel_rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                  margin_start=4, margin_end=4, margin_top=8, margin_bottom=8,
                                  css_classes=['scc-panel-rail'])
        self.panel_rail.update_property([Gtk.AccessibleProperty.LABEL], ['Design panels'])
        self.panel_buttons = {}
        self._active_panel_index = 1 if project.shapes else 0
        for idx, (key, label, _build) in enumerate(self._panels):
            if idx == 1 or idx == 3:
                self.panel_rail.append(Gtk.Separator(margin_top=2, margin_bottom=2))
            icon = PANEL_ICONS[key]
            btn = design_icon_button(icon, toggle=True, size=36, icon_size=20)
            btn.set_tooltip_text(f'{label}\n{icon.hint}' if icon.hint else label)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [label])
            btn.connect('clicked', lambda _b, index=idx: self._on_panel_rail_clicked(index))
            self.panel_buttons[key] = btn
            self.panel_rail.append(btn)

        property_header = Gtk.Box(spacing=8, margin_start=12, margin_end=8, margin_top=8, margin_bottom=4)
        self.panel_heading = Gtk.Label(label='Transform' if project.shapes else 'Page Setup',
                                       xalign=0, hexpand=True, css_classes=['heading'])
        property_header.append(self.panel_heading)
        self.property_disclosure = collapse = Gtk.ToggleButton(
            icon_name='pan-down-symbolic', active=True, css_classes=['flat'], tooltip_text='Show properties')
        collapse.update_property([Gtk.AccessibleProperty.LABEL], ['Show properties'])
        collapse.connect('toggled', lambda button: self.toggle_inspector_section(
            button, self.inspector, property_header, properties=True))
        property_header.append(collapse)
        properties.append(property_header)
        properties.append(Gtk.Separator())
        properties.append(self.inspector)
        self._user_split = self._ui_state['sidebar_split_user']
        default_pos = round(600 * self._ui_state['properties_ratio']) if self._user_split else 340
        self.property_position = self.object_position = default_pos
        self.dock = dock = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, position=self.property_position,
                         width_request=self._saved_sidebar_width,
                         css_classes=['scc-design-sidebar'],
                         resize_start_child=False, resize_end_child=True,
                         shrink_start_child=False, shrink_end_child=False)
        dock.set_start_child(properties)
        dock.set_end_child(self.objects_panel())
        dock.connect('notify::position', self._on_dock_split)
        self.workspace_panes = workspace_panes = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL, wide_handle=False,
            resize_start_child=True, resize_end_child=False,
            shrink_start_child=False, shrink_end_child=False, hexpand=True)
        canvas_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.notice_bar = Gtk.Frame(visible=False, css_classes=['scc-notice'])
        notice_row = Gtk.Box(spacing=8, margin_start=10, margin_end=6, margin_top=5, margin_bottom=5)
        self.notice_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        dismiss = self.button(notice_row, 'Dismiss', lambda *_: self.clear_notice())
        dismiss.update_property([Gtk.AccessibleProperty.LABEL], ['Dismiss notice'])
        notice_row.append(self.notice_label)
        notice_row.reorder_child_after(self.notice_label, None)
        self.notice_bar.set_child(notice_row)
        canvas_box.append(self.notice_bar)
        self.canvas.set_vexpand(True)
        canvas_box.append(self.canvas)
        workspace_panes.set_start_child(canvas_box)
        workspace_panes.set_end_child(dock)
        workspace = Gtk.Box()
        workspace.append(self.tool)
        self.rail_divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        workspace.append(self.rail_divider)
        workspace.append(workspace_panes)
        self.panel_rail_divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        workspace.append(self.panel_rail_divider)
        workspace.append(self.panel_rail)
        self.view.set_content(workspace)

        footer = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, margin_bottom=8)
        self.status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        footer.append(self.status)
        footer.append(zoom)
        self.trace_next = self.button(footer, 'Trace and Bleed…', self.trace_next_sticker)
        self.review = self.button(footer, 'Export Print & Cut…', lambda *_: self.export_stickers())
        self.trace_next.add_css_class('suggested-action')
        self.review.add_css_class('suggested-action')
        self.view.add_bottom_bar(footer)
        self.menu_bar = self.build_design_menus(shortcuts)
        header.pack_start(self.menu_bar)
        protect_settings_wheel(self.view)
        self.view.add_css_class('scc-workspace')
        self.view.add_css_class('scc-design')
        self.refresh_objects((0,) if project.shapes else ())
        saved_key = self._ui_state['active_panel']
        saved_idx = next((i for i, (k, _, _) in enumerate(self._panels) if k == saved_key), None)
        self.open_panel(saved_idx if (project.shapes and saved_idx is not None) else (1 if project.shapes else 0))
        if not self._sidebar_open:
            self.set_sidebar_visible(False, persist=False)
        self.tool_changed()
        self._restoring_ui = False

    def _persist_ui(self, **updates):
        if self._restoring_ui:
            return
        try:
            config.save_design_ui(updates)
        except OSError:
            pass

    def _on_dock_split(self, *_args):
        if self._restoring_ui:
            return
        height = self.dock.get_height()
        if height > 100 and self.dock.get_visible():
            self._user_split = True
            self._persist_ui(properties_ratio=self.dock.get_position() / height, sidebar_split_user=True)

    def _sync_panel_rail(self):
        for idx, (key, _label, _build) in enumerate(self._panels):
            btn = self.panel_buttons[key]
            active = self._sidebar_open and idx == self._active_panel_index
            if btn.get_active() != active:
                prev, self._restoring_ui = self._restoring_ui, True
                btn.set_active(active)
                self._restoring_ui = prev

    def _on_panel_rail_clicked(self, index):
        if self._restoring_ui:
            return
        if self._sidebar_open and index == self._active_panel_index:
            self.set_sidebar_visible(False)
            return
        self.open_panel(index)

    def set_sidebar_visible(self, visible, *, persist=True):
        self._sidebar_open = bool(visible)
        self.dock.set_visible(self._sidebar_open)
        self._sync_panel_rail()
        if persist:
            self._persist_ui(sidebar_collapsed=not self._sidebar_open)

    def active_panel_index(self):
        return self._active_panel_index

    def active_panel_key(self):
        return self._panels[self._active_panel_index][0]

    def open_panel(self, index):
        if isinstance(index, str):
            index = next((i for i, (k, _, _) in enumerate(self._panels) if k == index), 0)
        index = max(0, min(len(self._panels) - 1, index))
        self._active_panel_index = index
        key, label, _ = self._panels[index]
        self._sidebar_open = True
        self.dock.set_visible(True)
        self.property_disclosure.set_active(True)
        self.inspector.set_visible_child_name(label)
        self.panel_heading.set_text(label)
        self._sync_panel_rail()
        self._persist_ui(active_panel=key, sidebar_collapsed=False)

    def set_unit(self, unit, *, persist=True):
        if unit not in ('mm', 'in'):
            return
        self.unit = unit
        if hasattr(self, 'unit_button'):
            self.unit_button.set_label(unit)
        self.refresh_objects(self.selected_indices())
        if persist:
            self._persist_ui(units=unit)

    def toggle_page_focus(self):
        hide_mat = self.show_mat.get_active()
        self.show_mat.set_active(not hide_mat)
        if hide_mat:
            self.canvas.fit_page()
        else:
            self.fit(None)

    def _to_unit(self, mm):
        return mm / 25.4 if self.unit == 'in' else mm

    def _from_unit(self, value):
        return value * 25.4 if self.unit == 'in' else value

    def toggle_inspector_section(self, button, contents, header, *, properties):
        expanded = button.get_active()
        button.set_icon_name('pan-down-symbolic' if expanded else 'pan-end-symbolic')
        position = 'property_position' if properties else 'object_position'
        if not expanded:
            setattr(self, position, self.dock.get_position())
        contents.set_visible(expanded)
        if expanded:
            self.dock.set_position(getattr(self, position))
        elif properties:
            self.dock.set_position(header.get_height() + 12)
        else:
            self.dock.set_position(self.dock.get_height() - header.get_height() - 12)

    def tool_options_bar(self):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=['scc-design-options'])
        frame.update_property([Gtk.AccessibleProperty.LABEL], ['Tool options'])
        row = Gtk.Box(spacing=6, margin_start=8, margin_end=12, margin_top=4, margin_bottom=4)
        frame.append(row)
        self.context_transform = Gtk.Box(spacing=6)
        self.context_fields = {}
        for field, prefix, name in TRANSFORM_FIELDS:
            if field in ('width', 'rotation'):
                self.context_transform.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            entry = Gtk.Entry(width_chars=7, max_width_chars=9, input_purpose=Gtk.InputPurpose.NUMBER)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [name])
            entry.set_tooltip_text(name)
            entry.connect('activate', self.apply_context_field, field)
            label = Gtk.Label(label='↻' if field == 'rotation' else prefix, css_classes=['dim-label'])
            self.context_transform.append(label)
            self.context_transform.append(entry)
            self.context_fields[field] = entry, label
        row.append(self.context_transform)
        self.context_type = Gtk.Box(spacing=6)
        self.type_families = ()
        self.type_font = Gtk.DropDown()
        self.type_font.set_enable_search(True)
        self.type_font.update_property([Gtk.AccessibleProperty.LABEL], ['Font'])
        self.type_size = Gtk.SpinButton.new_with_range(1, 100, 0.5)
        self.type_size.set_value(10)
        self.type_size.set_width_chars(4)
        self.type_size.update_property([Gtk.AccessibleProperty.LABEL], ['Text size (mm)'])
        self.type_align = Gtk.DropDown.new_from_strings(['Left', 'Center', 'Right'])
        self.type_align.update_property([Gtk.AccessibleProperty.LABEL], ['Text alignment'])
        self.type_font.connect('notify::selected', self.apply_context_text)
        self.type_size.connect('value-changed', self.apply_context_text)
        self.type_align.connect('notify::selected', self.apply_context_text)
        self.context_type.append(self.type_font)
        self.context_type.append(self.type_size)
        self.context_type.append(self.type_align)
        row.append(self.context_type)

        self.radius_option = Gtk.Box()
        self.shape_radius = self.spin(self.radius_option, 'Radius (mm)', 0, 100, 3, step=0.5)
        row.append(self.radius_option)
        self.sides_option = Gtk.Box(visible=False)
        self.sides = self.spin(self.sides_option, 'Sides', 3, 64, 5, step=1, digits=0)
        self.sides.set_width_chars(3)
        self.sides.update_property([Gtk.AccessibleProperty.LABEL], ['Sides'])
        row.append(self.sides_option)
        self.close_polygon = self.button(row, 'Close Shape', lambda *_: self.finish_polygon(closed=True))
        self.close_polygon.set_visible(False)
        self.close_polygon.set_tooltip_text('Close the outline. Double-click or Enter stops with it open.')
        self.eraser_options = Gtk.Box(spacing=6, visible=False)
        self.eraser_size = self.spin(self.eraser_options, 'Size (mm)', 0.2, 50, 3.0, step=0.5, digits=1)
        self.eraser_size.set_width_chars(4)
        row.append(self.eraser_options)
        self.measure_options = Gtk.Box(spacing=6, visible=False)
        self.measure_label = Gtk.Label(label='Drag to measure', xalign=0)
        self.measure_label.update_property([Gtk.AccessibleProperty.LABEL], ['Measurement'])
        self.measure_options.append(self.measure_label)
        self.scale_to_button = self.button(self.measure_options, 'Scale to Length…', self.scale_to_length)
        self.scale_to_button.set_tooltip_text('Scale the selected objects to match a known length.')
        row.append(self.measure_options)
        self.point_menu_items = Gio.Menu()
        self.point_menu = Gtk.MenuButton(label='Point Actions', menu_model=self.point_menu_items,
                                         visible=False)
        row.append(self.point_menu)
        row.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.option_snap = design_icon_button(COMMAND_ICONS['snap'], toggle=True, size=30, icon_size=16)
        row.append(self.option_snap)
        self.unit_button = Gtk.Button(label=self.unit, tooltip_text='Switch display units between mm and inches')
        self.unit_button.update_property([Gtk.AccessibleProperty.LABEL], ['Measurement units'])
        self.unit_button.connect('clicked', lambda *_: self.set_unit('in' if self.unit == 'mm' else 'mm'))
        row.append(self.unit_button)
        self.tool_hint = Gtk.Label(label=TOOLS[0].hint, xalign=1, hexpand=True,
                                   css_classes=['dim-label', 'caption'], ellipsize=Pango.EllipsizeMode.END)
        self.tool_hint.set_tooltip_text(TOOLS[0].hint)
        row.append(self.tool_hint)
        return frame

    def refresh_measurement(self):
        if self.measurement is None:
            self.measure_label.set_text('Drag to measure')
            self.scale_to_button.set_sensitive(False)
            return
        length, angle = segment_measurement(*self.measurement)
        self.measure_label.set_text(f'{length:.2f} mm at {round(angle, 1) + 0.0:.1f}°')
        self.scale_to_button.set_sensitive(length > 0.05 and bool(self.selected_indices()))

    def scale_to_length(self, _button):
        if not self.scale_to_button.get_sensitive():
            return
        start, end = self.measurement
        measured, _ = segment_measurement(start, end)
        alert = Adw.AlertDialog(heading='Scale to Length', body='Enter the real length of the measured distance.')
        field = Gtk.SpinButton.new_with_range(0.1, 1000, 0.1)
        field.set_value(measured)
        field.set_digits(2)
        field.update_property([Gtk.AccessibleProperty.LABEL], ['Length (mm)'])
        row = Gtk.Box(spacing=6)
        row.append(field)
        row.append(Gtk.Label(label='mm'))
        alert.set_extra_child(row)
        alert.add_response('cancel', 'Cancel')
        alert.add_response('scale', 'Scale')
        alert.set_close_response('cancel')
        alert.set_default_response('scale')

        def apply(_alert, response):
            if response != 'scale':
                return
            selection = self.selected_indices()
            if not selection:
                return
            field.update()
            length = field.get_value()
            factor = length / measured
            try:
                shapes = editor_model.scale_selection(self.project.shapes, selection, start, factor)
            except ValueError as error:
                self.show_message(str(error), level='warning')
                return
            self.measurement = (start, (start[0] + (end[0] - start[0]) * factor,
                                        start[1] + (end[1] - start[1]) * factor))
            self.commit(shapes, selection)
            self.show_message(f'Scaled selection to {length:g} mm.')

        alert.connect('response', apply)
        alert.present(self.parent)

    def apply_context_field(self, entry, field):
        try:
            value = locale.atof(entry.get_text())
            if not math.isfinite(value):
                raise ValueError
        except ValueError:
            self.show_message('Enter a number.', level='warning')
        else:
            self.fields[field].set_value(value)
        self.refresh_context_fields()

    def refresh_context_fields(self):
        has_selection = bool(self.selected_indices())
        self.context_transform.set_visible(has_selection and
                                           self.active_tool in ('Select', *TRANSFORM_MODES))
        for key, (entry, label) in self.context_fields.items():
            source = self.fields[key]
            entry.set_visible(source.get_sensitive())
            label.set_visible(source.get_sensitive())
            entry.set_text(locale.format_string('%.3f', source.get_value()).rstrip('0').rstrip(
                locale.localeconv()['decimal_point']))

    def context_text_spec(self):
        selection = self.selected_indices()
        if len(selection) == 1:
            shape = self.project.shapes[selection[0]]
            if shape.text:
                return selection[0], shape.text
        return None, None

    def apply_context_text(self, *_args):
        if self.loading or not self.type_families:
            return
        index, spec = self.context_text_spec()
        if spec is None:
            return
        family = self.type_font.get_selected()
        if family >= len(self.type_families):
            return
        spec = replace(spec, family=self.type_families[family], size_mm=self.type_size.get_value(),
                       align=('left', 'center', 'right')[self.type_align.get_selected()])
        try:
            shapes, selection = design_text.edit(self.project, spec, index)
        except ValueError as error:
            self.show_message(str(error), level='warning')
        else:
            self.commit(shapes, selection)

    def refresh_context_type(self):
        typing = self.active_tool == 'Type'
        self.context_type.set_visible(typing)
        if not typing:
            return
        _, spec = self.context_text_spec()
        previous, self.loading = self.loading, True
        try:
            if not self.type_families:
                families, family, _ = design_text.font_choices(spec)
                self.type_families = families
                self.type_font.set_model(Gtk.StringList.new(families))
                self.type_font.set_selected(families.index(family))
            if spec:
                if spec.family in self.type_families:
                    self.type_font.set_selected(self.type_families.index(spec.family))
                self.type_size.set_value(spec.size_mm)
                self.type_align.set_selected(('left', 'center', 'right').index(spec.align))
        except ValueError as error:
            self.show_message(str(error), level='warning')
        finally:
            self.loading = previous

    @staticmethod
    def icon_button(parent, icon, callback):
        button = design_icon_button(COMMAND_ICONS[icon] if isinstance(icon, str) else icon)
        button.connect('clicked', callback)
        parent.append(button)
        return button

    def objects_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        margin_start=8, margin_end=8, margin_top=6, margin_bottom=8)
        header = Gtk.Box(spacing=4)
        collapse = Gtk.ToggleButton(icon_name='pan-down-symbolic', active=True, css_classes=['flat'],
                                    tooltip_text='Show objects')
        collapse.update_property([Gtk.AccessibleProperty.LABEL], ['Show objects'])
        header.append(collapse)
        header.append(Gtk.Label(label='Objects', xalign=0, hexpand=True, css_classes=['heading']))
        self.add_button = design_icon_button(COMMAND_ICONS['plus'], size=28, icon_size=16)
        self.add_button.set_tooltip_text(editor_model.ADD_SHAPE_TIP)
        self.add_button.update_property([Gtk.AccessibleProperty.LABEL], ['Add Shape'])
        self.add_button.connect('clicked', self.add_shape)
        header.append(self.add_button)
        options = Gtk.MenuButton(icon_name='view-more-symbolic', tooltip_text='Object options',
                                 css_classes=['flat'])
        options.update_property([Gtk.AccessibleProperty.LABEL], ['Object options'])
        menu = Gtk.Popover()
        more_objects = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for field, label, callback in (
                ('rename_button', 'Rename…', lambda: self.rename_object()),
                ('lock_button', 'Lock', lambda: self.toggle_lock()),
                ('toggle_group_button', 'Collapse Group', lambda: self.toggle_selected_group()),
                ('duplicate', 'Duplicate', lambda: self.duplicate_shape(None)),
                ('remove', 'Delete', lambda: self.delete_shape(None)),
                ('group_button', 'Group', lambda: self.group_selected()),
                ('ungroup_button', 'Ungroup', lambda: self.group_selected(release=True)),
                ('forward', 'Bring Forward', lambda: self.reorder(1)),
                ('backward', 'Send Backward', lambda: self.reorder(-1))):
            def activate(_button, command=callback):
                menu.popdown()
                command()

            item = self.button(more_objects, label, activate)
            item.add_css_class('flat')
            setattr(self, field, item)
        menu.set_child(more_objects)
        options.set_popover(menu)
        header.append(options)
        panel.append(header)
        self.objects = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE, css_classes=['scc-objects-list'])
        self.objects.update_property([Gtk.AccessibleProperty.LABEL], ['Object list'])
        placeholder = Gtk.Label(label='No objects yet. Draw with a tool, click +, or Import.',
                                margin_top=14, margin_bottom=14, margin_start=8, margin_end=8,
                                wrap=True, justify=Gtk.Justification.CENTER, css_classes=['dim-label'])
        self.objects.set_placeholder(placeholder)
        menu_items = Gio.Menu()
        for label, action in (('Rename…', 'rename-object'), ('Lock / Unlock', 'toggle-lock'),
                              ('Duplicate', 'duplicate'), ('Delete', 'delete'), ('Group', 'group'),
                              ('Ungroup', 'ungroup'), ('Bring Forward', 'forward'),
                              ('Send Backward', 'backward')):
            menu_items.append(label, f'design.{action}')
        self.object_context_items = menu_items
        click = Gtk.GestureClick(button=3)
        click.connect('pressed', self.open_object_context)
        self.objects.add_controller(click)
        self.objects.connect('selected-rows-changed', self.select_shape)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.objects)
        scroller.set_min_content_height(72)
        panel.append(scroller)
        collapse.connect('toggled', lambda button: self.toggle_inspector_section(
            button, scroller, header, properties=False))
        return panel

    @staticmethod
    def spin(parent, label, low, high, value, step=0.1, digits=2):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True))
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_value(value)
        spin.set_digits(digits)
        spin.set_numeric(True)
        spin.update_property([Gtk.AccessibleProperty.LABEL], [label])
        row.append(spin)
        parent.append(row)
        return spin

    @staticmethod
    def heading(parent, label):
        parent.append(Gtk.Label(label=label, xalign=0, css_classes=['heading'], margin_top=4))

    def page_panel(self, inspector):
        self.page_options = inspector
        self.heading(inspector, 'Media Size')
        self.page_sizes = (*geometry.SHEETS, 'Custom')
        self.page = Gtk.DropDown.new_from_strings(self.page_sizes)
        inspector.append(self.page)
        self.orientation = Gtk.DropDown.new_from_strings(('Portrait', 'Landscape'))
        inspector.append(self.orientation)
        self.page_width = self.spin(inspector, 'Width (mm)', 1, 1000, self.project.width)
        self.page_height = self.spin(inspector, 'Height (mm)', 1, 1000, self.project.height)
        self.page.connect('notify::selected', self.change_page)
        self.orientation.connect('notify::selected', self.change_orientation)
        for spin in (self.page_width, self.page_height):
            spin.connect('value-changed', self.change_custom_page)
        self.heading(inspector, 'Cutting Mat')
        self.mat = Gtk.DropDown.new_from_strings(list(graphtec.MAT_OPTIONS.values()))
        self.mat.connect('notify::selected', self.change_mat)
        inspector.append(self.mat)
        self.heading(inspector, 'Registration Marks')
        self.register = Gtk.CheckButton(label='Enable Registration Marks')
        self.register.connect('toggled', self.change_registration)
        inspector.append(self.register)
        inspector, self.page_advanced = disclosure(inspector)
        self.heading(inspector, 'Mark Geometry')
        self.mark_pattern = Gtk.DropDown.new_from_strings(('Type 1 (3 marks)', '4 marks'))
        self.mark_pattern.connect('notify::selected', self.change_registration)
        inspector.append(self.mark_pattern)
        self.mark_inset = self.spin(inspector, 'Inset (mm)', 2, 40, 10)
        self.mark_length = self.spin(inspector, 'Length (mm)', regmarks.MIN_LENGTH_MM,
                                     regmarks.MAX_LENGTH_MM, 20)
        self.mark_thickness = self.spin(inspector, 'Thickness (mm)', regmarks.MIN_THICKNESS_MM,
                                        regmarks.MAX_THICKNESS_MM, 1, step=0.05)
        for spin in (self.mark_inset, self.mark_length, self.mark_thickness):
            spin.connect('value-changed', self.change_registration)
        self.heading(inspector, 'Canvas')
        state = self._ui_state
        self.show_grid = self.view_toggle(inspector, 'Show Paper Grid', state['grid'], 'grid')
        self.show_mat_grid = self.view_toggle(inspector, 'Show Mat Grid', state['mat_grid'], 'mat_grid')
        self.show_mat = self.view_toggle(inspector, 'Show Cutting Mat', state['mat'], 'mat')
        self.show_guides = self.view_toggle(inspector, 'Show Guides', state['guides'], 'guides')
        self.show_cut_border = self.view_toggle(inspector, 'Show Cut Border', state['cut_border'], 'cut_border')
        self.print_border = self.view_toggle(inspector, 'Show Print Border', state['print_border'], 'print_border')
        self.print_margin = self.spin(inspector, 'Print margin (mm)', 0, 25, 5, step=0.5)
        self.print_margin.set_tooltip_text('Set the margin you use on your printer.')
        self.print_margin.connect('value-changed', lambda *_: self.canvas.queue_draw())
        self.show_rulers = self.view_toggle(inspector, 'Show Rulers', state['rulers'], 'rulers')
        self.snap = self.view_toggle(inspector, 'Snap to Grid', state['snap'], 'snap')
        self.smart_snap = self.view_toggle(inspector, 'Enable Smart Snapping', state['smart_snap'], 'smart_snap')
        self.smart_snap.set_tooltip_text('Line up moving shapes with nearby edges or centers.')
        self.grid_spacing = self.spin(inspector, 'Spacing (mm)', 0.5, 100, 5, step=0.5)
        self.grid_spacing.connect('value-changed', lambda *_: self.canvas.queue_draw())

    def view_toggle(self, parent, label, active=False, state_key=None):
        toggle = Gtk.CheckButton(label=label, active=active)

        def changed(widget):
            if state_key:
                self._persist_ui(**{state_key: bool(widget.get_active())})
            self.canvas.queue_draw()

        toggle.connect('toggled', changed)
        parent.append(toggle)
        return toggle

    def transform_panel(self, inspector):
        inspector.set_spacing(8)
        self.fields = {}

        def _make_spin(field, label, *, rotation=False):
            limit = 360 if rotation else 1000
            spin = Gtk.SpinButton.new_with_range(-limit, limit, 1 if rotation else 0.1)
            spin.set_digits(3)
            spin.set_numeric(True)
            spin.set_width_chars(7)
            spin.set_hexpand(True)
            spin.update_property([Gtk.AccessibleProperty.LABEL], [label])
            spin.set_tooltip_text(label)
            spin.connect('value-changed', self.change_field, field)
            self.fields[field] = spin
            return spin

        def _pair_section(title, specs):
            inspector.append(Gtk.Label(label=title, xalign=0, css_classes=['dim-label', 'caption']))
            row = Gtk.Grid(column_spacing=8, row_spacing=6, css_classes=['scc-transform-grid'])
            for col_idx, (field, short, label) in enumerate(specs):
                cell = Gtk.Box(spacing=6, hexpand=True)
                cell.append(Gtk.Label(label=short, css_classes=['dim-label']))
                cell.append(_make_spin(field, label))
                row.attach(cell, col_idx, 0, 1, 1)
            inspector.append(row)

        _pair_section('Position', (('x', 'X', 'X (mm)'), ('y', 'Y', 'Y (mm)')))
        _pair_section('Dimensions', (('width', 'W', 'Width (mm)'), ('height', 'H', 'Height (mm)')))

        lock_row = Gtk.Box(spacing=8, margin_top=2, margin_bottom=2)
        lock_row.append(Gtk.Label(label='Lock aspect ratio', xalign=0, hexpand=True))
        self.lock_aspect = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.lock_aspect.update_property([Gtk.AccessibleProperty.LABEL], ['Lock aspect ratio'])
        self.lock_aspect.set_tooltip_text('Keep width and height proportional.')
        lock_row.append(self.lock_aspect)
        inspector.append(lock_row)

        rot_ref = Gtk.Box(spacing=12, margin_top=4, margin_bottom=2)
        rot_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        rot_col.append(Gtk.Label(label='Rotation', xalign=0, css_classes=['dim-label', 'caption']))
        rot_col.append(_make_spin('rotation', 'Rotation (degrees)', rotation=True))
        self.panel_transform_actions = row = Gtk.Box(spacing=4, margin_top=2)
        rot_col.append(row)
        self.transform_buttons = {
            action: self.icon_button(row, icon, lambda _button, command=action: self.transform_objects(command))
            for action, icon in TRANSFORM_ICONS.items()
        }
        for widget in self.transform_buttons.values():
            widget.set_size_request(32, 32)
        rot_ref.append(rot_col)

        ref_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ref_col.append(Gtk.Label(label='Reference point', xalign=0, css_classes=['dim-label', 'caption']))
        self.origin_grid = self.build_origin_grid()
        ref_col.append(self.origin_grid)
        rot_ref.append(ref_col)
        inspector.append(rot_ref)

        inspector.append(Gtk.Separator(margin_top=2, margin_bottom=2))

        self.heading(inspector, 'Align and Space')
        self.alignment_buttons = {}
        for icons in ALIGNMENT_ROWS:
            row = Gtk.Box(spacing=4)
            inspector.append(row)
            for icon in icons:
                btn = self.icon_button(row, icon, lambda _button, option=icon.name: self.align_objects(option))
                btn.set_size_request(32, 32)
                self.alignment_buttons[icon.name] = btn

        advanced, _ = disclosure(inspector, 'Advanced transformation')
        self.flips = {}
        for field, label in (('flip_x', 'Flip local X'), ('flip_y', 'Flip local Y'),
                             ('visible', 'Visible')):
            toggle = Gtk.CheckButton(label=label)
            toggle.connect('toggled', self.change_toggle, field)
            advanced.append(toggle)
            self.flips[field] = toggle
        self.fields['radius'] = self.spin(advanced, 'Corner radius (mm)', 0, 1000, 0, digits=3)
        self.fields['radius'].connect('value-changed', self.change_field, 'radius')

    def build_origin_grid(self):
        overlay = Gtk.Overlay()
        overlay.set_size_request(60, 60)
        overlay.update_property([Gtk.AccessibleProperty.LABEL], ['Reference point'])
        frame = Gtk.DrawingArea(content_width=60, content_height=60)

        def _draw_outline(_area, cr, width, height):
            inset = 10.0
            cr.set_source_rgba(0.55, 0.58, 0.64, 0.55)
            cr.set_line_width(1.25)
            cr.rectangle(inset, inset, width - 2 * inset, height - 2 * inset)
            cr.stroke()

        frame.set_draw_func(_draw_outline)
        overlay.set_child(frame)
        grid = Gtk.Grid(column_spacing=2, row_spacing=2, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.origin_cells = {}
        self._active_origin = (0.5, 0.5)
        for row, vertical in enumerate(('Top', 'Middle', 'Bottom')):
            for column, horizontal in enumerate(('left', 'center', 'right')):
                key = (column / 2, row / 2)
                label = f'{vertical} {horizontal}'
                cell = Gtk.Button(tooltip_text=label, css_classes=['flat', 'scc-origin-cell'])
                cell.set_size_request(18, 18)
                cell.update_property([Gtk.AccessibleProperty.LABEL], [label])
                if key == self._active_origin:
                    cell.add_css_class('active')
                cell.connect('clicked', lambda _b, point=key: self.set_rotation_origin(*point))
                grid.attach(cell, column, row, 1, 1)
                self.origin_cells[key] = cell
        overlay.add_overlay(grid)
        return overlay

    def set_rotation_centre(self, on):
        if on:
            selection = self.selected_indices()
            if selection:
                box = editor_model.bounds(self.project.shapes[i] for i in selection)
                self.rotation_centre = (box.x + box.w / 2, box.y + box.h / 2)
            else:
                self.rotation_centre = (self.project.width / 2, self.project.height / 2)
        else:
            self.rotation_centre = None
        self.canvas.queue_draw()

    def set_rotation_origin(self, x, y, popover=None):
        if popover is not None:
            popover.popdown()
        self._active_origin = (x, y)
        if hasattr(self, 'origin_cells'):
            for point, cell in self.origin_cells.items():
                if point == self._active_origin:
                    cell.add_css_class('active')
                else:
                    cell.remove_css_class('active')
        selection = self.selected_indices()
        if selection:
            box = editor_model.bounds(self.project.shapes[i] for i in selection)
            self.rotation_centre = (box.x + box.w * x, box.y + box.h * y)
            self.canvas.queue_draw()

    def build_design_menus(self, shortcuts):
        self.menu_actions = Gio.SimpleActionGroup()
        self.view.insert_action_group('design', self.menu_actions)

        def command(menu, label, name, callback, shortcut=None, field=None, *, canvas=False):
            if field is None:
                action = Gio.SimpleAction.new(name, None)
            else:
                action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(field.get_active()))
                field.connect('toggled', lambda widget: action.set_state(GLib.Variant.new_boolean(widget.get_active())))
            action.connect('activate', lambda *_: callback())
            self.menu_actions.add_action(action)
            full_name = f'design.{name}'
            menu.append_item(menu_item(label, full_name, shortcut))
            if shortcut and not canvas:
                shortcuts.add_shortcut(Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(shortcut), Gtk.NamedAction.new(full_name)))

        files = Gio.Menu()
        for label, name, shortcut, callback in (
                ('New', 'new', '<Control>n', self.new_project),
                ('Open…', 'open', '<Control>o', self.open_project),
                ('Save', 'save', '<Control>s', self.save),
                ('Save As…', 'save-as', '<Control><Shift>s', self.save_as),
                ('Import Artwork…', 'import-artwork', '<Control>i', self.import_vector)):
            command(files, label, name, lambda handler=callback: handler(None), shortcut)
        command(files, 'Place Image…', 'place-sticker', self.place_sticker)
        command(files, 'Export Print & Cut…', 'export-stickers', self.export_stickers)
        edit = Gio.Menu()
        command(edit, 'Undo', 'undo', lambda: self.change_history(self.history.undo))
        command(edit, 'Redo', 'redo', lambda: self.change_history(self.history.redo))
        command(edit, 'Select All', 'select-all', lambda: self.set_selection(
            i for i, shape in enumerate(self.project.shapes) if shape.visible))
        command(edit, 'Cut', 'cut', lambda: self.copy(cut=True), '<Control>x', canvas=True)
        command(edit, 'Copy', 'copy', self.copy, '<Control>c', canvas=True)
        command(edit, 'Paste', 'paste', self.paste, '<Control>v', canvas=True)
        command(edit, 'Paste in Front', 'paste-front', lambda: self.paste(in_front=True),
                '<Control><Shift>v', canvas=True)
        command(edit, 'Duplicate', 'duplicate', lambda: self.duplicate_shape(None))
        command(edit, 'Delete', 'delete', lambda: self.delete_shape(None))

        view = Gio.Menu()
        command(view, 'Show or Hide Panels', 'panels-toggle', self.toggle_panels, 'F9')
        zoom = Gio.Menu()
        view.append_submenu('Zoom', zoom)
        command(zoom, 'Zoom In', 'zoom-in', lambda: self.canvas.zoom_by(1.25), '<Control>equal|<Control>plus')
        command(zoom, 'Zoom Out', 'zoom-out', lambda: self.canvas.zoom_by(0.8), '<Control>minus')
        command(zoom, 'Fit to Page', 'fit-page', self.canvas.fit_page, '<Control>0')
        command(zoom, 'Fit to Mat', 'fit-mat', self.canvas.fit_mat, '<Control><Alt>0')
        command(zoom, 'Fit to Selection', 'fit-selection', self.canvas.fit_selection, '<Control><Shift>0')
        command(zoom, 'Fit All', 'fit', lambda: self.fit(None))
        command(view, 'Page Focus', 'page-focus', self.toggle_page_focus, '<Control><Shift>p')
        rotate = Gio.Menu()
        view.append_submenu('Rotate Page', rotate)
        command(rotate, 'Left', 'rotate-page-left', lambda: self.rotate_page(False))
        command(rotate, 'Right', 'rotate-page-right', lambda: self.rotate_page(True))
        units = Gio.Menu()
        view.append_submenu('Units', units)
        command(units, 'Millimetres (mm)', 'units-mm', lambda: self.set_unit('mm'))
        command(units, 'Inches (in)', 'units-in', lambda: self.set_unit('in'))
        for label, name, field in (
                ('Show Rulers', 'rulers', self.show_rulers),
                ('Show Paper Grid', 'grid', self.show_grid),
                ('Show Guides', 'guides', self.show_guides),
                ('Show Cutting Mat', 'mat', self.show_mat),
                ('Show Mat Grid', 'mat-grid', self.show_mat_grid),
                ('Snap to Grid', 'snap', self.snap),
                ('Enable Smart Snapping', 'smart-snap', self.smart_snap),
                ('Show Print Border', 'print-border', self.print_border),
                ('Show Cut Border', 'cut-border', self.show_cut_border)):
            command(view, label, name, lambda toggle=field: toggle.set_active(not toggle.get_active()), field=field)
        command(view, 'Grid Options…', 'grid-options', lambda: self.show_page_options(self.show_grid))
        command(view, 'Registration Marks…', 'registration', lambda: self.show_page_options(self.mark_pattern))
        command(view, 'Show Design Page Settings…', 'page-options', lambda: self.open_panel(0),
                '<Control>F1')
        command(view, 'Switch to Send', 'send', lambda: self.send_workspace.emit('clicked'), '<Control>F2')

        panels = Gio.Menu()
        for label, name, index in (('Page Setup', 'page-setup', 0), ('Fill Color', 'fill', 3),
                                   ('Line Style', 'line', 3), ('Transform', 'transform', 1), ('Replicate', 'replicate', 2)):
            command(panels, label, name, lambda panel=index: self.open_panel(panel))
        colors = Gio.Menu()
        panels.append_submenu('Select by Color', colors)
        for label, field in (('Same Line Color', 'stroke'), ('Same Fill Color', 'fill')):
            command(colors, label, f'select-{field}', lambda channel=field: self.select_color(channel))
        command(panels, 'Offset…', 'offset-panel', self.offset_selection)

        objects = Gio.Menu()
        for title, first, second in (
                ('Mirror', ('Horizontally', 'horizontal'), ('Vertically', 'vertical')),
                ('Rotate', ('Left', 'left'), ('Right', 'right'))):
            menu = Gio.Menu()
            objects.append_submenu(title, menu)
            for label, operation in (first, second):
                command(menu, label, operation, lambda op=operation: self.transform_objects(op))
        arrange = Gio.Menu()
        objects.append_submenu('Arrange', arrange)
        command(arrange, 'Bring Forward', 'forward', lambda: self.reorder(1))
        command(arrange, 'Send Backward', 'backward', lambda: self.reorder(-1))
        align = Gio.Menu()
        objects.append_submenu('Align', align)
        for index, label in enumerate(ALIGNMENTS):
            command(align, label, f'align-{index}', lambda option=label: self.align_objects(option))
        command(objects, 'Replicate…', 'object-replicate', lambda: self.open_panel(2))
        command(objects, 'Group', 'group', self.group_selected)
        command(objects, 'Ungroup', 'ungroup', lambda: self.group_selected(release=True))
        command(objects, 'Rename…', 'rename-object', self.rename_object)
        command(objects, 'Lock / Unlock', 'toggle-lock', self.toggle_lock)
        holders = Gio.Menu()
        objects.append_submenu('Tool Holder', holders)
        for label, slot in (('Use Current Settings', None), ('Tool Holder 1', 1), ('Tool Holder 2', 2)):
            command(holders, label, f'holder-{slot}', lambda holder=slot: self.change_properties(holder=holder))
        self.menu_actions.lookup_action('holder-2').set_enabled(self.cutter.two_tools)
        for index, label in enumerate(path_edit.POINT_ACTIONS):
            name = f'point-action-{index}'
            self.point_menu_items.append(label, f'design.{name}')
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_args, option=label: self.point_action(option))
            self.menu_actions.add_action(action)
        command(objects, 'Convert to Path', 'convert', self.convert_selection)
        command(objects, 'Simplify', 'simplify', lambda: self.convert_selection(True), '<Control>j')
        modify = Gio.Menu()
        objects.append_submenu('Modify', modify)
        command(modify, 'Offset…', 'offset', self.offset_selection)
        for operation in editor_model.PATH_OPERATIONS:
            command(modify, operation, operation.lower().replace(' ', '-'),
                    lambda name=operation: self.modify_shapes(name))
        command(objects, 'Add or Edit Text…', 'text', self.text_dialog)
        command(objects, 'Trace and Bleed…', 'trace-sticker', self.trace_sticker)
        menus = Gio.Menu()
        for title, menu in (('_File', files), ('_Edit', edit), ('_View', view),
                            ('_Panels', panels), ('_Object', objects)):
            menus.append_submenu(title, menu)
        return Gtk.PopoverMenuBar.new_from_model(menus)

    def show_page_options(self, field):
        self.open_panel(0)
        self.page_advanced.set_expanded(True)

        def reveal():
            placed, bounds = field.compute_bounds(self.page_options)
            if placed:
                scroll = self.inspector.get_child_by_name('Page Setup')
                scroll.get_vadjustment().set_value(max(0, bounds.get_y() - 12))
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def toggle_panels(self):
        show = not self.tool.get_visible()
        for panel in (self.tool, self.rail_divider, self.commands, self.panel_rail, self.panel_rail_divider):
            panel.set_visible(show)
        self.set_sidebar_visible(show, persist=False)
        self.canvas.grab_focus()

    def show_message(self, text, *, level='info'):
        self.status.set_text(text)
        self.notice_label.set_text(text)
        if level == 'warning':
            self.notice_bar.add_css_class('scc-notice-warning')
        else:
            self.notice_bar.remove_css_class('scc-notice-warning')
        self.notice_bar.set_visible(bool(text))

    def clear_notice(self):
        self.notice_bar.set_visible(False)
        self.notice_label.set_text('')
        self.refresh_status()

    def rotate_page(self, clockwise):
        try:
            project = editor_model.rotate_page(self.project, clockwise)
        except ValueError as error:
            self.status.set_text(str(error))
            return
        self.history.commit(project)
        self.refresh_objects(self.selected_indices())

    def refuse_locked(self, indices=None):
        indices = self.selected_indices() if indices is None else tuple(indices)
        if editor_model.has_locked(self.project.shapes, indices):
            self.show_message(editor_model.LOCKED_MESSAGE, level='warning')
            return True
        return False

    def transform_objects(self, operation):
        selected = self.selected_indices()
        if self.refuse_locked(selected):
            return
        try:
            if operation in ('left', 'right') and self.rotation_centre is not None:
                shapes = editor_model.rotate_selection(
                    self.project.shapes, selected, -90 if operation == 'left' else 90, center=self.rotation_centre)
            else:
                shapes = editor_model.transform_selection(self.project.shapes, selected, operation)
        except ValueError as error:
            self.status.set_text(str(error))
            return
        self.commit(shapes, selected)

    def align_objects(self, option):
        indices = self.selected_indices()
        if self.refuse_locked(indices):
            return
        try:
            self.commit(editor_model.align(self.project.shapes, indices, option), indices)
        except ValueError as error:
            self.status.set_text(str(error))

    def select_color(self, field):
        self.set_selection(editor_model.matching_color(self.project.shapes, self.selected_indices(), field))

    def convert_selection(self, simplify=False):
        selected = self.selected_indices()
        if self.refuse_locked(selected):
            return
        try:
            shapes = tuple(editor_model.convert_path(shape, simplify) if index in selected else shape
                           for index, shape in enumerate(self.project.shapes))
        except ValueError as error:
            self.status.set_text(str(error))
        else:
            self.commit(shapes, selected)

    def offset_selection(self):
        if not self.selected_indices():
            return
        dialog = Adw.AlertDialog(heading='Offset', body='Use a negative distance to inset the shape.')
        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        amount = self.spin(options, 'Distance (mm)', -20, 20, 1)
        protect_settings_wheel(options)
        dialog.set_extra_child(options)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('offset', 'Create Offset')
        dialog.set_close_response('cancel')
        dialog.set_default_response('offset')
        dialog.set_response_appearance('offset', Adw.ResponseAppearance.SUGGESTED)

        def apply(*_):
            before = self.project.shapes
            try:
                copies = tuple(editor_model.offset_shape(before[i], amount.get_value()) for i in self.selected_indices())
            except ValueError as error:
                self.status.set_text(str(error))
            else:
                self.commit(before + copies, range(len(before), len(before) + len(copies)))

        dialog.connect('response::offset', apply)
        dialog.present(self.parent)

    def modify_shapes(self, operation):
        if self.refuse_locked():
            return
        try:
            shapes, selected = editor_model.modify_selection(self.project.shapes, self.selected_indices(), operation)
        except ValueError as error:
            self.status.set_text(str(error))
        else:
            self.commit(shapes, selected)

    def text_dialog(self, at=None):
        selection = self.selected_indices()
        shape = self.project.shapes[selection[0]] if len(selection) == 1 else None
        spec = shape.text if shape else None
        try:
            families, family, notice = design_text.font_choices(spec)
        except ValueError as error:
            self.status.set_text(str(error))
            return
        dialog = Adw.Dialog(title='Edit Text' if spec else 'Add Text', content_width=420)
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=16, margin_end=16, margin_top=8, margin_bottom=16)
        view.set_content(form)
        dialog.set_child(view)

        value = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        value.update_property([Gtk.AccessibleProperty.LABEL], ['Text'])
        value.get_buffer().set_text(spec.value if spec else '')
        entry = Gtk.ScrolledWindow(min_content_height=90)
        entry.set_child(value)
        form.append(entry)
        fonts = Gtk.DropDown.new_from_strings(families)
        fonts.set_enable_search(True)
        initial = (self.type_families[self.type_font.get_selected()]
                   if not spec and self.active_tool == 'Type' and self.type_families else family)
        fonts.set_selected(families.index(initial) if initial in families else families.index(family))
        fonts.update_property([Gtk.AccessibleProperty.LABEL], ['Font'])
        form.append(fonts)
        size = self.spin(form, 'Size (mm)', 1, 100, spec.size_mm if spec else self.type_size.get_value())
        status = Gtk.Label(label=notice, xalign=0, wrap=True)
        fonts.connect('notify::selected', lambda *_: status.set_text(''))
        form.append(status)
        protect_settings_wheel(form)

        def apply(_button):
            buffer = value.get_buffer()
            text = buffer.get_text(*buffer.get_bounds(), False)
            updated = (replace(spec, value=text, family=families[fonts.get_selected()], size_mm=size.get_value())
                       if spec else editor_model.TextSpec(text, families[fonts.get_selected()], size.get_value(),
                            align=('left', 'center', 'right')[self.type_align.get_selected()]))
            try:
                shapes, selected = design_text.edit(self.project, updated, selection[0] if spec else None)
                if at is not None and not spec:
                    placed = replace(shapes[-1], x=at[0], y=at[1])
                    shapes = shapes[:-1] + (placed,)
            except ValueError as error:
                status.set_text(str(error))
            else:
                self.commit(shapes, selected)
                dialog.close()

        button = Gtk.Button(label='Apply' if spec else 'Add', css_classes=['suggested-action'])
        button.connect('clicked', apply)
        header.pack_end(button)
        dialog.present(self.parent)
        value.grab_focus()

    def place_sticker(self):
        def opened(path):
            project = self.project
            shape = sticker_trace.place(path, project.width, project.height)
            self.commit((*project.shapes, shape), (len(project.shapes),))
            self.status.set_text('Select Trace and Bleed to create the cut outline.')

        self.file_dialog('Place Sticker Image', opened,
                         filters=('*.png', '*.PNG', '*.jpg', '*.JPG', '*.jpeg', '*.JPEG', '*.webp', '*.WEBP'))

    def trace_sticker(self):
        selection = self.selected_indices()
        if len(selection) != 1:
            return
        shape = self.project.shapes[selection[0]]
        settings = shape.sticker
        if settings is None:
            return
        dialog = Adw.Dialog(title='Trace and Bleed', content_width=410)
        view = Adw.ToolbarView()
        dialog.set_child(view)
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=16, margin_end=16, margin_top=8, margin_bottom=16)
        view.set_content(form)
        sources = Gtk.DropDown.new_from_strings(sticker_assets.SOURCES)
        sources.set_selected(sticker_assets.SOURCES.index(settings.trace_source))
        sources.update_property([Gtk.AccessibleProperty.LABEL], ['Trace from'])
        source_row = Gtk.Box(spacing=6)
        source_row.append(Gtk.Label(label='Trace from', xalign=0, hexpand=True))
        source_row.append(sources)
        form.append(source_row)
        threshold = self.spin(form, 'Threshold', 1, 254, settings.threshold, step=1, digits=0)
        holes = Gtk.CheckButton(label='Include interior holes', active=settings.keep_holes)
        form.append(holes)
        bleed = self.spin(form, 'Print bleed (mm)', 0, 5, settings.bleed_mm, step=0.25)
        status = Gtk.Label(xalign=0, wrap=True)
        form.append(status)
        protect_settings_wheel(form)

        def apply(_button):
            try:
                traced = sticker_trace.trace(shape, source=sticker_assets.SOURCES[sources.get_selected()],
                                             threshold=threshold.get_value_as_int(), keep_holes=holes.get_active(),
                                             bleed_mm=bleed.get_value())
            except ValueError as error:
                status.set_text(str(error))
            else:
                self.change_properties(contours=traced.contours, sticker=traced.sticker)
                dialog.close()

        button = Gtk.Button(label='Trace', css_classes=['suggested-action'])
        button.connect('clicked', apply)
        header.pack_end(button)
        dialog.present(self.parent)

    def trace_next_sticker(self, *_args):
        _, index, _ = sticker_print.export_readiness(self.project)
        if index is not None:
            self.set_selection((index,))
            self.trace_sticker()

    def export_stickers(self):
        if self.exporting_stickers:
            return

        _, _, requirement = sticker_print.export_readiness(self.project)
        if requirement:
            self.show_message(requirement, level='warning')
            return

        def chosen(path):
            target = sticker_print.destination(path)
            project, cutter = self.project, self.cutter.name
            self.exporting_stickers = True
            self.refresh_status()
            self.status.set_text('Exporting Print & Cut…')

            def done(message):
                self.exporting_stickers = False
                self.refresh_status()
                self.status.set_text(message)
                return GLib.SOURCE_REMOVE

            def write():
                try:
                    result = sticker_print.export(target, project, cutter=cutter)
                except (OSError, ValueError) as error:
                    message = str(error)
                else:
                    message = f'Saved {result.name}. Open it in Send → Sheet.'
                GLib.idle_add(done, message)

            threading.Thread(target=write, daemon=True).start()

        self.file_dialog('Export Print & Cut', chosen, save_name='stickers.zip')

    def replicate_panel(self, inspector):
        self.heading(inspector, 'Replicate')
        self.columns = self.spin(inspector, 'Columns', 1, 32, 2, step=1, digits=0)
        self.rows = self.spin(inspector, 'Rows', 1, 32, 2, step=1, digits=0)
        self.column_gap = self.spin(inspector, 'Column Spacing (mm)', 0, 1000, 2)
        self.row_gap = self.spin(inspector, 'Row Spacing (mm)', 0, 1000, 2)
        self.replicate_button = self.button(inspector, 'Replicate Selection', self.replicate_selection)

    def style_panel(self, inspector):
        self.heading(inspector, 'Line and Fill')
        self.colors = {}
        self.color_pickers = {}
        self.color_edits = set()
        for field, label in (('stroke', 'Line Color'), ('fill', 'Fill Color')):
            inspector.append(Gtk.Label(label=label, xalign=0))
            entry = Gtk.Entry(placeholder_text='#rrggbb or blank')
            entry.connect('activate', self.apply_colors)
            entry.connect('changed', self.change_color, field)
            row = Gtk.Box(spacing=6)
            entry.set_hexpand(True)
            row.append(entry)
            picker = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
            picker.set_tooltip_text(f'Pick {label.lower()}.')
            picker.connect('notify::rgba', lambda widget, *_args, channel=field: self.pick_color(widget, channel))
            row.append(picker)
            inspector.append(row)
            self.colors[field] = entry
            self.color_pickers[field] = picker
        self.button(inspector, 'Apply Colors', self.apply_colors)
        self.heading(inspector, 'Line Style')
        self.line_style = Gtk.DropDown.new_from_strings(list(editor_model.LINE_STYLES.values()))
        self.line_style.set_tooltip_text('Appearance only. The cutter follows the whole line.')
        self.line_style.connect('notify::selected', self.change_line_style)
        inspector.append(self.line_style)
        self.line_width = self.spin(inspector, 'Line Width (mm)', 0, 5, 0)
        self.line_width.set_tooltip_text('Use 0 for a hairline.')
        self.line_width.connect('value-changed', self.change_field, 'line_width_mm')
        self.heading(inspector, 'Object Action')
        self.action = Gtk.DropDown.new_from_strings(ACTIONS)
        self.action.connect('notify::selected', self.change_action)
        inspector.append(self.action)

    def pick_color(self, picker, field):
        color = picker.get_rgba()
        if color is not None:
            components = (color.red, color.green, color.blue)
            self.change_properties(**{field: "#" + "".join(f"{round(value * 255):02x}" for value in components)})

    def change_line_style(self, picker, *_args):
        index = picker.get_selected()
        choices = tuple(editor_model.LINE_STYLES)
        if index < len(choices):
            self.change_properties(line_style=choices[index])

    @staticmethod
    def button(parent, label, handler, start=False):
        widget = Gtk.Button(label=label)
        widget.connect('clicked', handler)
        if isinstance(parent, Adw.HeaderBar):
            (parent.pack_start if start else parent.pack_end)(widget)
        else:
            parent.append(widget)
        return widget

    @property
    def project(self):
        return self.draft or self.history.current

    @property
    def dirty(self):
        return self.project != self.saved

    @property
    def marks(self):
        p = self.project
        return p.registration.on_page(p.width, p.height) if p.registration else None

    @property
    def mat_size(self):
        p = self.project
        if p.mat in graphtec.MATS:
            return graphtec.MATS[p.mat][1:]
        if p.mat == 'auto' and p.registration is not None:
            commands = graphtec.registration_mat_commands(self.cutter, p.width, p.height)
            if commands:
                return next((w, h) for code, w, h in graphtec.MATS.values() if code == commands[0])
        return None

    @property
    def cut_border(self):
        p = self.project
        width, height = self.mat_size or (p.width, p.height)
        x, y = 0, 0
        right = min(p.width, width, self.cutter.width_mm)
        bottom = min(p.height, height, self.cutter.length_mm)
        if (marks := self.marks) is not None:
            x, y = marks.origin_mm
            right = min(right, x + marks.reg_width_mm)
            bottom = min(bottom, y + marks.reg_length_mm)
        return geometry.Rect(x, y, right - x, bottom - y) if right > x and bottom > y else None

    def selected_indices(self):
        chosen = set()
        for row in self.objects.get_selected_rows():
            entry = getattr(row, '_object_entry', None)
            if not entry:
                chosen.add(row.get_index())
            elif entry[0] == 'group':
                chosen.update(entry[2])
            else:
                chosen.add(entry[1])
        return tuple(sorted(chosen))

    def set_selection(self, indices):
        chosen = set(indices)
        previous, self.loading = self.loading, True
        self.objects.unselect_all()
        row = self.objects.get_first_child()
        while row is not None:
            entry = getattr(row, '_object_entry', None)
            if entry:
                if entry[0] == 'shape' and entry[1] in chosen:
                    self.objects.select_row(row)
                elif entry[0] == 'group' and entry[1] in self.collapsed_groups and set(entry[2]) <= chosen:
                    self.objects.select_row(row)
            row = row.get_next_sibling()
        self.loading = previous
        self.select_shape()

    def open_object_context(self, _gesture, _count, x, y):
        row = self.objects.get_row_at_y(int(y))
        if row is not None and not row.is_selected():
            entry = getattr(row, '_object_entry', None)
            if entry:
                self.set_selection(entry[2] if entry[0] == 'group' else (entry[1],))
            else:
                self.set_selection((row.get_index(),))
        anchor = Gdk.Rectangle()
        anchor.x, anchor.y, anchor.width, anchor.height = int(x), int(y), 1, 1
        menu = Gtk.PopoverMenu.new_from_model(self.object_context_items)
        menu.set_parent(self.objects)
        menu.set_pointing_to(anchor)
        menu.connect('closed', lambda widget: widget.unparent())
        menu.popup()

    def drop_objects(self, row, source_index, y):
        list_row = row.get_parent()
        entry = getattr(list_row, '_object_entry', None)
        after = y >= row.get_height() / 2
        if entry and entry[0] == 'group':
            target = (entry[2][-1] + 1) if after else entry[2][0]
        elif entry and entry[0] == 'shape':
            target = entry[1] + int(after)
        else:
            target = list_row.get_index() + int(after)
        if not 0 <= source_index < len(self.project.shapes):
            return False
        selected = self.selected_indices()
        if source_index not in selected:
            selected = (source_index,)
        shapes, selection = editor_model.reorder_objects(self.project.shapes, selected, target)
        self.commit(shapes, selection)
        return True

    def set_object_visibility(self, targets, visible, toggle=None):
        if self.loading:
            return
        targets = {targets} if isinstance(targets, int) else set(targets)
        shapes = self.project.shapes
        was_focused = toggle is not None and self.parent.get_focus() is toggle
        updated = tuple(replace(shape, visible=visible) if i in targets else shape for i, shape in enumerate(shapes))
        self.commit(updated, self.selected_indices())
        if was_focused and len(targets) == 1:
            index = next(iter(targets))
            row = self.objects.get_first_child()
            while row is not None:
                entry = getattr(row, '_object_entry', None)
                if entry and entry[0] == 'shape' and entry[1] == index:
                    row.get_child().get_first_child().grab_focus()
                    break
                row = row.get_next_sibling()

    def toggle_group_collapsed(self, group_id):
        selection = self.selected_indices()
        if group_id in self.collapsed_groups:
            self.collapsed_groups.remove(group_id)
        else:
            self.collapsed_groups.add(group_id)
        self.refresh_objects(selection)

    def toggle_selected_group(self):
        groups = sorted({self.project.shapes[i].group for i in self.selected_indices()
                         if self.project.shapes[i].group is not None})
        if not groups:
            return
        if all(g in self.collapsed_groups for g in groups):
            self.collapsed_groups.difference_update(groups)
        else:
            self.collapsed_groups.update(groups)
        self.refresh_objects(self.selected_indices())

    def rename_object(self, name=None):
        indices = self.selected_indices()
        if len(indices) != 1:
            return
        index = indices[0]
        shape = self.project.shapes[index]

        def apply_name(raw):
            try:
                cleaned = editor_model.clean_object_name(raw)
            except ValueError as error:
                self.show_message(str(error), level='warning')
                return
            shapes = self.project.shapes
            self.commit(shapes[:index] + (replace(shape, name=cleaned),) + shapes[index + 1:], (index,))

        if name is not None:
            apply_name(name)
            return
        alert = Adw.AlertDialog(heading='Rename Object', body='Leave blank to use the automatic name.')
        entry = Gtk.Entry(text=shape.name or editor_model.object_name(shape))
        entry.update_property([Gtk.AccessibleProperty.LABEL], ['Object name'])
        alert.set_extra_child(entry)
        alert.add_response('cancel', 'Cancel')
        alert.add_response('rename', 'Rename')
        alert.set_close_response('cancel')
        alert.set_default_response('rename')
        alert.connect('response', lambda _d, response: apply_name(entry.get_text()) if response == 'rename' else None)
        alert.present(self.parent)
        entry.grab_focus()

    def toggle_lock(self):
        indices = self.selected_indices()
        if not indices:
            return
        shapes = self.project.shapes
        lock = not all(shapes[i].locked for i in indices)
        updated = tuple(replace(shape, locked=lock) if i in indices else shape for i, shape in enumerate(shapes))
        self.commit(updated, indices)

    def set_object_locked(self, target, locked):
        if self.loading:
            return
        targets = {target} if isinstance(target, int) else set(target)
        shapes = self.project.shapes
        updated = tuple(replace(shape, locked=locked) if i in targets else shape for i, shape in enumerate(shapes))
        self.commit(updated, self.selected_indices())

    def commit_inline_name(self, index, text):
        if self.loading or index >= len(self.project.shapes):
            return
        shape = self.project.shapes[index]
        default_display = editor_model.object_display_name(self.project.shapes, index, unit=self.unit)
        if not shape.name and text.strip() == default_display:
            return
        try:
            cleaned = editor_model.clean_object_name(text)
        except ValueError as error:
            self.show_message(str(error), level='warning')
            self.refresh_objects(self.selected_indices())
            return
        shapes = self.project.shapes
        self.commit(shapes[:index] + (replace(shape, name=cleaned),) + shapes[index + 1:], (index,))

    def refresh_objects(self, selection=()):
        self.loading = True
        self.refresh_page()
        shapes = self.project.shapes
        active_groups = {shape.group for shape in shapes if shape.group is not None}
        self.collapsed_groups &= active_groups
        while (row := self.objects.get_row_at_index(0)) is not None:
            self.objects.remove(row)
        for entry in editor_model.object_rows(shapes, self.collapsed_groups):
            if entry[0] == 'group':
                _, gid, members = entry
                collapsed = gid in self.collapsed_groups
                all_locked = all(shapes[m].locked for m in members)
                any_visible = any(shapes[m].visible for m in members)
                count = '1 object' if len(members) == 1 else f'{len(members)} objects'
                label = f'Group {gid} ({count})'
                box = Gtk.Box(spacing=4, margin_top=2, margin_bottom=2, margin_start=4, margin_end=6,
                              tooltip_text=label + (' · Locked' if all_locked else ''))
                expander = Gtk.Button(icon_name='pan-end-symbolic' if collapsed else 'pan-down-symbolic',
                                      css_classes=['flat', 'scc-object-visibility'],
                                      tooltip_text='Expand group' if collapsed else 'Collapse group')
                expander.update_property([Gtk.AccessibleProperty.LABEL],
                                         [f"{'Expand' if collapsed else 'Collapse'} Group {gid}"])
                expander.connect('clicked', lambda _b, group_id=gid: self.toggle_group_collapsed(group_id))
                box.append(expander)
                visible = Gtk.ToggleButton(icon_name='view-reveal-symbolic' if any_visible else 'view-conceal-symbolic',
                                           active=any_visible, css_classes=['flat', 'scc-object-visibility'])
                visible.update_property([Gtk.AccessibleProperty.LABEL], [f'Visibility of Group {gid}'])
                visible.connect('toggled', lambda button, group_members=members:
                                self.set_object_visibility(group_members, button.get_active()))
                box.append(visible)
                lock_btn = design_icon_button(COMMAND_ICONS['lock' if all_locked else 'unlock'],
                                              toggle=True, size=24, icon_size=14)
                lock_btn.set_active(all_locked)
                lock_btn.add_css_class('scc-object-lock')
                if all_locked:
                    lock_btn.add_css_class('locked')
                lock_btn.set_tooltip_text('Unlock group' if all_locked else 'Lock group')
                lock_btn.connect('toggled', lambda button, group_members=members:
                                 self.set_object_locked(group_members, button.get_active()))
                box.append(lock_btn)
                box.append(tool_image(COMMAND_ICONS['group'], 16))
                box.append(Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
                list_row = Gtk.ListBoxRow(child=box)
                list_row._object_entry = ('group', gid, members)
                self.objects.append(list_row)
                continue
            _, i, indent = entry
            obj = shapes[i]
            tooltip = obj.action if obj.group is None else f'{obj.action} · Group {obj.group}'
            tool = f' · Tool {obj.holder}' if obj.holder else ''
            display = editor_model.object_display_name(shapes, i, unit=self.unit)
            name = f'{display}{tool}'
            box = Gtk.Box(spacing=5, margin_top=2, margin_bottom=2, margin_start=20 if indent else 6, margin_end=6,
                          tooltip_text=f'{name}\n{tooltip}' + (' · Locked' if obj.locked else ''))
            visible = Gtk.ToggleButton(icon_name='view-reveal-symbolic' if obj.visible else 'view-conceal-symbolic',
                                       active=obj.visible, css_classes=['flat', 'scc-object-visibility'])
            visible.update_property([Gtk.AccessibleProperty.LABEL], [f'Visibility of {name}'])
            visible.set_tooltip_text('Hide object' if obj.visible else 'Show object')
            visible.connect('toggled', lambda button, index=i: self.set_object_visibility(index, button.get_active(), button))
            box.append(visible)
            lock_btn = design_icon_button(COMMAND_ICONS['lock' if obj.locked else 'unlock'],
                                          toggle=True, size=24, icon_size=14)
            lock_btn.set_active(obj.locked)
            lock_btn.add_css_class('scc-object-lock')
            if obj.locked:
                lock_btn.add_css_class('locked')
            lock_btn.set_tooltip_text('Unlock object' if obj.locked else 'Lock object')
            lock_btn.update_property([Gtk.AccessibleProperty.LABEL], [f'Lock {name}'])
            lock_btn.connect('toggled', lambda button, index=i: self.set_object_locked(index, button.get_active()))
            box.append(lock_btn)
            box.append(tool_image(OBJECT_ICONS.get(obj.kind, COMMAND_ICONS['path']), 16))
            name_label = Gtk.EditableLabel(text=name, hexpand=True)
            name_label.update_property([Gtk.AccessibleProperty.LABEL], [name])
            name_label.connect('notify::editing', lambda lbl, _p, index=i:
                               (not lbl.get_editing()) and self.commit_inline_name(index, lbl.get_text()))
            box.append(name_label)
            source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            source.connect('prepare', lambda _src, _x, _y, index=i:
                           Gdk.ContentProvider.new_for_value(index))
            box.add_controller(source)
            target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
            target.connect('drop', lambda action, value, _x, y, widget=box:
                           bool(action.get_current_drop().get_drag()) and self.drop_objects(widget, value, y))
            box.add_controller(target)
            list_row = Gtk.ListBoxRow(child=box)
            list_row._object_entry = ('shape', i)
            self.objects.append(list_row)
        self.loading = False
        self.set_selection(selection)

    def refresh_page(self):
        previous, self.loading = self.loading, True
        p = self.project
        named = geometry.match_sheet((p.width, p.height))
        name = 'Custom' if self.custom_page or not named else named[0]
        self.page.set_selected(self.page_sizes.index(name))
        self.orientation.set_selected(0 if p.width <= p.height else 1)
        for input_, value in ((self.page_width, p.width), (self.page_height, p.height)):
            input_.set_sensitive(name == 'Custom')
            input_.get_parent().set_visible(name == 'Custom')
            input_.set_value(value)
        self.mat.set_selected(list(graphtec.MAT_OPTIONS).index(p.mat))
        reg = p.registration or self.last_registration
        self.register.set_active(p.registration is not None)
        for widget in (self.mark_pattern, self.mark_inset, self.mark_length, self.mark_thickness):
            widget.set_sensitive(self.register.get_active())
        self.mark_pattern.set_selected(regmarks.PATTERNS.index(reg.pattern))
        self.mark_inset.set_value(reg.inset_mm)
        self.mark_length.set_value(reg.length_mm)
        self.mark_thickness.set_value(reg.thickness_mm)
        self.loading = previous

    def refresh_status(self):
        p = self.project
        action = editor_model.send_action(p.shapes)
        count = len(p.artwork(action).paths) if action else 0
        self.status.set_text(editor_model.document_summary(p, count, len(self.selected_indices()), unit=self.unit))
        name = self.project_path.name if self.project_path else self.source_name or "Untitled"
        self.document_title.update_property([Gtk.AccessibleProperty.LABEL], [name])
        self.document_title.set_text(name)
        self.document_title.set_tooltip_text(f'{name} (unsaved changes)' if self.dirty else name)
        badge = editor_model.save_state_label(self.project_path, self.dirty)
        self.save_state.set_text(badge)
        self.save_state.set_visible(bool(badge))
        if self.dirty:
            self.save_state.add_css_class('warning')
        else:
            self.save_state.remove_css_class('warning')
        if action and self.notice_bar.get_visible() and self.notice_bar.has_css_class('scc-notice-warning'):
            self.notice_bar.set_visible(False)
        self.save_button.set_sensitive(self.dirty or self.project_path is None)
        self.undo_button.set_sensitive(bool(self.history.past))
        self.redo_button.set_sensitive(bool(self.history.future))
        self.menu_actions.lookup_action('undo').set_enabled(bool(self.history.past))
        self.menu_actions.lookup_action('redo').set_enabled(bool(self.history.future))
        stickers, next_trace, requirement = sticker_print.export_readiness(p)
        self.review.set_visible(bool(stickers))
        ready = not requirement and not self.exporting_stickers
        self.review.set_sensitive(ready)
        self.review.set_tooltip_text(requirement or None)
        self.menu_actions.lookup_action('export-stickers').set_enabled(ready)
        self.trace_next.set_visible(next_trace is not None)
        if stickers and requirement:
            self.status.set_text(requirement)
        self.refresh_tool_hint()
        self.canvas.queue_draw()

    def refresh_tool_hint(self):
        tool = TOOLS[self.tool.get_selected()]
        indices = self.selected_indices()
        shapes = self.project.shapes
        obj = shapes[indices[0]] if len(indices) == 1 else None
        hint = contextual_hint(
            tool,
            shape_count=len(shapes),
            selected_count=len(indices),
            is_sticker=bool(obj and obj.sticker),
            is_text=bool(obj and obj.text),
            needs_trace=bool(obj and obj.sticker and not obj.contours),
            node_selected=obj is not None and self.active_node(obj) is not None,
        )
        self.tool_hint.set_label(hint)
        point_edit = self.active_tool == 'Point Edit'
        self.tool_hint.set_visible(point_edit)
        self.point_menu.set_visible(point_edit)
        chosen = point_edit and obj is not None and self.active_node(obj) is not None
        for index, action in enumerate(path_edit.POINT_ACTIONS):
            enabled = (point_edit and obj is not None and path_edit.editable(obj)
                       if action == 'Simplify' else chosen)
            self.menu_actions.lookup_action(f'point-action-{index}').set_enabled(enabled)
        self.status.set_tooltip_text(hint)
        self.tool_hint.set_tooltip_text(f'{tool.tooltip}\n{hint}' if hint != tool.hint else tool.tooltip)

    def selection_presentation(self, picker, *_args):
        focused = self.parent.get_focus()
        if focused and focused.is_ancestor(picker):
            target = picker.get_visible_child()
            if target is not None:
                target.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def select_shape(self, *_args):
        if self.loading:
            return
        indices = self.selected_indices()
        shapes = self.project.shapes
        obj = shapes[indices[0]] if len(indices) == 1 else None
        can_trace = bool(obj and obj.sticker)
        self.trace_button.set_sensitive(can_trace)
        self.menu_actions.lookup_action('trace-sticker').set_enabled(can_trace)
        self.loading = True
        box = editor_model.bounds(shapes[i] for i in indices) if len(indices) > 1 else None
        bounds_fields = {'x': box.x, 'y': box.y, 'width': box.w, 'height': box.h} if box else {}
        for field, spin in self.fields.items():
            enabled = obj is not None and (field != 'radius' or obj.kind == 'Rounded rectangle')
            spin.set_sensitive(enabled or field in bounds_fields)
            if field in bounds_fields:
                if field in ('width', 'height'):
                    spin.set_range(0.1, 1000)
                spin.set_value(bounds_fields[field])
            elif obj is not None:
                if field in ('width', 'height'):
                    low = -1000 if obj.kind == 'Line' else 0 if obj.kind == 'Path' else 0.1
                    spin.set_range(low, 1000)
                spin.set_value(getattr(obj, field))
        self.lock_aspect.set_sensitive(obj is not None and min(obj.width, obj.height) > 0)
        for field, toggle in self.flips.items():
            toggle.set_sensitive(obj is not None)
            if obj is not None:
                toggle.set_active(getattr(obj, field))
        for field, entry in self.colors.items():
            values = {getattr(shapes[index], field) for index in indices}
            entry.set_sensitive(bool(indices))
            entry.set_placeholder_text('Mixed' if len(values) > 1 else '#rrggbb or blank')
            entry.set_text(values.pop() if len(values) == 1 else '')
            picker = self.color_pickers[field]
            picker.set_sensitive(bool(indices))
            rgba = Gdk.RGBA()
            if rgba.parse(entry.get_text() or '#313945'):
                picker.set_rgba(rgba)
        self.color_edits.clear()
        styles = {shapes[index].line_style for index in indices}
        self.line_style.set_sensitive(bool(indices))
        self.line_style.set_selected(tuple(editor_model.LINE_STYLES).index(styles.pop()) if len(styles) == 1
                                     else Gtk.INVALID_LIST_POSITION)
        self.line_width.set_sensitive(bool(indices))
        self.line_width.set_value(obj.line_width_mm if obj else 0)
        self.action.set_sensitive(bool(indices))
        object_actions = {shapes[index].action for index in indices}
        self.action.set_selected(ACTIONS.index(object_actions.pop()) if len(object_actions) == 1
                                 else Gtk.INVALID_LIST_POSITION)
        self.rename_button.set_sensitive(len(indices) == 1)
        self.lock_button.set_sensitive(bool(indices))
        all_locked = bool(indices) and all(shapes[i].locked for i in indices)
        self.lock_button.set_label('Unlock' if all_locked else 'Lock')
        selected_groups = sorted({shapes[i].group for i in indices if shapes[i].group is not None})
        self.toggle_group_button.set_visible(bool(selected_groups))
        self.toggle_group_button.set_sensitive(bool(selected_groups))
        if selected_groups:
            collapsed = all(g in self.collapsed_groups for g in selected_groups)
            self.toggle_group_button.set_label('Expand Group' if collapsed else 'Collapse Group')
        self.origin_grid.set_sensitive(bool(indices))
        if self.rotation_centre is not None and not indices:
            self.rotation_centre = None
        self.menu_actions.lookup_action('rename-object').set_enabled(len(indices) == 1)
        self.menu_actions.lookup_action('toggle-lock').set_enabled(bool(indices))
        for btn in (self.duplicate, self.remove, self.duplicate_ribbon_button, self.delete_ribbon_button):
            btn.set_sensitive(bool(indices))
        for name in ('cut', 'copy', 'duplicate', 'delete'):
            self.menu_actions.lookup_action(name).set_enabled(bool(indices))
        for name in ('paste', 'paste-front'):
            self.menu_actions.lookup_action(name).set_enabled(bool(self.clipboard))
        self.replicate_button.set_sensitive(bool(indices))
        can_group = (len(indices) >= 2 and
                     (shapes[indices[0]].group is None or len({shapes[i].group for i in indices}) > 1))
        self.group_button.set_sensitive(can_group)
        self.ungroup_button.set_sensitive(any(shapes[i].group is not None for i in indices))
        units = len(editor_model.selected_units(shapes, indices))
        any_grouped = any(shapes[i].group is not None for i in indices)
        show_selection_bar = units > 1 or any_grouped or can_trace or bool(obj and obj.text)
        self.selection_separator.set_visible(True)
        self.selection_tools.set_visible(show_selection_bar)
        self.group_ribbon_button.set_visible(units > 1)
        self.ungroup_ribbon_button.set_visible(any_grouped)
        self.align_button.set_visible(units >= 2)
        self.selection_squeezer.set_visible(units > 1 or any_grouped)
        self.compact_selection.remove_all()
        if units > 1:
            self.compact_selection.append('Group', 'design.group')
        if any_grouped:
            self.compact_selection.append('Ungroup', 'design.ungroup')
        if units > 1:
            align = Gio.Menu()
            for index, label in enumerate(ALIGNMENTS):
                align.append(label, f'design.align-{index}')
            self.compact_selection.append_submenu('Align', align)
        self.trace_button.set_visible(can_trace)
        self.edit_text_ribbon_button.set_visible(bool(obj and obj.text))
        for command in self.transform_buttons.values():
            command.set_sensitive(bool(indices))
        for option, command in self.alignment_buttons.items():
            space = option.startswith('Space ')
            sensitive = units >= (3 if space else 2)
            tip = (f"{option}\nSelect {'three objects to space' if space else 'two objects to align'}."
                   if not sensitive else option)
            command.set_sensitive(sensitive)
            command.set_tooltip_text(tip)
            self.menu_actions.lookup_action(f'align-{ALIGNMENTS.index(option)}').set_enabled(sensitive)
            ribbon_align = self.ribbon_align_buttons[option]
            ribbon_align.set_sensitive(sensitive)
            ribbon_align.set_tooltip_text(tip)
        self.forward.set_sensitive(bool(indices) and indices[-1] + 1 < len(shapes))
        self.backward.set_sensitive(bool(indices) and indices[0] > 0)
        self.loading = False
        self.refresh_measurement()
        self.refresh_context_fields()
        self.refresh_context_type()
        self.refresh_status()

    def commit(self, shapes, selection):
        shapes = tuple(shapes)
        self.history.commit(replace(self.history.current, shapes=shapes))
        self.refresh_objects(selection)
        if not shapes:
            self.open_panel(0)
        elif self.active_panel_index() == 0 and selection:
            self.open_panel(1)

    def change_history(self, callback):
        indices = self.selected_indices()
        self.cancel_drag()
        callback()
        self.refresh_objects(indices)

    def change_properties(self, **values):
        indices = self.selected_indices()
        if self.loading or not indices:
            return
        if self.refuse_locked(indices):
            self.select_shape()
            return
        shapes = tuple(replace(shape, **values) if i in indices else shape
                       for i, shape in enumerate(self.project.shapes))
        if "holder" in values:
            self.commit(shapes, indices)
            return
        self.history.commit(replace(self.history.current, shapes=shapes))
        self.select_shape()

    def change_field(self, spin, field):
        if self.loading:
            return
        value = spin.get_value()
        values = {field: value}
        indices = self.selected_indices()
        if indices and self.refuse_locked(indices):
            self.select_shape()
            return
        if len(indices) > 1:
            if field in ('x', 'y', 'width', 'height'):
                try:
                    shapes = editor_model.edit_selection_bounds(self.project.shapes, indices, field, value)
                except ValueError as error:
                    self.show_message(str(error), level='warning')
                    self.select_shape()
                else:
                    self.commit(shapes, indices)
            return
        if len(indices) == 1 and field in ('width', 'height') and self.lock_aspect.get_active():
            shape = self.project.shapes[indices[0]]
            if min(shape.width, shape.height) > 0 and value > 0:
                other = 'height' if field == 'width' else 'width'
                ratio = getattr(shape, other) / getattr(shape, field)
                minimum = max(spin.get_adjustment().get_lower(),
                              self.fields[other].get_adjustment().get_lower() / ratio)
                value = max(minimum, min(value, 1000 / ratio))
                values = {field: value, other: value * ratio}
        self.change_properties(**values)

    def change_toggle(self, toggle, field):
        self.change_properties(**{field: toggle.get_active()})

    def change_action(self, picker, *_args):
        if picker.get_selected() < len(ACTIONS):
            self.change_properties(action=ACTIONS[picker.get_selected()])

    def change_color(self, _entry, field):
        if not self.loading:
            self.color_edits.add(field)

    def apply_colors(self, *_args):
        try:
            values = {field: editor_model.stroke_color(self.colors[field].get_text().strip())
                      for field in self.color_edits}
        except ValueError as error:
            self.status.set_text(str(error))
            return
        if values:
            self.change_properties(**values)

    def set_media_size(self, width, height):
        self.history.commit(replace(self.history.current, width=width, height=height))
        self.refresh_page()
        self.fit(None)
        self.refresh_status()

    def change_page(self, *_args):
        if self.loading:
            return
        name = self.page_sizes[self.page.get_selected()]
        self.custom_page = name == 'Custom'
        if self.custom_page:
            self.refresh_page()
        else:
            self.set_media_size(*geometry.SHEETS[name].size(self.orientation.get_selected() == 0))

    def change_orientation(self, *_args):
        if self.loading:
            return
        short, long = sorted((self.project.width, self.project.height))
        size = (short, long) if self.orientation.get_selected() == 0 else (long, short)
        self.set_media_size(*size)

    def change_custom_page(self, *_args):
        if not self.loading and self.page_sizes[self.page.get_selected()] == 'Custom':
            self.set_media_size(self.page_width.get_value(), self.page_height.get_value())

    def change_mat(self, *_args):
        if not self.loading:
            selected = list(graphtec.MAT_OPTIONS)[self.mat.get_selected()]
            self.history.commit(replace(self.history.current, mat=selected))
            self.refresh_status()

    def change_registration(self, *_args):
        if self.loading:
            return
        setup = None
        if self.register.get_active():
            setup = Registration(inset_mm=self.mark_inset.get_value(), length_mm=self.mark_length.get_value(),
                                 thickness_mm=self.mark_thickness.get_value(),
                                 pattern=regmarks.PATTERNS[self.mark_pattern.get_selected()])
            self.last_registration = setup
        self.history.commit(replace(self.history.current, registration=setup))
        self.refresh_page()
        self.refresh_status()

    def add_shape(self, _button):
        p = self.project
        shape = Shape(editor_model.shape_to_add(self.active_tool), x=(p.width - 40) / 2, y=(p.height - 30) / 2)
        self.commit(p.shapes + (shape,), (len(p.shapes),))
        if not self.dock.get_visible():
            self.toggle_panels()
        self.open_panel(1)
        self.fields['x'].grab_focus()
        self.fields['x'].select_region(0, -1)

    def group_selected(self, *, release=False):
        shapes, indices = self.project.shapes, self.selected_indices()
        if indices:
            selection = {member for i in indices for member in editor_model.group_members(shapes, i)}
            edited = editor_model.group_selection(shapes, indices, release=release)
            self.commit(edited, tuple(sorted(selection)))

    def duplicate_shape(self, _button):
        shapes, indices = self.project.shapes, self.selected_indices()
        if not indices:
            return
        try:
            result = editor_model.duplicate(shapes, indices)
            self.commit(result, range(len(shapes), len(result)))
        except ValueError as error:
            self.status.set_text(str(error))

    def copy(self, *, cut=False):
        indices = self.selected_indices()
        if not indices:
            return
        if cut and self.refuse_locked(indices):
            return
        self.clipboard = tuple(self.project.shapes[i] for i in indices)
        if cut:
            self.delete_shape(None)
        else:
            self.select_shape()

    def paste(self, *, in_front=False):
        if self.clipboard:
            shapes = self.project.shapes
            pasted = editor_model.paste(shapes, self.clipboard, offset=not in_front)
            self.commit(pasted, range(len(shapes), len(pasted)))

    def delete_shape(self, _button):
        shapes, indices = self.project.shapes, self.selected_indices()
        if self.refuse_locked(indices):
            return
        self.commit((shape for i, shape in enumerate(shapes) if i not in indices), ())

    def transfer_properties(self, source):
        selection = self.selected_indices()
        if source is None:
            self.show_message('Click a shape to copy its properties.')
            return
        if not selection:
            self.show_message('Select the shapes to change first, then click the shape to copy with the Eyedropper.')
            return
        if self.refuse_locked(selection):
            return
        shape = self.project.shapes[source]
        self.change_properties(action=shape.action, stroke=shape.stroke, fill=shape.fill,
                               line_style=shape.line_style, line_width_mm=shape.line_width_mm,
                               holder=shape.holder)

    def knife_slice(self, start, end):
        shapes = self.project.shapes
        targets = set(self.selected_indices()) or set(range(len(shapes)))
        out, made = [], []
        try:
            for index, shape in enumerate(shapes):
                pieces = (editor_model.slice_shape(shape, start, end)
                          if index in targets and shape.visible and not shape.locked and shape.closed
                          and not shape.sticker and shape.kind != 'Line' else (shape,))
                if len(pieces) > 1:
                    made.extend(range(len(out), len(out) + len(pieces)))
                out.extend(pieces)
        except ValueError as error:
            self.show_message(str(error), level='warning')
            return
        if not made:
            self.show_message('The knife line has to cross a closed shape all the way to split it.')
            self.canvas.queue_draw()
            return
        self.commit(out, made)

    def erase_stroke(self, stroke):
        shapes = self.project.shapes
        targets = self.selected_indices() or range(len(shapes))
        try:
            out, changed = editor_model.erase(shapes, targets, stroke, self.eraser_size.get_value() / 2)
        except ValueError as error:
            self.show_message(str(error), level='warning')
            return
        if changed:
            self.commit(out, ())
        else:
            self.canvas.queue_draw()

    def reorder(self, step):
        shapes, indices = list(self.project.shapes), self.selected_indices()
        if not indices or indices[0] + step < 0 or indices[-1] + step >= len(shapes):
            return
        for i in (reversed(indices) if step > 0 else indices):
            shapes[i], shapes[i+step] = shapes[i+step], shapes[i]
        self.commit(shapes, (i + step for i in indices))

    def replicate_selection(self, _button):
        original, indices = self.project.shapes, self.selected_indices()
        try:
            shapes = editor_model.replicate(original, indices, self.columns.get_value_as_int(),
                                            self.rows.get_value_as_int(),
                                            self.column_gap.get_value(), self.row_gap.get_value())
            self.commit(shapes, (*indices, *range(len(original), len(shapes))))
        except ValueError as error:
            self.status.set_text(str(error))

    def fit(self, _button):
        self.canvas.pan = (0, 0)
        self.canvas.zoom = 1.0
        self.zoom_readout.set_label('100%')
        self.canvas.queue_draw()

    @property
    def active_tool(self):
        return self.tools[self.tool.get_selected()]

    def tool_changed(self):
        self.canvas.grab_focus()
        self.selected_node = None
        self.polygon.clear()
        self.cancel_drag()
        self.measurement = None
        self.measure_options.set_visible(self.active_tool == 'Measure')
        self.refresh_measurement()
        tool = TOOLS[self.tool.get_selected()]
        transforming = tool.name in TRANSFORM_MODES
        self.refresh_cursor()
        self.option_snap.set_visible(tool.name not in (
            'Rotate', 'Scale', 'Hand', 'Zoom', 'Measure', 'Knife', 'Eraser', 'Eyedropper'))
        self.eraser_options.set_visible(tool.name == 'Eraser')
        self.sides_option.set_visible(tool.name == "Regular Polygon")
        self.close_polygon.set_visible(tool.name in CLICK_PATH_TOOLS)
        self.refresh_context_fields()
        self.refresh_context_type()
        self.radius_option.set_visible(tool.name == 'Rounded rectangle')
        self.refresh_tool_hint()
        if transforming:
            self.open_panel(1)

    def refresh_cursor(self):
        if self.space_pan:
            self.canvas.set_cursor_from_name('grab')
            return
        select_cursor = self.active_tool in ('Select', 'Point Edit') or self.active_tool in TRANSFORM_MODES
        self.canvas.set_cursor_from_name({'Hand': 'grab', 'Zoom': 'zoom-in', 'Type': 'text',
                                          'Eyedropper': 'pointer'}.get(
            self.active_tool, 'default' if select_cursor else 'crosshair'))

    def release_pan(self):
        if self.space_pan:
            self.space_pan = False
            self.refresh_cursor()

    def canvas_key_released(self, _controller, key, _code, _state):
        if key == Gdk.KEY_space:
            self.release_pan()

    def pick_polygon_point(self, gesture, presses, x, y):
        if self.space_pan or self.active_tool not in ('Polygon', 'Curve'):
            return
        self.canvas.grab_focus()
        left, top, scale = self.canvas.viewport(self.canvas.get_width(), self.canvas.get_height())
        point = self.grid_point((x-left)/scale, (y-top)/scale)
        state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
        if self.active_tool == 'Polygon' and self.polygon and (state & Gdk.ModifierType.SHIFT_MASK):
            point = constrain_angle(self.polygon[-1], point)
        if max(map(abs, point)) > editor_model.SHAPE_LIMIT:
            return
        curve = self.active_tool == "Curve"
        if presses >= 2:
            if len(self.polygon) >= 2:
                self.finish_polygon(closed=False)
            return
        if len(self.polygon) >= 3 and math.dist(point, self.polygon[0]) <= (6 if curve else 7) / scale:
            self.finish_polygon()
            return
        if not self.polygon or self.polygon[-1] != point:
            self.polygon.append(point)
        self.canvas.queue_draw()

    def finish_polygon(self, *, closed=True):
        if len(set(self.polygon)) < (3 if closed else 2):
            self.status.set_text("Place at least three points to close the outline." if closed
                                 else "Place at least two points to draw a line.")
            return
        try:
            points = tuple(self.polygon)
            nodes = (path_edit.from_clicks(points, closed=closed) if self.active_tool == 'Curve'
                     else path_edit.Nodes(points, (), closed))
            shape = path_edit.to_shape(nodes, Shape("Path"))
        except ValueError as error:
            self.status.set_text(str(error))
            return
        self.polygon.clear()
        self.commit(self.project.shapes + (shape,), (len(self.project.shapes),))

    def grid_point(self, x, y):
        if self.snap.get_active():
            spacing = self.grid_spacing.get_value()
            x, y = round(x / spacing) * spacing, round(y / spacing) * spacing
        return round(x, 4), round(y, 4)

    def drag_begin(self, gesture, x, y):
        self.canvas.grab_focus()
        left, top, scale = self.canvas.viewport(self.canvas.get_width(), self.canvas.get_height())
        x, y = (x-left)/scale, (y-top)/scale
        mode = 'Hand' if self.space_pan else self.active_tool
        if mode == 'Measure':
            self.measurement = ((x, y), (x, y))
            self.drag = Drag('measure', x, y, scale)
            self.refresh_measurement()
            self.canvas.queue_draw()
            return
        if mode == 'Eyedropper':
            source = next((index for index in reversed(range(len(self.project.shapes)))
                           if self.project.shapes[index].visible
                           and editor_model.hit(self.project.shapes[index], (x, y), 5 / scale)), None)
            self.transfer_properties(source)
            return
        if mode == 'Knife':
            self.knife = None
            self.drag = Drag('knife', x, y, scale)
            return
        if mode == 'Eraser':
            self.eraser_stroke = [(x, y)]
            self.drag = Drag('erase', x, y, scale)
            return
        if mode == 'Hand':
            self.drag = Drag('pan', x, y, scale, start_pan=self.canvas.pan)
            return
        if mode == 'Zoom':
            state = gesture.get_current_event_state()
            factor = 0.8 if state & Gdk.ModifierType.ALT_MASK else 1.25
            self.canvas.zoom_by(factor, self.canvas.pointer)
            return
        if mode == 'Type':
            for index in reversed(range(len(self.project.shapes))):
                shape = self.project.shapes[index]
                if shape.visible and shape.text and editor_model.hit(shape, (x, y), 5 / scale):
                    self.set_selection((index,))
                    self.text_dialog()
                    return
            self.set_selection(())
            self.text_dialog(at=self.grid_point(x, y))
            return
        if mode in ('Polygon', 'Curve'):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if mode == 'Point Edit':
            self.begin_point_edit(x, y, scale)
            return
        if mode != 'Select' and mode not in TRANSFORM_MODES:
            if not (-editor_model.SHAPE_LIMIT <= x <= editor_model.SHAPE_LIMIT
                    and -editor_model.SHAPE_LIMIT <= y <= editor_model.SHAPE_LIMIT):
                return
            start = self.grid_point(x, y)
            self.drawing = True
            self.drag = Drag(mode, x, y, scale, points=[start])
            return

        extend = mode != 'Rotate' and bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        indices = self.selected_indices()
        if self.rotation_centre and math.dist(self.rotation_centre, (x, y)) <= 7 / scale:
            self.drag = Drag('centre', x, y, scale, indices)
            self.canvas.set_cursor_from_name('grabbing')
            return
        handle = rotation_handle(self.project.shapes, indices, scale) if mode in TRANSFORM_MODES else None
        scale_anchor = handle.opposite_corner((x, y)) if handle and mode in SCALE_MODES else None
        if handle and mode in ROTATE_MODES and handle.hit((x, y)):
            if self.refuse_locked(indices):
                return
            self.drag = Drag('rotate', x, y, scale, indices, rotation=handle)
            self.canvas.set_cursor_from_name('grabbing')
            return
        if scale_anchor is not None:
            if self.refuse_locked(indices):
                return
            self.drag = Drag('scale', x, y, scale, indices, scale_anchor=scale_anchor)
            self.canvas.set_cursor_from_name('grabbing')
            return
        for index in reversed(range(len(self.project.shapes))):
            shape = self.project.shapes[index]
            if shape.visible and editor_model.hit(shape, (x, y), 5 / scale):
                members = set(editor_model.group_members(self.project.shapes, index))
                if extend:
                    chosen = set(indices)
                    self.set_selection(sorted(chosen - members if members <= chosen else chosen | members))
                elif not members <= set(indices):
                    self.set_selection(tuple(sorted(members)))
                selected = self.selected_indices()
                if editor_model.has_locked(self.project.shapes, selected):
                    self.show_message(editor_model.LOCKED_MESSAGE, level='warning')
                    return
                if mode == 'Rotate':
                    rotation = rotation_handle(self.project.shapes, selected, scale)
                    if rotation:
                        self.drag = Drag('rotate', x, y, scale, selected, rotation=rotation)
                        self.canvas.set_cursor_from_name('grabbing')
                elif mode != 'Scale':
                    self.drag = Drag('move', x, y, scale, selected)
                return
        if not extend:
            self.set_selection(())
        self.drag = Drag('marquee', x, y, scale, indices if extend else ())

    def begin_point_edit(self, x, y, scale):
        position = (x, y)
        indices = self.selected_indices()
        if len(indices) == 1:
            index = indices[0]
            shape = self.project.shapes[index]
            if shape.locked:
                self.show_message(editor_model.LOCKED_MESSAGE, level='warning')
                return
            if path_edit.editable(shape):
                outline = path_edit.nodes(shape)
                shown = path_edit.adjacent_points(outline, self.active_node(shape))
                distance, kind, point, side = path_edit.nearest_node(outline, position, handle_indices=shown)
                if distance <= 7 / scale:
                    self.selected_node = (shape, point)
                    self.drag = Drag('node', x, y, scale, indices, node=(outline, kind, point, side))
                    self.refresh_tool_hint()
                    self.canvas.queue_draw()
                    return
                try:
                    added, point = path_edit.insert_point(outline, position, 5 / scale)
                    edited = path_edit.to_shape(added, shape)
                except ValueError:
                    pass
                else:
                    shapes = self.project.shapes
                    self.draft = replace(self.project, shapes=shapes[:index] + (edited,) + shapes[index + 1:])
                    self.drag = Drag('node', x, y, scale, indices, node=(added, 'point', point, None))
                    self.canvas.queue_draw()
                    return
            elif shape.visible and shape.kind in editor_model.POINT_EDIT_KINDS:
                distance, vertex = editor_model.nearest_vertex(shape, position)
                if distance <= 7 / scale:
                    self.drag = Drag('point', x, y, scale, indices, vertex=vertex)
                    return
        self.selected_node = None
        for index in reversed(range(len(self.project.shapes))):
            shape = self.project.shapes[index]
            editable = path_edit.editable(shape) or shape.kind in editor_model.POINT_EDIT_KINDS
            if shape.visible and editable and editor_model.hit(shape, position, 5 / scale):
                self.set_selection((index,))
                break
        self.refresh_tool_hint()
        self.canvas.queue_draw()

    def draw_shape(self, drag, x, y, *, final=False, shift=False, alt=False):
        start = drag.points[0]
        end = self.grid_point(x, y)
        if drag.mode in ("Freehand", "Smooth Freehand"):
            if end != drag.points[-1]:
                drag.points.append(end)
            if len(drag.points) < 2:
                return None
            if drag.mode == "Smooth Freehand":
                return path_edit.smooth_freehand(drag.points, drag.scale, final=final)
            points = tuple(drag.points)
            closed = final and len(points) > 3 and math.dist(points[0], points[-1]) <= 8 / drag.scale
            if closed:
                points = points[:-1]
            nodes = path_edit.Nodes(points, (), closed and len(set(points)) >= 3)
            return path_edit.to_shape(nodes, Shape("Path"))
        if drag.mode == "Line":
            if shift:
                end = constrain_angle(start, end)
            return Shape("Line", *start, end[0] - start[0], end[1] - start[1]) if end != start else None
        if drag.mode == "Arc":
            if min(abs(end[0] - start[0]), abs(end[1] - start[1])) < 0.1:
                return None
            return path_edit.to_shape(path_edit.arc(start, end), Shape("Path"))
        if drag.mode == "Regular Polygon":
            return path_edit.draw_regular_polygon(start, end, self.sides.get_value_as_int(), shift=shift, alt=alt)
        dx, dy = end[0] - start[0], end[1] - start[1]
        if shift:
            side = max(abs(dx), abs(dy))
            dx, dy = math.copysign(side, dx or 1), math.copysign(side, dy or 1)
        if alt:
            left, top, w, h = start[0] - abs(dx), start[1] - abs(dy), 2 * abs(dx), 2 * abs(dy)
        else:
            left, top, w, h = min(start[0], start[0] + dx), min(start[1], start[1] + dy), abs(dx), abs(dy)
        if min(w, h) < 0.1:
            return None
        radius = self.shape_radius.get_value() if drag.mode == 'Rounded rectangle' else 3
        return Shape(drag.mode, left, top, w, h, radius=radius)

    def drag_update(self, gesture, dx, dy, *, final=False):
        drag = self.drag
        if drag is None or (self.draft is None and abs(dx) + abs(dy) < 2):
            return
        x, y = drag.x + dx / drag.scale, drag.y + dy / drag.scale
        base = self.history.current
        if drag.mode == 'measure':
            self.measurement = ((drag.x, drag.y), (x, y))
            self.refresh_measurement()
        elif drag.mode == 'centre':
            self.rotation_centre = self.grid_point(x, y)
        elif drag.mode == 'knife':
            state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
            start, end = (drag.x, drag.y), (x, y)
            self.knife = (start, constrain_angle(start, end) if state & Gdk.ModifierType.SHIFT_MASK else end)
        elif drag.mode == 'erase':
            self.eraser_stroke.append((x, y))
        elif drag.mode == 'pan':
            self.canvas.pan = drag.start_pan[0] + dx, drag.start_pan[1] + dy
        elif drag.mode == 'marquee':
            self.marquee = geometry.Rect(min(drag.x, x), min(drag.y, y),
                                         abs(x - drag.x), abs(y - drag.y))
        elif drag.mode == 'node':
            index = drag.indices[0]
            outline, kind, point, side = drag.node
            anchor = outline.points[point] if kind == 'point' else outline.handles[point][side]
            target = self.grid_point(anchor[0] + dx / drag.scale, anchor[1] + dy / drag.scale)
            moved = (path_edit.move_point(outline, point, target) if kind == 'point'
                     else path_edit.move_handle(outline, point, side, target))
            if final and kind == 'point' and point in (0, len(outline.points) - 1):
                joined = path_edit.join_ends(moved, 6 / drag.scale)
                if joined is not None:
                    moved = joined
                    drag.node = (outline, kind, 0, side)
            try:
                edited = path_edit.to_shape(moved, base.shapes[index])
            except ValueError as error:
                self.draft = None
                if final:
                    self.show_message(str(error), level='warning')
            else:
                self.draft = replace(base, shapes=base.shapes[:index] + (edited,) + base.shapes[index + 1:])
        elif drag.mode == 'point':
            index = drag.indices[0]
            shape = base.shapes[index]
            contour, vertex = drag.vertex
            px, py = shape.paths()[contour].points[vertex]
            position = self.grid_point(px + dx / drag.scale, py + dy / drag.scale)
            try:
                edited = editor_model.move_vertex(shape, drag.vertex, position)
            except ValueError:
                self.draft = None
            else:
                shapes = base.shapes[:index] + (edited,) + base.shapes[index+1:]
                self.draft = replace(base, shapes=shapes)
        elif drag.mode == 'rotate':
            state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
            snap = bool(state & Gdk.ModifierType.SHIFT_MASK)
            centre = self.rotation_centre or drag.rotation.center
            if self.rotation_centre is not None:
                turn = math.degrees(math.atan2(y - centre[1], x - centre[0])
                                    - math.atan2(drag.y - centre[1], drag.x - centre[0]))
                angle = 15 * round(turn / 15) if snap else round(turn, 3)
            else:
                angle = drag.rotation.angle((drag.x, drag.y), (x, y), snap=snap)
            if angle is not None:
                try:
                    rotated = editor_model.rotate_selection(base.shapes, drag.indices, angle, center=centre)
                except ValueError:
                    self.draft, drag.angle = None, 0
                else:
                    self.draft, drag.angle = replace(base, shapes=rotated), angle
        elif drag.mode == 'scale':
            factor = scale_from_corner((drag.x, drag.y), (x, y), drag.scale_anchor)
            try:
                scaled = editor_model.scale_selection(base.shapes, drag.indices, drag.scale_anchor, factor)
            except ValueError:
                self.draft = None
            else:
                self.draft = replace(base, shapes=scaled)
        elif drag.mode == 'move':
            if drag.indices:
                anchor = base.shapes[drag.indices[0]]
                ax = anchor.x + dx / drag.scale
                ay = anchor.y + dy / drag.scale
                ax, ay = self.grid_point(ax, ay)
                mx, my = ax - anchor.x, ay - anchor.y
                if self.smart_snap.get_active():
                    mx, my = editor_model.snap_move(base.shapes, drag.indices, mx, my, base.width, base.height, 6 / drag.scale)
                shapes = editor_model.move(base.shapes, drag.indices, mx, my)
                self.draft = replace(base, shapes=shapes)
        else:
            limit = editor_model.SHAPE_LIMIT
            try:
                state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
                shape = self.draw_shape(drag, x, y, final=final,
                                        shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
                                        alt=bool(state & Gdk.ModifierType.ALT_MASK))
            except ValueError:
                shape = None
            valid = shape is not None and max(abs(shape.x), abs(shape.y), abs(shape.width),
                                              abs(shape.height)) <= limit
            self.draft = replace(base, shapes=base.shapes + (shape,)) if valid else None
        self.canvas.queue_draw()

    def drag_end(self, gesture, dx, dy):
        if self.drag is None:
            return
        if dx or dy or self.drag.mode in ('rotate', 'scale', 'Freehand', 'Smooth Freehand', 'knife', 'erase'):
            self.drag_update(gesture, dx, dy, final=True)
        if self.drag.mode == 'knife':
            line = self.knife
            self.cancel_drag()
            if line:
                self.knife_slice(*line)
            return
        if self.drag.mode == 'erase':
            stroke = list(self.eraser_stroke)
            self.cancel_drag()
            self.erase_stroke(stroke)
            return
        if self.drag.mode == 'centre':
            self.cancel_drag()
            self.canvas.queue_draw()
            return
        indices = self.selected_indices()
        if self.marquee is not None:
            region = self.marquee
            selected = set(self.drag.indices)
            for i, shape in enumerate(self.project.shapes):
                if shape.visible:
                    box = editor_model.bounds((shape,))
                    if region.x <= box.x <= box.x2 <= region.x2 and region.y <= box.y <= box.y2 <= region.y2:
                        selected.update(editor_model.group_members(self.project.shapes, i))
            indices = tuple(sorted(selected))
        if self.draft is not None:
            if self.drawing:
                indices = (len(self.draft.shapes) - 1,)
            if self.drag.node:
                index = self.drag.indices[0]
                self.selected_node = (self.draft.shapes[index], self.drag.node[2])
            self.history.commit(self.draft)
            self.cancel_drag()
            self.refresh_objects(indices)
        else:
            self.cancel_drag()
            self.set_selection(indices)

    def cancel_drag(self, *_args):
        if self.drag and self.drag.mode in ('rotate', 'scale', 'centre'):
            self.refresh_cursor()
        self.drag = self.draft = self.marquee = self.knife = None
        self.eraser_stroke = []
        self.drawing = False
        self.select_shape()

    def active_node(self, shape):
        if self.selected_node and self.selected_node[0] is shape:
            return self.selected_node[1]
        return None

    def point_context(self, _gesture, _count, x, y):
        if self.active_tool != 'Point Edit':
            return
        indices = self.selected_indices()
        if len(indices) != 1:
            return
        shape = self.project.shapes[indices[0]]
        if not path_edit.editable(shape):
            return
        left, top, scale = self.canvas.viewport(self.canvas.get_width(), self.canvas.get_height())
        point = ((x - left) / scale, (y - top) / scale)
        outline = path_edit.nodes(shape)
        distance, _, index, _ = path_edit.nearest_node(outline, point, handle_indices=())
        if distance > 7 / scale:
            return
        self.selected_node = (shape, index)
        self.refresh_tool_hint()
        self.canvas.queue_draw()
        anchor = Gdk.Rectangle()
        anchor.x, anchor.y, anchor.width, anchor.height = int(x), int(y), 1, 1
        menu = Gtk.PopoverMenu.new_from_model(self.point_menu_items)
        menu.set_parent(self.canvas)
        menu.set_pointing_to(anchor)
        menu.connect('closed', lambda widget: widget.unparent())
        menu.popup()

    def point_action(self, action):
        if self.active_tool != 'Point Edit' or self.drag is not None:
            return
        indices = self.selected_indices()
        if len(indices) != 1 or self.refuse_locked(indices):
            return
        index = indices[0]
        shapes = self.project.shapes
        shape = shapes[index]
        if not path_edit.editable(shape):
            return
        if action == 'Simplify':
            self.convert_selection(True)
            return
        point = self.active_node(shape)
        if point is None:
            return
        try:
            pieces, chosen = path_edit.edit_point(shape, path_edit.nodes(shape), point, action)
        except ValueError as error:
            self.show_message(str(error), level='warning')
            return
        self.selected_node = (pieces[0], chosen)
        self.commit(shapes[:index] + pieces + shapes[index + 1:], indices)

    def point_key(self, indices, key, arrows):
        cycle = key in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright)
        delete = key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace)
        if len(indices) != 1 or not (cycle or delete or key in arrows):
            return False
        index = indices[0]
        shapes = self.project.shapes
        shape = shapes[index]
        if not path_edit.editable(shape):
            return False
        outline = path_edit.nodes(shape)
        current = self.active_node(shape)
        if cycle:
            direction = 1 if key == Gdk.KEY_bracketright else -1
            current = path_edit.step_point(current, len(outline.points), direction)
            self.selected_node = (shape, current)
        elif current is None:
            return not delete
        else:
            if self.refuse_locked((index,)):
                return True
            try:
                if delete:
                    outline = path_edit.delete_point(outline, current)
                    edited = path_edit.to_shape(outline, shape)
                    current = max(0, current - 1)
                else:
                    dx, dy = arrows[key]
                    step = self.grid_spacing.get_value() if self.snap.get_active() else 1
                    edited = path_edit.nudge_point(shape, outline, current, dx * step, dy * step)
            except ValueError as error:
                self.show_message(str(error), level='warning')
                return True
            self.selected_node = (edited, current)
            self.commit(shapes[:index] + (edited,) + shapes[index + 1:], indices)
            outline = path_edit.nodes(edited)
        self.canvas.reveal_point(outline.points[current])
        self.refresh_tool_hint()
        self.canvas.queue_draw()
        return True

    def canvas_key(self, _controller, key, _code, state):
        if key == Gdk.KEY_space and not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)):
            if self.drag is None:
                self.space_pan = True
                self.refresh_cursor()
            return True
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        indices = self.selected_indices()
        arrows = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
                  Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1)}
        if key == Gdk.KEY_Escape:
            in_progress = bool(self.drag is not None or self.polygon)
            self.cancel_drag()
            self.polygon.clear()
            if self.measurement is not None:
                self.measurement = None
                self.refresh_measurement()
                self.canvas.queue_draw()
            elif in_progress:
                self.canvas.queue_draw()
            else:
                self.set_selection(())
                self.tool.set_selected(0)
        elif self.drag is not None and self.drag.mode in ('rotate', 'scale', 'node'):
            return True
        elif self.active_tool == 'Point Edit' and not (state & (
                Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK
        )) and self.point_key(indices, key, arrows):
            return True
        elif self.active_tool in ('Polygon', 'Curve') and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish_polygon(closed=False)
        elif not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) and Gdk.keyval_to_upper(key) == Gdk.KEY_O:
            self.set_rotation_centre(self.rotation_centre is None)
        elif not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) and (
                tool_index := TOOL_KEYS.get(Gdk.keyval_to_upper(key))) is not None:
            self.tool.set_selected(tool_index)
        elif ctrl and not state & Gdk.ModifierType.ALT_MASK and key in (
                Gdk.KEY_x, Gdk.KEY_c, Gdk.KEY_v, Gdk.KEY_V):
            if key in (Gdk.KEY_x, Gdk.KEY_c):
                self.copy(cut=key == Gdk.KEY_x)
            else:
                self.paste(in_front=bool(state & Gdk.ModifierType.SHIFT_MASK))
        elif ctrl and key == Gdk.KEY_a:
            self.set_selection(i for i, shape in enumerate(self.project.shapes) if shape.visible)
        elif ctrl and key in (Gdk.KEY_g, Gdk.KEY_G):
            self.group_selected(release=bool(state & Gdk.ModifierType.SHIFT_MASK))
        elif ctrl and key == Gdk.KEY_d:
            self.duplicate_shape(None)
        elif ctrl and key == Gdk.KEY_z:
            self.change_history(self.history.undo)
        elif ctrl and key == Gdk.KEY_y:
            self.change_history(self.history.redo)
        elif key == Gdk.KEY_F2 and len(indices) == 1:
            self.rename_object()
        elif key in (Gdk.KEY_BackSpace, Gdk.KEY_Delete) and indices:
            self.delete_shape(None)
        elif key in arrows and indices:
            if self.refuse_locked(indices):
                return True
            distance = self.grid_spacing.get_value() if self.snap.get_active() else 1
            dx, dy = arrows[key]
            self.commit(editor_model.move(self.project.shapes, indices, dx * distance, dy * distance), indices)
        else:
            return False
        return True

    def confirm_discard(self, continuation):
        if not self.dirty:
            continuation()
            return
        alert = Adw.AlertDialog(heading='Save changes to this design?')
        alert.add_response('cancel', 'Cancel')
        alert.add_response('discard', 'Discard')
        alert.add_response('save', 'Save')
        alert.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response('save')
        alert.set_close_response('cancel')

        def respond(_dialog, response):
            if response == 'save':
                self.save(None, continuation=continuation)
            elif response == 'discard':
                continuation()

        alert.connect('response', respond)
        alert.present(self.parent)

    def file_dialog(self, title, callback, *, filters=(), save_name=None):
        dialog = Gtk.FileDialog(title=title)
        if filters:
            file_filter = Gtk.FileFilter()
            for pattern in filters:
                file_filter.add_pattern(pattern)
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(file_filter)
            dialog.set_filters(store)
        if save_name:
            dialog.set_initial_name(save_name)

        def finish(d, result):
            try:
                file = (d.save_finish(result) if save_name else d.open_finish(result))
                filename = file.get_path()
                if filename is None:
                    raise ValueError('Choose a local file.')
                callback(Path(filename))
            except GLib.Error:
                return
            except (OSError, ValueError) as error:
                self.show_message(str(error), level='warning')

        (dialog.save if save_name else dialog.open)(self.parent, None, finish)

    def load_vector(self, path, opened, page=None):
        try:
            drawing = importer.load(path, page=page)
            opened(drawing)
        except importer.InkscapePageRequired as error:
            choose_inkscape_page(self.parent, path, error.count,
                                 lambda page: self.load_vector(path, opened, page))
        except (OSError, ValueError) as error:
            self.show_message(str(error), level='warning')

    def open_project(self, _button):
        def opened(path):
            native_project = path.suffix.lower() == '.scc-template'
            def replace_project(project, details=''):
                def confirmed():
                    self.replace_document(project, path if native_project else None, path.name)
                    notice = design_text.unavailable_fonts(project) or details
                    if notice:
                        self.show_message(notice)
                self.confirm_discard(confirmed)

            def vector(drawing):
                shapes, details = editor_model.editable_import(drawing)
                size = drawing.page_size_mm or (self.project.width, self.project.height)
                if not all(1 <= mm <= editor_model.SHAPE_LIMIT for mm in size):
                    raise ValueError('Design supports page dimensions from 1 to 1,000 mm.')
                replace_project(editor_model.Project(*size, shapes, mat=self.project.mat), details)

            if native_project:
                replace_project(load_project(path, legacy_registration=self.project.registration))
            else:
                self.load_vector(path, vector)

        self.file_dialog('Open Design', opened, filters=('*.scc-template', '*.svg', '*.SVG',
                         '*.dxf', '*.DXF', '*.studio3', '*.STUDIO3'))

    def replace_document(self, project, path=None, source=None):
        self.cancel_drag()
        self.measurement = None
        self.rotation_centre = None
        self.collapsed_groups.clear()
        self.polygon.clear()
        self.notice_bar.set_visible(False)
        self.notice_label.set_text('')
        self.history = History(project)
        self.saved = project
        self.project_path, self.source_name = path, source
        self.custom_page = False
        if project.registration is not None:
            self.last_registration = project.registration
        self.fit(None)
        self.refresh_objects((0,) if project.shapes else ())
        self.open_panel(1 if project.shapes else 0)

    def new_project(self, _button):
        self.confirm_discard(lambda: self.replace_document(replace(self.project, shapes=())))

    def save(self, _button, *, continuation=None):
        if self.project_path is None:
            self.save_as(None, continuation=continuation)
        else:
            self.write_design(self.project_path, continuation)

    def save_as(self, _button, *, continuation=None):
        name = self.project_path.name if self.project_path else (
            Path(self.source_name).with_suffix('.scc-template').name if self.source_name else 'design.scc-template')
        self.file_dialog('Save design project', lambda path: self.write_design(path, continuation),
                         save_name=name)

    def write_design(self, path, continuation):
        if path.suffix.lower() != '.scc-template':
            path = path.with_name(path.name + '.scc-template')
            if path.exists():
                self.show_message(f'Choose the full name {path.name} to confirm replacement.', level='warning')
                return
        try:
            save_project(path, self.project)
        except (OSError, ValueError) as error:
            self.show_message(f'Could not save {path.name}. {error}', level='warning')
            return
        self.project_path = path
        self.saved = self.project
        self.refresh_status()
        self.show_message(f'Saved {path.name}')
        if continuation:
            continuation()

    def import_vector(self, _button):
        def opened(drawing):
            shapes, details = editor_model.editable_import(drawing)
            start = len(self.project.shapes)
            self.commit(self.project.shapes + shapes, range(start, start + len(shapes)))
            self.show_message(details)

        self.file_dialog('Import Artwork', lambda path: self.load_vector(path, opened), filters=('*.svg','*.dxf','*.SVG','*.DXF','*.studio3','*.STUDIO3'))

