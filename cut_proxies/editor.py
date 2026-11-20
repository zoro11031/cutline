"""Full-window GTK design workspace and shape inspector."""

from dataclasses import dataclass, replace
import math
from pathlib import Path
import threading

import cairo
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import design_text, editor_model, geometry, graphtec, importer, regmarks, sticker_assets, sticker_print, sticker_trace, template
from .design_tools import COMMAND_ICONS, TOOL_GROUPS, TOOL_KEYS, TOOL_NAMES
from .editor_canvas import DesignCanvas
from .editor_model import (ACTIONS, ALIGNMENTS,
                           History, Registration, Shape,
                           import_shapes, load_project, save_project)
from .gtk_controls import (disclosure, menu_item, protect_settings_wheel, workspace_header, workspace_tools,
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


def panel(width):
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                   margin_top=14, margin_bottom=14, margin_start=12, margin_end=12)
    scroll.set_size_request(width, -1)
    scroll.set_child(body)
    return scroll, body


def draw_tool_icon(area, context, width, height, tool):
    color = area.get_style_context().get_color()
    context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
    context.translate((width - 24) / 2, (height - 24) / 2)
    context.set_line_width(1.7)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    for first, *rest in tool.strokes:
        context.move_to(*first)
        for point in rest:
            context.line_to(*point)
    context.stroke()


def design_icon_button(tool, *, toggle=False):
    button_type = Gtk.ToggleButton if toggle else Gtk.Button
    button = button_type(css_classes=["image-button", "flat"])
    button.set_size_request(34, 34)
    button.set_tooltip_text(tool.tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                           [tool.name, tool.hint])
    if tool.shortcut:
        button.update_property([Gtk.AccessibleProperty.KEY_SHORTCUTS], [tool.shortcut])
    icon = Gtk.DrawingArea(content_width=22, content_height=22)
    icon.set_draw_func(draw_tool_icon, tool)
    button.set_child(icon)
    return button


class GtkToolStrip(Gtk.Box):
    """One column of exclusive canvas modes."""

    def __init__(self, *, changed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         margin_start=6, margin_end=6)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Drawing tools"])
        self.buttons = []
        self._selected = 0
        self._changed = changed
        for tools in TOOL_GROUPS:
            if self.buttons:
                self.append(Gtk.Separator())
            for tool in tools:
                button = design_icon_button(tool, toggle=True)
                if self.buttons:
                    button.set_group(self.buttons[0])
                else:
                    button.set_active(True)
                button.connect("toggled", self._on_toggled, len(self.buttons))
                self.append(button)
                self.buttons.append(button)
        self.active_label = Gtk.Label(label=TOOL_NAMES[0], xalign=0, css_classes=["caption"])

    def _on_toggled(self, button, index):
        if button.get_active():
            self._selected = index
            self.active_label.set_label(TOOL_NAMES[index])
            self._changed()

    def get_selected(self) -> int:
        return self._selected

    def set_selected(self, index: int):
        if 0 <= index < len(self.buttons):
            self.buttons[index].set_active(True)


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
        self.drag = None
        self.draft = None
        self.marquee = None
        self.drawing = False
        self.custom_page = False
        self.polygon = []
        self.cutter = cutter or graphtec.device_by_name("Silhouette Cameo 4")
        self.last_registration = project.registration or Registration.from_marks(
            regmarks.marks(project.width, project.height))

        header, self.design_workspace, self.send_workspace = workspace_header(
            lambda *_: self.canvas.grab_focus(), open_send, active="Design")
        self.view.add_top_bar(header)
        self.shortcuts = shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        self.view.add_controller(shortcuts)

        toolbar = workspace_tools(margin_start=12, margin_end=12, margin_bottom=8)
        self.view.add_top_bar(toolbar)
        history_tools = Gtk.Box(spacing=2)
        self.save_button = self.icon_button(history_tools, 'save', self.save)
        self.undo_button = self.icon_button(history_tools, 'undo', lambda *_: self.change_history(self.history.undo))
        self.redo_button = self.icon_button(history_tools, 'redo', lambda *_: self.change_history(self.history.redo))
        toolbar.append(history_tools)
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6))
        artwork = Gtk.Box(spacing=2)
        self.add_button = self.icon_button(artwork, 'shape', self.add_shape)
        self.text_button = self.icon_button(artwork, 'text', lambda *_: self.text_dialog())
        self.icon_button(artwork, 'image', lambda *_: self.place_sticker())
        self.trace_button = self.icon_button(artwork, 'trace', lambda *_: self.trace_sticker())
        toolbar.append(artwork)
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6))
        self.document_title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
                                        css_classes=["heading"])
        toolbar.append(self.document_title)
        fit_button = design_icon_button(COMMAND_ICONS['fit'])
        fit_button.connect('clicked', self.fit)
        zoom, self.zoom_readout = zoom_controls(lambda factor: self.canvas.zoom_by(factor),
                                               lambda: self.fit(None), fit_button=fit_button)
        toolbar.append(zoom)

        self.tools = TOOL_NAMES
        self.tool = GtkToolStrip(changed=self.tool_changed)
        self.exporting_stickers = False

        self.canvas = DesignCanvas(self)
        self.canvas.set_cursor_from_name('default')
        properties = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, height_request=125)
        self.inspector = Gtk.Stack(vexpand=True)
        panels = (('Page Setup', self.page_panel), ('Transform', self.transform_panel),
                  ('Replicate', self.replicate_panel), ('Line and Fill', self.style_panel))
        for label, build in panels:
            scroll, content = panel(244)
            build(content)
            self.inspector.add_named(scroll, label)
        self.panel_switcher = switcher = Gtk.DropDown.new_from_strings([label for label, _ in panels])
        switcher.set_margin_start(12)
        switcher.set_margin_end(12)
        switcher.set_margin_top(8)
        switcher.connect('notify::selected', lambda widget, *_: self.inspector.set_visible_child_name(
            panels[widget.get_selected()][0]))
        properties.append(switcher)
        properties.append(self.inspector)
        dock = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True, position=340, width_request=252,
                         resize_start_child=True, resize_end_child=True,
                         shrink_start_child=False, shrink_end_child=False)
        dock.set_start_child(properties)
        dock.set_end_child(self.objects_panel())
        workspace_panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                                    resize_start_child=True, resize_end_child=False,
                                    shrink_start_child=False, shrink_end_child=False, hexpand=True)
        workspace_panes.set_start_child(self.canvas)
        workspace_panes.set_end_child(dock)
        workspace = Gtk.Box()
        workspace.append(self.tool)
        workspace.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        workspace.append(workspace_panes)
        self.view.set_content(workspace)

        footer = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, margin_bottom=8)
        footer.append(self.tool.active_label)
        footer.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        footer.append(self.status)
        # As in Silhouette Studio, the Send switch sends the design; only a
        # sticker sheet has a step of its own, its Print & Cut export.
        self.review = self.button(footer, 'Export Print & Cut…', lambda *_: self.export_stickers())
        self.review.add_css_class('suggested-action')
        self.view.add_bottom_bar(footer)
        self.menu_bar = self.build_design_menus(shortcuts)
        header.pack_start(self.menu_bar)
        protect_settings_wheel(self.view)
        self.view.add_css_class('scc-workspace')
        self.refresh_objects((0,))
        self.tool_changed()

    @staticmethod
    def icon_button(parent, icon, callback):
        button = design_icon_button(COMMAND_ICONS[icon])
        button.connect('clicked', callback)
        parent.append(button)
        return button

    def objects_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, height_request=110,
                        margin_start=12, margin_end=12, margin_bottom=6)
        header = Gtk.Box(spacing=2)
        header.append(Gtk.Label(label='Objects', xalign=0, hexpand=True, css_classes=['heading']))
        self.duplicate = self.icon_button(header, 'duplicate', self.duplicate_shape)
        self.remove = self.icon_button(header, 'delete', self.delete_shape)
        options = Gtk.MenuButton(icon_name='view-more-symbolic', tooltip_text='Object options',
                                 css_classes=['flat'])
        options.update_property([Gtk.AccessibleProperty.LABEL], ['Object options'])
        menu = Gtk.Popover()
        more_objects = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for field, label, callback in (
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
        self.objects = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE, css_classes=['boxed-list'])
        self.objects.update_property([Gtk.AccessibleProperty.LABEL], ['Object list'])
        self.objects.set_placeholder(Gtk.Label(label='No objects yet', margin_top=14, margin_bottom=14,
                                               css_classes=['dim-label']))
        self.objects.connect('selected-rows-changed', self.select_shape)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.objects)
        panel.append(scroller)
        return panel

    @staticmethod
    def spin(parent, label, low, high, value, step=0.1, digits=2):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label, xalign=0, hexpand=True, wrap=True))
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_value(value)
        spin.set_digits(digits)
        spin.set_numeric(True)
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
        self.show_grid = self.view_toggle(inspector, 'Show Grid', True)
        self.show_mat = self.view_toggle(inspector, 'Show Cutting Mat', True)
        self.show_cut_border = self.view_toggle(inspector, 'Show Cut Border', True)
        self.print_border = self.view_toggle(inspector, 'Show Print Border')
        self.print_margin = self.spin(inspector, 'Print margin (mm)', 0, 25, 5, step=0.5)
        self.print_margin.set_tooltip_text('Set the margin you use on your printer.')
        self.print_margin.connect('value-changed', lambda *_: self.canvas.queue_draw())
        self.show_rulers = self.view_toggle(inspector, 'Show Rulers')
        self.snap = self.view_toggle(inspector, 'Snap to Grid')
        self.smart_snap = self.view_toggle(inspector, 'Enable Smart Snapping')
        self.smart_snap.set_tooltip_text('Line up moving shapes with nearby edges or centers.')
        self.grid_spacing = self.spin(inspector, 'Spacing (mm)', 0.5, 100, 5, step=0.5)
        self.grid_spacing.connect('value-changed', lambda *_: self.canvas.queue_draw())

    def view_toggle(self, parent, label, active=False):
        toggle = Gtk.CheckButton(label=label, active=active)
        toggle.connect('toggled', lambda *_: self.canvas.queue_draw())
        parent.append(toggle)
        return toggle

    def transform_panel(self, inspector):
        self.heading(inspector, 'Position and Size')
        fields = (
            ('x', 'X (mm)', -1000, 1000), ('y', 'Y (mm)', -1000, 1000),
            ('width', 'Width (mm)', -1000, 1000), ('height', 'Height (mm)', -1000, 1000),
            ('rotation', 'Rotation (°)', -360, 360), ('radius', 'Radius (mm)', 0, 1000),
        )
        self.fields = {}
        for field, label, low, high in fields[:-1]:
            spin = self.spin(inspector, label, low, high, 0)
            spin.connect('value-changed', self.change_field, field)
            self.fields[field] = spin
        self.lock_aspect = Gtk.CheckButton(label='Lock Aspect Ratio')
        inspector.append(self.lock_aspect)
        self.flips = {}
        for field, label in (('flip_x', 'Mirror Horizontally'), ('flip_y', 'Mirror Vertically'),
                             ('visible', 'Visible')):
            toggle = Gtk.CheckButton(label=label)
            toggle.connect('toggled', self.change_toggle, field)
            inspector.append(toggle)
            self.flips[field] = toggle
        self.heading(inspector, 'Align')
        self.alignment = Gtk.DropDown.new_from_strings(ALIGNMENTS)
        inspector.append(self.alignment)
        self.align_button = self.button(inspector, 'Apply Alignment', self.align_selection)
        advanced, _ = disclosure(inspector)
        self.fields['radius'] = self.spin(advanced, 'Radius (mm)', 0, 1000, 0)
        self.fields['radius'].connect('value-changed', self.change_field, 'radius')

    def build_design_menus(self, shortcuts):
        self.menu_actions = Gio.SimpleActionGroup()
        self.view.insert_action_group('design', self.menu_actions)

        def command(menu, label, name, callback, shortcut=None, field=None):
            if field is None:
                action = Gio.SimpleAction.new(name, None)
            else:
                action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(field.get_active()))
                field.connect('toggled', lambda widget: action.set_state(GLib.Variant.new_boolean(widget.get_active())))
            action.connect('activate', lambda *_: callback())
            self.menu_actions.add_action(action)
            full_name = f'design.{name}'
            menu.append_item(menu_item(label, full_name, shortcut))
            if shortcut:
                shortcuts.add_shortcut(Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(shortcut), Gtk.NamedAction.new(full_name)))

        files = Gio.Menu()
        for label, name, shortcut, callback in (
                ('New', 'new', '<Control>n', self.new_project),
                ('Open…', 'open', '<Control>o', self.open_project),
                ('Save', 'save', '<Control>s', self.save),
                ('Save As…', 'save-as', '<Control><Shift>s', self.save_as),
                ('Import Artwork…', 'import-artwork', None, self.import_vector)):
            command(files, label, name, lambda handler=callback: handler(None), shortcut)
        command(files, 'Place Image…', 'place-sticker', self.place_sticker)
        command(files, 'Export Print & Cut ZIP…', 'export-stickers', self.export_stickers)
        edit = Gio.Menu()
        command(edit, 'Undo', 'undo', lambda: self.change_history(self.history.undo))
        command(edit, 'Redo', 'redo', lambda: self.change_history(self.history.redo))
        command(edit, 'Select All', 'select-all', lambda: self.set_selection(
            i for i, shape in enumerate(self.project.shapes) if shape.visible))
        command(edit, 'Duplicate', 'duplicate', lambda: self.duplicate_shape(None))
        command(edit, 'Delete', 'delete', lambda: self.delete_shape(None))

        view = Gio.Menu()
        zoom = Gio.Menu()
        view.append_submenu('Zoom', zoom)
        command(zoom, 'Zoom In', 'zoom-in', lambda: self.canvas.zoom_by(1.25), '<Control>equal|<Control>plus')
        command(zoom, 'Zoom Out', 'zoom-out', lambda: self.canvas.zoom_by(0.8), '<Control>minus')
        command(zoom, 'Fit', 'fit', lambda: self.fit(None), '<Control>0')
        rotate = Gio.Menu()
        view.append_submenu('Rotate Page', rotate)
        command(rotate, 'Left', 'rotate-page-left', lambda: self.rotate_page(False))
        command(rotate, 'Right', 'rotate-page-right', lambda: self.rotate_page(True))
        for label, name, field in (
                ('Show Grid', 'grid', self.show_grid), ('Snap to Grid', 'snap', self.snap),
                ('Enable Smart Snapping', 'smart-snap', self.smart_snap),
                ('Show Print Border', 'print-border', self.print_border),
                ('Show Cut Border', 'cut-border', self.show_cut_border)):
            command(view, label, name, lambda toggle=field: toggle.set_active(not toggle.get_active()), field=field)
        command(view, 'Grid Options…', 'grid-options', lambda: self.show_page_options(self.show_grid))
        command(view, 'Registration Marks…', 'registration', lambda: self.show_page_options(self.mark_pattern))
        command(view, 'Show Design Page Settings…', 'page-options', lambda: self.panel_switcher.set_selected(0),
                '<Control>F1')
        command(view, 'Switch to Send', 'send', lambda: self.send_workspace.emit('clicked'), '<Control>F2')

        panels = Gio.Menu()
        for label, name, index in (('Page Setup', 'page-setup', 0), ('Fill Color', 'fill', 3),
                                   ('Line Style', 'line', 3), ('Transform', 'transform', 1), ('Replicate', 'replicate', 2)):
            command(panels, label, name, lambda panel=index: self.panel_switcher.set_selected(panel))
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
            command(align, label, f'align-{index}', lambda option=index: self.align_objects(option))
        command(objects, 'Replicate…', 'object-replicate', lambda: self.panel_switcher.set_selected(2))
        command(objects, 'Group', 'group', self.group_selected)
        command(objects, 'Ungroup', 'ungroup', lambda: self.group_selected(release=True))
        holders = Gio.Menu()
        objects.append_submenu('Tool Holder', holders)
        for label, slot in (('Use Current Settings', None), ('Tool Holder 1', 1), ('Tool Holder 2', 2)):
            command(holders, label, f'holder-{slot}', lambda holder=slot: self.change_properties(holder=holder))
        self.menu_actions.lookup_action('holder-2').set_enabled(self.cutter.two_tools)
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
        self.panel_switcher.set_selected(0)
        self.page_advanced.set_expanded(True)

        def reveal():
            placed, bounds = field.compute_bounds(self.page_options)
            if placed:
                scroll = self.inspector.get_child_by_name('Page Setup')
                scroll.get_vadjustment().set_value(max(0, bounds.get_y() - 12))
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def rotate_page(self, clockwise):
        try:
            project = editor_model.rotate_page(self.project, clockwise)
        except ValueError as error:
            self.status.set_text(str(error))
            return
        self.history.commit(project)
        self.refresh_objects(self.selected_indices())

    def transform_objects(self, operation):
        selected = self.selected_indices()
        try:
            shapes = editor_model.transform_selection(self.project.shapes, selected, operation)
        except ValueError as error:
            self.status.set_text(str(error))
            return
        self.commit(shapes, selected)

    def align_objects(self, option):
        self.alignment.set_selected(option)
        self.align_selection(None)

    def select_color(self, field):
        self.set_selection(editor_model.matching_color(self.project.shapes, self.selected_indices(), field))

    def convert_selection(self, simplify=False):
        selected = self.selected_indices()
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
        try:
            shapes, selected = editor_model.modify_selection(self.project.shapes, self.selected_indices(), operation)
        except ValueError as error:
            self.status.set_text(str(error))
        else:
            self.commit(shapes, selected)

    def text_dialog(self):
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
        fonts.set_selected(families.index(family))
        fonts.update_property([Gtk.AccessibleProperty.LABEL], ['Font'])
        form.append(fonts)
        size = self.spin(form, 'Size (mm)', 1, 100, spec.size_mm if spec else 10)
        status = Gtk.Label(label=notice, xalign=0, wrap=True)
        fonts.connect('notify::selected', lambda *_: status.set_text(''))
        form.append(status)
        protect_settings_wheel(form)

        def apply(_button):
            buffer = value.get_buffer()
            text = buffer.get_text(*buffer.get_bounds(), False)
            updated = editor_model.TextSpec(text, families[fonts.get_selected()], size.get_value())
            try:
                shapes, selected = design_text.edit(self.project, updated, selection[0] if spec else None)
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

    def export_stickers(self):
        if self.exporting_stickers:
            return

        def chosen(path):
            target = sticker_print.destination(path)
            project, cutter = self.project, self.cutter.name
            self.exporting_stickers = True
            self.status.set_text('Exporting Print & Cut…')

            def done(message):
                self.exporting_stickers = False
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

        self.file_dialog('Export Print & Cut', chosen, filters=('*.zip',), save_name='stickers.zip')

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
        return tuple(sorted(row.get_index() for row in self.objects.get_selected_rows()))

    def set_selection(self, indices):
        previous, self.loading = self.loading, True
        self.objects.unselect_all()
        for index in indices:
            row = self.objects.get_row_at_index(index)
            if row is not None:
                self.objects.select_row(row)
        self.loading = previous
        self.select_shape()

    def refresh_objects(self, selection=()):
        self.loading = True
        self.refresh_page()
        while (row := self.objects.get_row_at_index(0)) is not None:
            self.objects.remove(row)
        for i, obj in enumerate(self.project.shapes, 1):
            tooltip = obj.action if obj.group is None else f'{obj.action} · Group {obj.group}'
            tool = f' · Tool {obj.holder}' if obj.holder else ''
            name = f'{i}. {editor_model.object_name(obj)}{tool}'
            label = Gtk.Label(label=name, xalign=0, tooltip_text=f'{name}\n{tooltip}',
                              ellipsize=Pango.EllipsizeMode.END,
                              margin_top=2, margin_bottom=2, margin_start=8, margin_end=8)
            self.objects.append(label)
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
        selection = len(self.selected_indices())
        self.status.set_text(f'{p.width:g} × {p.height:g} mm · ' + ('1 path' if count == 1 else f'{count} paths')
                             + (f' · {selection} selected' if selection else ''))
        name = self.project_path.name if self.project_path else self.source_name or "Untitled"
        self.document_title.set_text(name + (" *" if self.dirty else ""))
        self.document_title.set_tooltip_text(f'{name} (unsaved changes)' if self.dirty else name)
        self.save_button.set_sensitive(self.dirty or self.project_path is None)
        self.undo_button.set_sensitive(bool(self.history.past))
        self.redo_button.set_sensitive(bool(self.history.future))
        self.menu_actions.lookup_action('undo').set_enabled(bool(self.history.past))
        self.menu_actions.lookup_action('redo').set_enabled(bool(self.history.future))
        self.review.set_visible(any(shape.visible and shape.sticker for shape in p.shapes))
        self.canvas.queue_draw()

    def select_shape(self, *_args):
        if self.loading:
            return
        indices = self.selected_indices()
        shapes = self.project.shapes
        obj = shapes[indices[0]] if len(indices) == 1 else None
        text_label = 'Edit Text…' if obj and obj.text else 'Add Text…'
        self.text_button.set_tooltip_text(text_label)
        hint = 'Edit the selected text.' if obj and obj.text else COMMAND_ICONS['text'].hint
        self.text_button.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                         [text_label, hint])
        can_trace = bool(obj and obj.sticker)
        self.trace_button.set_sensitive(can_trace)
        self.menu_actions.lookup_action('trace-sticker').set_enabled(can_trace)
        self.loading = True
        for field, spin in self.fields.items():
            enabled = obj is not None and (field != 'radius' or obj.kind == 'Rounded rectangle')
            spin.set_sensitive(enabled)
            if obj is not None:
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
        self.duplicate.set_sensitive(bool(indices))
        self.remove.set_sensitive(bool(indices))
        self.menu_actions.lookup_action('duplicate').set_enabled(bool(indices))
        self.menu_actions.lookup_action('delete').set_enabled(bool(indices))
        self.replicate_button.set_sensitive(bool(indices))
        self.group_button.set_sensitive(len(indices) >= 2 and
                                        (shapes[indices[0]].group is None or
                                         len({shapes[i].group for i in indices}) > 1))
        self.ungroup_button.set_sensitive(any(shapes[i].group is not None for i in indices))
        self.align_button.set_sensitive(len(editor_model.selected_units(shapes, indices)) >= 2)
        self.forward.set_sensitive(bool(indices) and indices[-1] + 1 < len(shapes))
        self.backward.set_sensitive(bool(indices) and indices[0] > 0)
        self.loading = False
        self.refresh_status()

    def commit(self, shapes, selection):
        self.history.commit(replace(self.history.current, shapes=tuple(shapes)))
        self.refresh_objects(selection)

    def change_history(self, callback):
        indices = self.selected_indices()
        self.cancel_drag()
        callback()
        self.refresh_objects(indices)

    def change_properties(self, **values):
        indices = self.selected_indices()
        if self.loading or not indices:
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

    def delete_shape(self, _button):
        shapes, indices = self.project.shapes, self.selected_indices()
        self.commit((shape for i, shape in enumerate(shapes) if i not in indices), ())

    def reorder(self, step):
        shapes, indices = list(self.project.shapes), self.selected_indices()
        if not indices or indices[0] + step < 0 or indices[-1] + step >= len(shapes):
            return
        for i in (reversed(indices) if step > 0 else indices):
            shapes[i], shapes[i+step] = shapes[i+step], shapes[i]
        self.commit(shapes, (i + step for i in indices))

    def align_selection(self, _button):
        indices = self.selected_indices()
        operation = ALIGNMENTS[self.alignment.get_selected()]
        try:
            self.commit(editor_model.align(self.project.shapes, indices, operation), indices)
        except ValueError as error:
            self.status.set_text(str(error))

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
        self.polygon.clear()
        self.cancel_drag()
        mode = self.active_tool
        self.canvas.set_cursor_from_name('crosshair' if mode not in ('Select', 'Point Edit') else 'default')

    def pick_polygon_point(self, _gesture, presses, x, y):
        if self.active_tool != 'Polygon':
            return
        self.canvas.grab_focus()
        left, top, scale = self.canvas.viewport(self.canvas.get_width(), self.canvas.get_height())
        point = self.grid_point((x-left)/scale, (y-top)/scale)
        if max(map(abs, point)) > editor_model.SHAPE_LIMIT:
            return
        if len(self.polygon) >= 3 and math.dist(point, self.polygon[0]) <= 7 / scale:
            self.finish_polygon()
            return
        if not self.polygon or self.polygon[-1] != point:
            self.polygon.append(point)
        if presses >= 2:
            self.finish_polygon()
        self.canvas.queue_draw()

    def finish_polygon(self):
        if len(set(self.polygon)) < 3:
            self.status.set_text('Place at least three points, then press Enter to close the polygon.')
            return
        try:
            path = template.CutPath(tuple(self.polygon), closed=True)
            shape = editor_model.shape_from_path(path)
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
        mode = self.active_tool
        if mode == 'Polygon':
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if mode == 'Point Edit':
            self.begin_point_edit(x, y, scale)
            return
        if mode != "Select":
            if not (-editor_model.SHAPE_LIMIT <= x <= editor_model.SHAPE_LIMIT
                    and -editor_model.SHAPE_LIMIT <= y <= editor_model.SHAPE_LIMIT):
                return
            start = self.grid_point(x, y)
            self.drawing = True
            self.drag = Drag(mode, x, y, scale, points=[start])
            return

        extend = bool(gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        indices = self.selected_indices()
        for index in reversed(range(len(self.project.shapes))):
            shape = self.project.shapes[index]
            if shape.visible and editor_model.hit(shape, (x, y), 5 / scale):
                members = set(editor_model.group_members(self.project.shapes, index))
                if extend:
                    chosen = set(indices)
                    self.set_selection(sorted(chosen - members if members <= chosen else chosen | members))
                elif not members <= set(indices):
                    self.set_selection(tuple(sorted(members)))
                self.drag = Drag('move', x, y, scale, self.selected_indices())
                return
        if not extend:
            self.set_selection(())
        self.drag = Drag('marquee', x, y, scale, indices if extend else ())

    def begin_point_edit(self, x, y, scale):
        indices = self.selected_indices()
        if len(indices) == 1:
            index = indices[0]
            shape = self.project.shapes[index]
            if shape.visible and shape.kind in editor_model.POINT_EDIT_KINDS:
                distance, vertex = editor_model.nearest_vertex(shape, (x, y))
                if distance <= 7 / scale:
                    self.drag = Drag('point', x, y, scale, (index,), vertex=vertex)
                    return
        for index in reversed(range(len(self.project.shapes))):
            shape = self.project.shapes[index]
            if shape.visible and shape.kind in editor_model.POINT_EDIT_KINDS and editor_model.hit(shape, (x, y), 5 / scale):
                self.set_selection((index,))
                break

    def draw_shape(self, drag, x, y):
        start = drag.points[0]
        end = self.grid_point(x, y)
        if drag.mode == "Freehand":
            if end != drag.points[-1]:
                drag.points.append(end)
            if len(drag.points) < 2:
                return None
            path = template.CutPath(tuple(drag.points), closed=False)
            return editor_model.shape_from_path(path)
        if drag.mode == "Line":
            return Shape("Line", *start, end[0] - start[0], end[1] - start[1]) if end != start else None
        w, h = abs(end[0] - start[0]), abs(end[1] - start[1])
        if min(w, h) < 0.1:
            return None
        return Shape(drag.mode, min(start[0], end[0]), min(start[1], end[1]), w, h)

    def drag_update(self, _gesture, dx, dy):
        drag = self.drag
        if drag is None or (self.draft is None and abs(dx) + abs(dy) < 2):
            return
        x, y = drag.x + dx / drag.scale, drag.y + dy / drag.scale
        base = self.history.current
        if drag.mode == 'marquee':
            self.marquee = geometry.Rect(min(drag.x, x), min(drag.y, y),
                                         abs(x - drag.x), abs(y - drag.y))
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
                shape = self.draw_shape(drag, x, y)
            except ValueError:
                shape = None
            valid = shape is not None and max(abs(shape.x), abs(shape.y), abs(shape.width),
                                              abs(shape.height)) <= limit
            self.draft = replace(base, shapes=base.shapes + (shape,)) if valid else None
        self.canvas.queue_draw()

    def drag_end(self, _gesture, dx, dy):
        if self.drag is None:
            return
        if dx or dy:
            self.drag_update(None, dx, dy)
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
            self.history.commit(self.draft)
            self.cancel_drag()
            self.refresh_objects(indices)
        else:
            self.cancel_drag()
            self.set_selection(indices)

    def cancel_drag(self, *_args):
        self.drag = self.draft = self.marquee = None
        self.drawing = False
        self.select_shape()

    def canvas_key(self, _controller, key, _code, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        indices = self.selected_indices()
        if key == Gdk.KEY_Escape:
            self.cancel_drag()
            self.set_selection(())
            self.tool.set_selected(0)
        elif self.active_tool == 'Polygon' and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.finish_polygon()
        elif not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)) and (
                tool_index := TOOL_KEYS.get(Gdk.keyval_to_upper(key))) is not None:
            self.tool.set_selected(tool_index)
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
        elif key in (Gdk.KEY_BackSpace, Gdk.KEY_Delete) and indices:
            self.delete_shape(None)
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down) and indices:
            distance = self.grid_spacing.get_value() if self.snap.get_active() else 1
            dx, dy = {Gdk.KEY_Left: (-distance, 0), Gdk.KEY_Right: (distance, 0),
                      Gdk.KEY_Up: (0, -distance), Gdk.KEY_Down: (0, distance)}[key]
            self.commit(editor_model.move(self.project.shapes, indices, dx, dy), indices)
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
                self.status.set_text(str(error))

        (dialog.save if save_name else dialog.open)(self.parent, None, finish)

    def open_project(self, _button):
        def opened(path):
            native_project = path.suffix.lower() == '.scc-template'
            if native_project:
                project = load_project(path, legacy_registration=self.project.registration)
                details = ''
            else:
                drawing = importer.load(path)
                shapes, details = editor_model.editable_import(drawing)
                size = drawing.page_size_mm or (self.project.width, self.project.height)
                if not all(1 <= mm <= editor_model.SHAPE_LIMIT for mm in size):
                    raise ValueError('Design supports page dimensions from 1 to 1,000 mm.')
                project = editor_model.Project(*size, shapes, mat=self.project.mat)

            def replace_project():
                self.history = History(project)
                self.saved = project
                self.project_path = path if native_project else None
                self.source_name = path.name
                self.custom_page = False
                if project.registration is not None:
                    self.last_registration = project.registration
                self.polygon.clear()
                self.fit(None)
                self.refresh_objects((0,))
                notice = design_text.unavailable_fonts(project) or details
                if notice:
                    self.status.set_text(notice)

            self.confirm_discard(replace_project)

        self.file_dialog('Open Design', opened, filters=('*.scc-template', '*.svg', '*.SVG',
                         '*.dxf', '*.DXF', '*.studio3', '*.STUDIO3'))

    def new_project(self, _button):
        def create():
            project = replace(self.project, shapes=())
            self.history = History(project)
            self.saved = project
            self.project_path = self.source_name = None
            self.fit(None)
            self.refresh_objects()

        self.confirm_discard(create)

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
                self.status.set_text(f'Choose the full name {path.name} to confirm replacement.')
                return
        try:
            save_project(path, self.project)
        except (OSError, ValueError) as error:
            self.status.set_text(f'Could not save {path.name}. {error}')
            return
        self.project_path = path
        self.saved = self.project
        self.refresh_status()
        self.status.set_text(f'Saved {path.name}')
        if continuation:
            continuation()

    def import_vector(self, _button):
        def opened(path):
            shapes, details = import_shapes(path)
            p_unit = 'path' if len(shapes) == 1 else 'paths'
            alert = Adw.AlertDialog(heading='Place imported paths?',
                                    body=f'{len(shapes)} {p_unit} · {path.name}\n{details}')
            alert.add_response('cancel', 'Cancel')
            alert.add_response('place', 'Place')
            alert.set_close_response('cancel')
            alert.connect('response', lambda _dialog, response: self.commit(
                self.project.shapes + shapes, range(len(self.project.shapes),
                                                    len(self.project.shapes) + len(shapes))) if response == 'place' else None)
            alert.present(self.parent)

        self.file_dialog('Import cut geometry', opened, filters=('*.svg','*.dxf','*.SVG','*.DXF','*.studio3','*.STUDIO3'))

