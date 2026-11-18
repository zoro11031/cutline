"""GTK editing window for cut template projects."""

from dataclasses import replace
from pathlib import Path

from gi.repository import GLib, Gtk

from . import job, template
from .editor_model import KINDS, Shape, artwork, read_project, write_project


class TemplateEditor(Gtk.Window):
    def __init__(self, parent, checked, options, preview, shapes, on_apply):
        super().__init__(title="Template editor", transient_for=parent, modal=True,
                         default_width=850, default_height=580)
        self.width, self.height = checked.grid.sheet_w_mm, checked.grid.sheet_h_mm
        self.options = dict(options, portrait=checked.grid.portrait,
                            mode=template.ROUNDED, skip=())
        self.shapes = list(shapes)
        self.preview = preview
        self.on_apply = on_apply
        self.loading = False
        self.drag_origin = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.set_child(root)
        fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.append(fields)
        tools = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tools.set_size_request(250, -1)
        fields.append(tools)
        fields.append(preview)
        preview.set_hexpand(True)
        preview.set_vexpand(True)
        fields.set_vexpand(True)

        self.kind = Gtk.DropDown.new_from_strings(KINDS)
        tools.append(self.kind)
        add = Gtk.Button(label="Add shape")
        add.connect("clicked", self.add_shape)
        tools.append(add)
        self.selection = Gtk.DropDown.new_from_strings([])
        self.selection.connect("notify::selected", self.select_shape)
        tools.append(self.selection)
        self.fields = {}
        for attr, label in (("x", "X (mm)"), ("y", "Y (mm)"),
                            ("width", "Width / ΔX (mm)"), ("height", "Height / ΔY (mm)"),
                            ("radius", "Corner radius (mm)")):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            low = -1000 if attr in ("x", "y", "width", "height") else 0
            spin = Gtk.SpinButton.new_with_range(low, 1000, 0.1)
            spin.set_digits(2)
            spin.connect("value-changed", self.change_shape, attr)
            row.append(spin)
            tools.append(row)
            self.fields[attr] = spin

        duplicate = Gtk.Button(label="Duplicate selected")
        duplicate.connect("clicked", self.duplicate_shape)
        self.duplicate = duplicate
        tools.append(duplicate)
        delete = Gtk.Button(label="Delete selected")
        delete.connect("clicked", self.delete_shape)
        self.delete = delete
        tools.append(delete)

        files = Gtk.Box(spacing=6)
        for label, handler in (("Open project", self.open_project),
                               ("Save project", self.save_project)):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            files.append(button)
        tools.append(files)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        preview.add_controller(drag)

        footer = Gtk.Box(spacing=12)
        root.append(footer)
        self.status = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        footer.append(self.status)
        self.apply = Gtk.Button(label="Use template")
        self.apply.add_css_class("suggested-action")
        self.apply.connect("clicked", self.apply_template)
        footer.append(self.apply)
        self.refresh_list(0)

    def refresh_list(self, index):
        self.loading = True
        self.selection.set_model(Gtk.StringList.new(
            [f"{i + 1}. {shape.kind}" for i, shape in enumerate(self.shapes)]))
        self.selection.set_selected(index if self.shapes else Gtk.INVALID_LIST_POSITION)
        self.loading = False
        self.select_shape()

    def select_shape(self, *_args):
        if self.loading:
            return
        self.loading = True
        index = self.selection.get_selected()
        shape = self.shapes[index] if index < len(self.shapes) else None
        self.delete.set_sensitive(shape is not None)
        self.duplicate.set_sensitive(shape is not None)
        for attr, spin in self.fields.items():
            enabled = shape is not None and (attr != "radius" or shape.kind == "Rounded rectangle")
            spin.set_sensitive(enabled)
            if shape is not None:
                lower = (-1000 if attr in ("x", "y") or
                         (shape.kind == "Line" and attr in ("width", "height"))
                         else 0 if attr == "radius" else 0.1)
                spin.set_range(lower, 1000)
                spin.set_value(getattr(shape, attr))
        self.loading = False
        self.rebuild()

    def change_shape(self, spin, attr):
        if self.loading:
            return
        index = self.selection.get_selected()
        self.shapes[index] = replace(self.shapes[index], **{attr: spin.get_value()})
        self.rebuild()

    def add_shape(self, _button):
        self.shapes.append(Shape(KINDS[self.kind.get_selected()]))
        self.refresh_list(len(self.shapes) - 1)

    def duplicate_shape(self, _button):
        shape = self.shapes[self.selection.get_selected()]
        self.shapes.append(replace(shape, x=shape.x + 5, y=shape.y + 5))
        self.refresh_list(len(self.shapes) - 1)

    def delete_shape(self, _button):
        index = self.selection.get_selected()
        self.shapes.pop(index)
        self.refresh_list(min(index, len(self.shapes) - 1))

    def drag_begin(self, _gesture, x, y):
        self.drag_origin = None
        left, top, scale = self.preview.viewport(
            self.preview.get_width(), self.preview.get_height(), self.width, self.height)
        if scale <= 0:
            return
        x, y = (x - left) / scale, (y - top) / scale
        margin = 4 / scale
        for index in reversed(range(len(self.shapes))):
            shape = self.shapes[index]
            x1, x2 = sorted((shape.x, shape.x + shape.width))
            y1, y2 = sorted((shape.y, shape.y + shape.height))
            if x1 - margin <= x <= x2 + margin and y1 - margin <= y <= y2 + margin:
                self.selection.set_selected(index)
                self.drag_origin = index, shape, scale
                return

    def drag_update(self, _gesture, dx, dy):
        if self.drag_origin is None:
            return
        index, shape, scale = self.drag_origin
        self.shapes[index] = replace(shape, x=round(shape.x + dx / scale, 2),
                                     y=round(shape.y + dy / scale, 2))
        self.select_shape()

    def open_project(self, _button):
        dialog = Gtk.FileDialog(title="Open template project")

        def opened(dialog, result):
            try:
                path = Path(dialog.open_finish(result).get_path())
            except GLib.Error:
                return
            try:
                self.shapes = list(read_project(path, self.width, self.height))
            except (OSError, ValueError) as exc:
                self.status.set_text(f"Could not open project: {exc}")
                return
            self.refresh_list(0)

        dialog.open(self, None, opened)

    def save_project(self, _button):
        dialog = Gtk.FileDialog(title="Save template project", initial_name="template.scc-template")

        def saved(dialog, result):
            try:
                path = Path(dialog.save_finish(result).get_path())
            except GLib.Error:
                return
            try:
                write_project(path, self.shapes, self.width, self.height)
            except OSError as exc:
                self.status.set_text(f"Could not save project: {exc}")
                return
            self.status.set_text(f"Saved {path.name}")

        dialog.save(self, None, saved)

    def rebuild(self):
        self.preview.set_job(None)
        self.apply.set_sensitive(False)
        if not self.shapes:
            self.status.set_text("Add a shape to start the template.")
            return
        if any(s.kind == "Line" and s.width == s.height == 0 for s in self.shapes):
            self.status.set_text("A line needs two distinct endpoints.")
            return
        design = artwork(self.shapes, self.width, self.height)
        checked = job.build(**dict(self.options, imported=design))
        self.preview.set_job(checked)
        self.status.set_text(checked.blocked[0] if checked.blocked
                             else f"{len(design.paths)} shapes verified")
        self.apply.set_sensitive(True)

    def apply_template(self, _button):
        self.on_apply(tuple(self.shapes), artwork(self.shapes, self.width, self.height))
        self.close()
