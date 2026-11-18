"""Full-window GTK design workspace and shape inspector."""

from dataclasses import dataclass, replace
import math
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import editor_model, geometry, graphtec, importer, regmarks, template
from .editor_canvas import DesignCanvas
from .editor_model import (ACTIONS, ALIGNMENTS, ALL_ACTIONS, KINDS, REVIEW_ACTIONS,
                           History, Registration, Shape,
                           import_shapes, load_project, save_project)


@dataclass
class Drag:
    mode: str
    x: float
    y: float
    scale: float
    indices: tuple[int, ...] = ()
    points: list | None = None
    vertex: int | None = None


def panel(width):
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                   margin_top=14, margin_bottom=14, margin_start=12, margin_end=12)
    scroll.set_size_request(width, -1)
    scroll.set_child(body)
    return scroll, body


class DesignWorkspace:
    def __init__(self, parent, project, on_back, on_review, *, cutter=None):
        self.view = Adw.ToolbarView()
        self.parent = parent
        self.history = History(project)
        self.saved = project
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

        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='Design')
        header.set_title_widget(self.title)
        self.button(header, 'Back', on_back, start=True)
        self.button(header, 'New', self.new_project, start=True)
        self.button(header, 'Open…', self.open_project)
        self.button(header, 'Save…', self.save)
        self.view.add_top_bar(header)

        toolbar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.view.add_top_bar(toolbar)
        self.undo_button = self.button(toolbar, 'Undo', lambda *_: self.change_history(self.history.undo))
        self.redo_button = self.button(toolbar, 'Redo', lambda *_: self.change_history(self.history.redo))
        self.button(toolbar, 'Import…', self.import_vector)
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 4, 0.1)
        self.zoom.set_value(1)
        self.zoom.set_size_request(105, -1)
        self.zoom.set_tooltip_text('Canvas zoom')
        self.zoom.connect('value-changed', self.zoom_changed)
        toolbar.append(self.zoom)
        self.button(toolbar, 'Fit', self.fit)

        body = Gtk.Box()
        left, objects = panel(174)
        objects.append(Gtk.Label(label='Drawing Tool', xalign=0, css_classes=['heading']))
        self.tools = ("Select", "Point Edit", *KINDS, "Polygon", "Freehand")
        self.tool = Gtk.DropDown.new_from_strings(self.tools)
        self.tool.connect('notify::selected', self.tool_changed)
        objects.append(self.tool)
        self.add_button = self.button(objects, 'Add Shape', self.add_shape)
        objects.append(Gtk.Label(label='Objects', xalign=0, css_classes=['heading'], margin_top=10))
        self.objects = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.objects.add_css_class('boxed-list')
        self.objects.connect('selected-rows-changed', self.select_shape)
        objects.append(self.objects)
        self.duplicate = self.button(objects, 'Duplicate', self.duplicate_shape)
        self.group_button = self.button(objects, 'Group', lambda *_: self.group_selected())
        self.ungroup_button = self.button(objects, 'Ungroup', lambda *_: self.group_selected(release=True))
        self.remove = self.button(objects, 'Delete', self.delete_shape)
        self.forward = self.button(objects, 'Bring Forward', lambda *_: self.reorder(1))
        self.backward = self.button(objects, 'Send Backward', lambda *_: self.reorder(-1))

        self.canvas = DesignCanvas(self)
        self.canvas.set_cursor_from_name('default')
        self.tool.set_tooltip_text('Drag to draw or move. Shift-click extends the selection. '
                                    'Use the middle button to pan.')
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=252, hexpand=False)
        self.inspector = Gtk.Stack(vexpand=True)
        panels = (('Page Setup', self.page_panel), ('Transform', self.transform_panel),
                  ('Replicate', self.replicate_panel), ('Line and Fill', self.style_panel))
        for label, build in panels:
            scroll, content = panel(244)
            build(content)
            self.inspector.add_named(scroll, label)
        switcher = Gtk.DropDown.new_from_strings([label for label, _ in panels])
        switcher.set_margin_start(12)
        switcher.set_margin_end(12)
        switcher.set_margin_top(8)
        switcher.connect('notify::selected', lambda widget, *_: self.inspector.set_visible_child_name(
            panels[widget.get_selected()][0]))
        right.append(switcher)
        right.append(self.inspector)
        for child in (left, self.canvas, right):
            body.append(child)
        self.view.set_content(body)

        footer = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, margin_bottom=8)
        self.status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        footer.append(self.status)
        self.review_action = Gtk.DropDown.new_from_strings(REVIEW_ACTIONS)
        self.review_action.set_tooltip_text('Action to review')
        self.review_action.connect('notify::selected', lambda *_: self.refresh_status())
        footer.append(self.review_action)
        self.review = self.button(footer, 'Review paths', self.review_paths)
        self.review.add_css_class('suggested-action')
        self.view.add_bottom_bar(footer)
        self.refresh_objects((0,))
        self.tool_changed()

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
        self.show_mat = self.view_toggle(inspector, 'Show Cutting Mat', True)
        self.show_cut_border = self.view_toggle(inspector, 'Show Cut Border', True)
        self.heading(inspector, 'Registration Marks')
        self.register = Gtk.CheckButton(label='Enable Registration Marks')
        self.register.connect('toggled', self.change_registration)
        inspector.append(self.register)
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
        self.heading(inspector, 'Grid Settings')
        self.show_rulers = self.view_toggle(inspector, 'Show Rulers')
        self.show_grid = self.view_toggle(inspector, 'Show Grid', True)
        self.snap = self.view_toggle(inspector, 'Snap to Grid')
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
            ('radius', 'Radius (mm)', 0, 1000), ('rotation', 'Rotation (°)', -360, 360),
        )
        self.fields = {}
        for field, label, low, high in fields:
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
        self.color_edits = set()
        for field, label in (('stroke', 'Line Color'), ('fill', 'Fill Color')):
            inspector.append(Gtk.Label(label=label, xalign=0))
            entry = Gtk.Entry(placeholder_text='#rrggbb or blank')
            entry.connect('activate', self.apply_colors)
            entry.connect('changed', self.change_color, field)
            inspector.append(entry)
            self.colors[field] = entry
        self.button(inspector, 'Apply Colors', self.apply_colors)
        self.heading(inspector, 'Object Action')
        self.action = Gtk.DropDown.new_from_strings(ACTIONS)
        self.action.connect('notify::selected', self.change_action)
        inspector.append(self.action)

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
        while (child := self.objects.get_first_child()) is not None:
            self.objects.remove(child)
        for i, obj in enumerate(self.project.shapes, 1):
            tooltip = obj.action if obj.group is None else f'{obj.action} · Group {obj.group}'
            label = Gtk.Label(label=f'{i}. {obj.kind}', xalign=0, tooltip_text=tooltip,
                              margin_top=7, margin_bottom=7, margin_start=8, margin_end=8)
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
        action = REVIEW_ACTIONS[self.review_action.get_selected()]
        count = sum(s.visible and s.action != "None" and action in (s.action, ALL_ACTIONS)
                    for s in p.shapes)
        label = "cut and score" if action == ALL_ACTIONS else action.lower()
        selection = len(self.selected_indices())
        self.status.set_text(f'{p.width:g} × {p.height:g} mm · {count} {label} path(s)'
                             + (f' · {selection} selected' if selection else ''))
        self.title.set_subtitle('Unsaved changes' if self.dirty else '')
        self.undo_button.set_sensitive(bool(self.history.past))
        self.redo_button.set_sensitive(bool(self.history.future))
        self.review.set_sensitive(count > 0)
        self.canvas.queue_draw()

    def select_shape(self, *_args):
        if self.loading:
            return
        indices = self.selected_indices()
        shapes = self.project.shapes
        obj = shapes[indices[0]] if len(indices) == 1 else None
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
        self.color_edits.clear()
        self.action.set_sensitive(bool(indices))
        object_actions = {shapes[index].action for index in indices}
        self.action.set_selected(ACTIONS.index(object_actions.pop()) if len(object_actions) == 1
                                 else Gtk.INVALID_LIST_POSITION)
        self.duplicate.set_sensitive(bool(indices))
        self.remove.set_sensitive(bool(indices))
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
        kind = self.active_tool
        if kind in KINDS:
            p = self.project
            shape = Shape(kind, x=(p.width - 40) / 2, y=(p.height - 30) / 2)
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

    def zoom_changed(self, scale):
        self.canvas.zoom = scale.get_value()
        self.canvas.queue_draw()

    def fit(self, _button):
        self.canvas.pan = (0, 0)
        self.zoom.set_value(1)
        self.canvas.queue_draw()

    @property
    def active_tool(self):
        return self.tools[self.tool.get_selected()]

    def tool_changed(self, *_args):
        self.polygon.clear()
        self.cancel_drag()
        mode = self.active_tool
        self.add_button.set_sensitive(mode in KINDS)
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
            if shape.visible and shape.kind in ('Path', 'Line'):
                distances = [math.dist((x, y), point) for point in shape.path().points]
                vertex = min(range(len(distances)), key=distances.__getitem__)
                if distances[vertex] <= 7 / scale:
                    self.drag = Drag('point', x, y, scale, (index,), vertex=vertex)
                    return
        for index in reversed(range(len(self.project.shapes))):
            shape = self.project.shapes[index]
            if shape.visible and shape.kind in ('Path', 'Line') and editor_model.hit(shape, (x, y), 5 / scale):
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
            px, py = shape.path().points[drag.vertex]
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
                shapes = editor_model.move(base.shapes, drag.indices, ax - anchor.x, ay - anchor.y)
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
        alert = Adw.AlertDialog(heading='Discard unsaved changes?',
                                body='Save the project first to keep your edits.')
        alert.add_response('cancel', 'Cancel')
        alert.add_response('discard', 'Discard')
        alert.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response('cancel')
        alert.set_close_response('cancel')
        alert.connect('response', lambda _dialog, response: continuation() if response == 'discard' else None)
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
                self.custom_page = False
                if project.registration is not None:
                    self.last_registration = project.registration
                self.polygon.clear()
                self.fit(None)
                self.refresh_objects((0,))
                if details:
                    self.status.set_text(details)

            self.confirm_discard(replace_project)

        self.file_dialog('Open Design', opened, filters=('*.scc-template', '*.svg', '*.SVG',
                         '*.dxf', '*.DXF', '*.studio3', '*.STUDIO3'))

    def new_project(self, _button):
        def create():
            project = replace(self.project, shapes=())
            self.history = History(project)
            self.saved = project
            self.fit(None)
            self.refresh_objects()

        self.confirm_discard(create)

    def save(self, _button):
        def saved(path):
            if not path.suffix:
                path = path.with_suffix('.scc-template')
            save_project(path, self.project)
            self.saved = self.project
            self.refresh_status()
            self.status.set_text(f'Saved {path.name}')

        self.file_dialog('Save design project', saved, save_name='design.scc-template')

    def import_vector(self, _button):
        def opened(path):
            shapes, details = import_shapes(path)
            alert = Adw.AlertDialog(heading='Place imported paths?',
                                    body=f'{len(shapes)} path(s) · {path.name}\n{details}')
            alert.add_response('cancel', 'Cancel')
            alert.add_response('place', 'Place')
            alert.set_close_response('cancel')
            alert.connect('response', lambda _dialog, response: self.commit(
                self.project.shapes + shapes, range(len(self.project.shapes),
                                                    len(self.project.shapes) + len(shapes))) if response == 'place' else None)
            alert.present(self.parent)

        self.file_dialog('Import cut geometry', opened, filters=('*.svg','*.dxf','*.SVG','*.DXF','*.studio3','*.STUDIO3'))

    def review_paths(self, _button):
        action = REVIEW_ACTIONS[self.review_action.get_selected()]
        self.on_review(self.project, action)
