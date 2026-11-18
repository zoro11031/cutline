"""Full-window GTK design workspace and shape inspector."""

from dataclasses import replace
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from . import geometry
from .editor_model import (ACTIONS, ALL_ACTIONS, KINDS, REVIEW_ACTIONS, History, Shape,
                           import_shapes, load_project, save_project)


class DesignCanvas(Gtk.DrawingArea):
    def __init__(self, editor):
        super().__init__(hexpand=True, vexpand=True)
        self.editor = editor
        self.zoom = 1.0
        self.pan = (0.0, 0.0)
        self.set_draw_func(self.draw)
        move = Gtk.GestureDrag(button=1)
        move.connect('drag-begin', editor.drag_begin)
        move.connect('drag-update', editor.drag_update)
        move.connect('drag-end', editor.drag_end)
        move.connect('cancel', editor.cancel_drag)
        self.add_controller(move)
        pan = Gtk.GestureDrag(button=2)
        pan.connect('drag-begin', lambda *_: setattr(self, 'pan_start', self.pan))
        pan.connect('drag-update', self.pan_update)
        self.add_controller(pan)

    def pan_update(self, _gesture, x, y):
        self.pan = self.pan_start[0] + x, self.pan_start[1] + y
        self.queue_draw()

    def viewport(self, width, height):
        project = self.editor.project
        scale = max(0.01, min((width-56)/project.width, (height-56)/project.height)) * self.zoom
        return ((width-project.width*scale)/2+self.pan[0],
                (height-project.height*scale)/2+self.pan[1], scale)

    def draw(self, _area, cr, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        cr.set_source_rgb(*(0.12, 0.13, 0.15) if dark else (0.88, 0.89, 0.92))
        cr.paint()
        project = self.editor.project
        x, y, scale = self.viewport(width, height)
        w, h = project.width, project.height
        cr.translate(x, y)
        cr.scale(scale, scale)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_line_width(1/scale)
        if self.editor.snap.get_active():
            cr.set_source_rgb(0.90, 0.92, 0.95)
            for i in range(5, int(w), 5):
                cr.move_to(i, 0)
                cr.line_to(i, h)
            for i in range(5, int(h), 5):
                cr.move_to(0, i)
                cr.line_to(w, i)
            cr.stroke()
        marks = self.editor.marks
        if marks is not None:
            cr.set_source_rgba(0.9, 0.3, 0.22, 0.13)
            for zone in marks.corner_zones():
                cr.rectangle(zone.x, zone.y, zone.w, zone.h)
            cr.fill()
            cr.set_source_rgb(0.05, 0.05, 0.05)
            if marks.square:
                s = marks.square
                cr.rectangle(s.x, s.y, s.w, s.h)
                cr.fill()
            cr.set_line_width(marks.thickness_mm)
            for mark in marks.ells:
                for a, b, c, d in mark.arms():
                    cr.move_to(a, b)
                    cr.line_to(c, d)
            cr.stroke()

        selection = self.editor.selected_index()
        for i, shape in enumerate(project.shapes):
            if not shape.visible:
                continue
            path = shape.path()
            cr.move_to(*path.points[0])
            for point in path.points[1:]:
                cr.line_to(*point)
            if path.closed:
                cr.close_path()
            if i == selection:
                cr.set_source_rgba(0.25, 0.37, 0.76, 0.14)
                if path.closed:
                    cr.fill_preserve()
                cr.set_source_rgb(0.19, 0.30, 0.77)
            else:
                cr.set_source_rgb(0.21, 0.25, 0.30)
            cr.set_line_width((2 if i == selection else 1.25)/scale)
            cr.set_dash([3/scale, 2/scale] if shape.action == 'Score' else [])
            cr.stroke()


def panel(width):
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                   margin_top=14, margin_bottom=14, margin_start=12, margin_end=12)
    scroll.set_size_request(width, -1)
    scroll.set_child(body)
    return scroll, body


class DesignWorkspace:
    def __init__(self, parent, project, on_back, on_review):
        self.view = Adw.ToolbarView()
        self.parent = parent
        self.history = History(project)
        self.saved = project
        self.on_review = on_review
        self.loading = False
        self.drag = None
        self.draft = None

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
        self.button(toolbar, 'Import SVG or DXF…', self.import_vector)
        self.snap = Gtk.CheckButton(label='Snap to 5 mm grid')
        self.snap.connect('toggled', lambda *_: self.canvas.queue_draw())
        toolbar.append(self.snap)
        self.zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 4, 0.1)
        self.zoom.set_value(1)
        self.zoom.set_size_request(105, -1)
        self.zoom.set_tooltip_text('Canvas zoom')
        self.zoom.connect('value-changed', self.zoom_changed)
        toolbar.append(self.zoom)
        self.button(toolbar, 'Fit', self.fit)

        body = Gtk.Box()
        left, objects = panel(184)
        objects.append(Gtk.Label(label='Objects', xalign=0, css_classes=['heading']))
        self.kind = Gtk.DropDown.new_from_strings(KINDS)
        objects.append(self.kind)
        self.button(objects, 'Add shape', self.add_shape)
        self.objects = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.objects.add_css_class('boxed-list')
        self.objects.connect('row-selected', self.select_shape)
        objects.append(self.objects)
        self.duplicate = self.button(objects, 'Duplicate', self.duplicate_shape)
        self.remove = self.button(objects, 'Delete', self.delete_shape)
        self.forward = self.button(objects, 'Bring forward', lambda *_: self.reorder(1))
        self.backward = self.button(objects, 'Send backward', lambda *_: self.reorder(-1))

        self.canvas = DesignCanvas(self)
        self.canvas.set_tooltip_text('Drag to move an object. Drag with the middle mouse button to pan.')
        right, inspector = panel(244)
        inspector.append(Gtk.Label(label='Page', xalign=0, css_classes=['heading']))
        self.page_sizes = tuple((f'{sheet.name} · {direction}', sheet.size(portrait))
                                for sheet in geometry.SHEETS.values()
                                for direction, portrait in (('Portrait', True), ('Landscape', False)))
        self.page = Gtk.DropDown.new_from_strings([name for name, _ in self.page_sizes])
        self.page.connect('notify::selected', self.change_page)
        inspector.append(self.page)
        inspector.append(Gtk.Label(label='Properties', xalign=0, css_classes=['heading']))
        self.fields = {}
        for field, label, low, high in (
                ('x', 'X (mm)', -1000, 1000), ('y', 'Y (mm)', -1000, 1000),
                ('width', 'Width (mm)', -1000, 1000), ('height', 'Height (mm)', -1000, 1000),
                ('radius', 'Radius (mm)', 0, 1000), ('rotation', 'Rotation (°)', -360, 360)):
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            spin = Gtk.SpinButton.new_with_range(low, high, 0.1)
            spin.set_digits(2)
            spin.connect('value-changed', self.change_field, field)
            row.append(spin)
            inspector.append(row)
            self.fields[field] = spin
        self.flips = {}
        for field, label in (('flip_x', 'Mirror horizontally'), ('flip_y', 'Mirror vertically'),
                             ('visible', 'Visible')):
            toggle = Gtk.CheckButton(label=label)
            toggle.connect('toggled', self.change_toggle, field)
            inspector.append(toggle)
            self.flips[field] = toggle
        inspector.append(Gtk.Label(label='Object action', xalign=0))
        self.action = Gtk.DropDown.new_from_strings(ACTIONS)
        self.action.connect('notify::selected', self.change_action)
        inspector.append(self.action)
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
        self.refresh_objects(0)

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

    def selected_index(self):
        row = self.objects.get_selected_row()
        return row.get_index() if row is not None else None

    def refresh_objects(self, index):
        self.loading = True
        size = (self.project.width, self.project.height)
        index_for_size = next((i for i, (_, page) in enumerate(self.page_sizes) if page == size),
                              len(self.page_sizes))
        names = [name for name, _ in self.page_sizes]
        if index_for_size == len(names):
            names.append(f'{size[0]:g} × {size[1]:g} mm')
        self.page.set_model(Gtk.StringList.new(names))
        self.page.set_selected(index_for_size)
        while (child := self.objects.get_first_child()) is not None:
            self.objects.remove(child)
        for i, obj in enumerate(self.project.shapes):
            label = Gtk.Label(label=f'{i+1}. {obj.kind}', xalign=0,
                              margin_top=7, margin_bottom=7, margin_start=8, margin_end=8)
            self.objects.append(label)
        if self.project.shapes and index is not None:
            self.objects.select_row(self.objects.get_row_at_index(min(index, len(self.project.shapes)-1)))
        self.loading = False
        self.select_shape()

    def refresh_status(self):
        p = self.project
        action = REVIEW_ACTIONS[self.review_action.get_selected()]
        count = sum(s.visible and s.action != "None" and action in (s.action, ALL_ACTIONS)
                    for s in p.shapes)
        label = "cut and score" if action == ALL_ACTIONS else action.lower()
        self.status.set_text(f'{p.width:g} × {p.height:g} mm · {count} {label} path(s)')
        self.title.set_subtitle('Unsaved changes' if self.dirty else '')
        self.undo_button.set_sensitive(bool(self.history.past))
        self.redo_button.set_sensitive(bool(self.history.future))
        self.review.set_sensitive(count > 0)
        self.canvas.queue_draw()

    def select_shape(self, *_args):
        if self.loading:
            return
        i = self.selected_index()
        obj = self.project.shapes[i] if i is not None else None
        self.loading = True
        for field, spin in self.fields.items():
            enabled = obj is not None and (field != 'radius' or obj.kind == 'Rounded rectangle')
            spin.set_sensitive(enabled)
            if obj is not None:
                if field in ('width', 'height'):
                    low = -1000 if obj.kind == 'Line' else 0 if obj.kind == 'Path' else 0.1
                    spin.set_range(low, 1000)
                spin.set_value(getattr(obj, field))
        for field, toggle in self.flips.items():
            toggle.set_sensitive(obj is not None)
            if obj is not None:
                toggle.set_active(getattr(obj, field))
        self.action.set_sensitive(obj is not None)
        if obj is not None:
            self.action.set_selected(ACTIONS.index(obj.action))
        self.duplicate.set_sensitive(obj is not None)
        self.remove.set_sensitive(obj is not None)
        self.forward.set_sensitive(i is not None and i+1 < len(self.project.shapes))
        self.backward.set_sensitive(i is not None and i > 0)
        self.loading = False
        self.refresh_status()

    def commit(self, shapes, index):
        self.history.commit(replace(self.history.current, shapes=tuple(shapes)))
        self.refresh_objects(index)

    def change_history(self, callback):
        index = self.selected_index()
        self.cancel_drag()
        callback()
        self.refresh_objects(index)

    def change_property(self, field, value):
        i = self.selected_index()
        if self.loading or i is None:
            return
        shapes = self.project.shapes
        updated = replace(shapes[i], **{field: value})
        self.history.commit(replace(self.history.current, shapes=shapes[:i]+(updated,)+shapes[i+1:]))
        self.refresh_status()

    def change_field(self, spin, field):
        self.change_property(field, spin.get_value())

    def change_toggle(self, toggle, field):
        self.change_property(field, toggle.get_active())

    def change_action(self, *_args):
        self.change_property('action', ACTIONS[self.action.get_selected()])

    def add_shape(self, _button):
        shapes = self.project.shapes
        obj = Shape(KINDS[self.kind.get_selected()], x=(self.project.width-40)/2,
                    y=(self.project.height-30)/2)
        self.commit(shapes+(obj,), len(shapes))

    def change_page(self, *_args):
        index = self.page.get_selected()
        if self.loading:
            return
        if index < len(self.page_sizes):
            width, height = self.page_sizes[index][1]
            self.history.commit(replace(self.history.current, width=width, height=height))
        self.fit(None)
        self.refresh_objects(self.selected_index())

    def duplicate_shape(self, _button):
        shapes = self.project.shapes
        obj = shapes[self.selected_index()]
        self.commit(shapes+(replace(obj, x=min(1000,obj.x+5), y=min(1000,obj.y+5)),), len(shapes))

    def delete_shape(self, _button):
        shapes, index = self.project.shapes, self.selected_index()
        self.commit(shapes[:index]+shapes[index+1:], index)

    def reorder(self, step):
        shapes, i = list(self.project.shapes), self.selected_index()
        j = i+step
        shapes[i], shapes[j] = shapes[j], shapes[i]
        self.commit(shapes, j)

    def zoom_changed(self, scale):
        self.canvas.zoom = scale.get_value()
        self.canvas.queue_draw()

    def fit(self, _button):
        self.canvas.pan = (0, 0)
        self.zoom.set_value(1)
        self.canvas.queue_draw()

    def drag_begin(self, gesture, x, y):
        left, top, scale = self.canvas.viewport(self.canvas.get_width(), self.canvas.get_height())
        x, y = (x-left)/scale, (y-top)/scale
        tolerance = 4/scale
        for i in reversed(range(len(self.project.shapes))):
            obj = self.project.shapes[i]
            if not obj.visible:
                continue
            xs, ys = zip(*obj.path().points)
            if min(xs)-tolerance <= x <= max(xs)+tolerance and min(ys)-tolerance <= y <= max(ys)+tolerance:
                self.objects.select_row(self.objects.get_row_at_index(i))
                self.drag = i, obj, scale
                return
        self.objects.unselect_all()
        gesture.set_state(Gtk.EventSequenceState.DENIED)

    def drag_update(self, _gesture, dx, dy):
        if self.drag is None or (self.draft is None and abs(dx)+abs(dy) < 2):
            return
        i, obj, scale = self.drag
        x, y = obj.x+dx/scale, obj.y+dy/scale
        if self.snap.get_active():
            x, y = round(x/5)*5, round(y/5)*5
        shapes = self.history.current.shapes
        obj = replace(obj, x=max(-1000,min(1000,round(x,2))), y=max(-1000,min(1000,round(y,2))))
        self.draft = replace(self.history.current, shapes=shapes[:i]+(obj,)+shapes[i+1:])
        self.select_shape()

    def drag_end(self, _gesture, dx, dy):
        if self.drag is None:
            return
        if dx or dy:
            self.drag_update(None, dx, dy)
        if self.draft is not None:
            self.history.commit(self.draft)
        self.cancel_drag()

    def cancel_drag(self, *_args):
        self.drag = self.draft = None
        self.select_shape()

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
            project = load_project(path, legacy_registration=self.project.registration)

            def replace_project():
                self.history = History(project)
                self.saved = project
                self.fit(None)
                self.refresh_objects(0)

            self.confirm_discard(replace_project)

        self.file_dialog('Open design project', opened, filters=('*.scc-template',))

    def new_project(self, _button):
        def create():
            project = replace(self.project, shapes=())
            self.history = History(project)
            self.saved = project
            self.fit(None)
            self.refresh_objects(None)

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
                self.project.shapes+shapes, len(self.project.shapes)) if response == 'place' else None)
            alert.present(self.parent)

        self.file_dialog('Import cut geometry', opened, filters=('*.svg','*.dxf','*.SVG','*.DXF','*.studio3','*.STUDIO3'))

    def review_paths(self, _button):
        action = REVIEW_ACTIONS[self.review_action.get_selected()]
        self.on_review(self.project, action)
