"""Design page, mat, registration overlay and live drawing feedback."""

from functools import lru_cache
from io import BytesIO
import math

import cairo

from gi.repository import Adw, Gdk, Gtk

from . import editor_model, path_edit
from .design_tools import ROTATE_MODES, SCALE_MODES, TRANSFORM_MODES, TURN_RIGHT, rotation_handle


@lru_cache(maxsize=4)
def _sticker_surface(png):
    return cairo.ImageSurface.create_from_png(BytesIO(png))


def _rgb(value):
    return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))


def draw_strokes(context, strokes):
    for first, *rest in strokes:
        context.move_to(*first)
        for point in rest:
            context.line_to(*point)
    context.stroke()


def _grid(cr, width, height, spacing):
    for index in range(1, math.ceil(width / spacing)):
        x = index * spacing
        cr.move_to(x, 0)
        cr.line_to(x, height)
    for index in range(1, math.ceil(height / spacing)):
        y = index * spacing
        cr.move_to(0, y)
        cr.line_to(width, y)
    cr.stroke()


def draw_feed_arrow(cr, width, color, unit=1):
    center = width / 2
    cr.set_source_rgb(*color)
    cr.move_to(center, -11 * unit)
    for dx, y in ((5, -6), (2, -6), (2, -2), (-2, -2), (-2, -6), (-5, -6)):
        cr.line_to(center + dx * unit, y * unit)
    cr.close_path()
    cr.fill()


class DesignCanvas(Gtk.DrawingArea):
    def __init__(self, editor):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.editor = editor
        self.zoom = 1.0
        self.pan = (0.0, 0.0)
        self.pointer = None
        self.set_draw_func(self.draw)
        move = Gtk.GestureDrag(button=1)
        move.connect('drag-begin', editor.drag_begin)
        move.connect('drag-update', editor.drag_update)
        move.connect('drag-end', editor.drag_end)
        move.connect('cancel', editor.cancel_drag)
        self.add_controller(move)
        points = Gtk.GestureClick(button=1)
        points.connect('pressed', editor.pick_polygon_point)
        self.add_controller(points)
        pan = Gtk.GestureDrag(button=2)
        pan.connect('drag-begin', lambda *_: setattr(self, 'pan_start', self.pan))
        pan.connect('drag-update', self.pan_update)
        self.add_controller(pan)
        keyboard = Gtk.EventControllerKey()
        keyboard.connect('key-pressed', editor.canvas_key)
        self.add_controller(keyboard)
        pointer = Gtk.EventControllerMotion()
        pointer.connect('motion', self.track_pointer)
        pointer.connect('leave', lambda *_: setattr(self, 'pointer', None))
        self.add_controller(pointer)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect('scroll', self.scroll_zoom)
        self.add_controller(scroll)

    def track_pointer(self, _motion, x, y):
        self.pointer = (x, y)

    def scroll_zoom(self, controller, _dx, dy):
        if not dy or not (controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK):
            return False
        self.zoom_by(1.25 if dy < 0 else 1 / 1.25, self.pointer)
        return True

    def zoom_by(self, factor, anchor=None):
        width, height = self.get_width(), self.get_height()
        px, py = anchor if anchor is not None else (width / 2, height / 2)
        x, y, scale = self.viewport(width, height)
        paper_x, paper_y = (px - x) / scale, (py - y) / scale
        self.zoom = max(0.5, min(8.0, self.zoom * factor))
        x, y, scale = self.viewport(width, height)
        self.pan = (self.pan[0] + px - x - paper_x * scale,
                    self.pan[1] + py - y - paper_y * scale)
        self.editor.zoom_readout.set_label(f'{self.zoom:.0%}')
        self.queue_draw()

    def pan_update(self, _gesture, x, y):
        self.pan = self.pan_start[0] + x, self.pan_start[1] + y
        self.queue_draw()

    def fit_selection(self):
        indices = self.editor.selected_indices()
        if not indices:
            return
        box = editor_model.bounds(self.editor.project.shapes[i] for i in indices)
        width, height = self.get_width(), self.get_height()
        _, _, scale = self.viewport(width, height)
        margin = max(4.0, box.w * 0.08, box.h * 0.08)
        desired = min((width - 36) / (box.w + 2 * margin), (height - 36) / (box.h + 2 * margin))
        self.zoom = max(0.5, min(8.0, self.zoom * desired / scale))
        self.pan = (0.0, 0.0)
        left, top, scale = self.viewport(width, height)
        self.pan = (width / 2 - left - (box.x + box.w / 2) * scale,
                    height / 2 - top - (box.y + box.h / 2) * scale)
        self.editor.zoom_readout.set_label(f'{self.zoom:.0%}')
        self.queue_draw()

    def reveal_point(self, point):
        width, height = self.get_width(), self.get_height()
        if min(width, height) < 48:
            return
        left, top, scale = self.viewport(width, height)
        x, y = left + point[0] * scale, top + point[1] * scale
        self.pan = (self.pan[0] + min(width - 24, max(24, x)) - x,
                    self.pan[1] + min(height - 24, max(24, y)) - y)

    def viewport(self, width, height):
        project = self.editor.project
        mat = self.editor.mat_size if self.editor.show_mat.get_active() else None
        w, h = (max(a, b) for a, b in zip((project.width, project.height), mat or (0, 0)))
        scale = max(0.01, min((width - 92) / w, (height - 92) / h)) * self.zoom
        return ((width-w*scale)/2+self.pan[0], (height-h*scale)/2+self.pan[1], scale)

    def draw(self, _area, cr, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        cr.set_source_rgb(*(0.14, 0.15, 0.17) if dark else (0.83, 0.85, 0.87))
        cr.paint()
        editor = self.editor
        project = editor.project
        x, y, scale = self.viewport(width, height)
        w, h = project.width, project.height
        cr.translate(x, y)
        cr.scale(scale, scale)

        mat = editor.mat_size if editor.show_mat.get_active() else None
        if mat:
            self.draw_mat(cr, *mat, scale, editor.show_mat_grid.get_active())
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill_preserve()
        cr.set_source_rgb(0.68, 0.71, 0.74)
        cr.set_line_width(0.8 / scale)
        cr.stroke()
        if editor.print_border.get_active():
            margin = editor.print_margin.get_value()
            if 2 * margin < min(w, h):
                cr.rectangle(margin, margin, w - 2 * margin, h - 2 * margin)
                cr.set_source_rgb(0.26, 0.45, 0.63)
                cr.set_dash([2, 1])
                cr.stroke()
                cr.set_dash([])
        if editor.show_grid.get_active():
            spacing = editor.grid_spacing.get_value()
            spacing *= max(1, math.ceil(4 / (spacing * scale)))
            cr.set_source_rgb(0.89, 0.91, 0.93)
            _grid(cr, w, h, spacing)
        if editor.show_rulers.get_active():
            self.draw_rulers(cr, w, h, scale)
        if editor.show_cut_border.get_active() and (border := editor.cut_border):
            cr.rectangle(border.x, border.y, border.w, border.h)
            cr.set_source_rgba(0.81, 0.15, 0.15, 0.8)
            cr.set_line_width(1 / scale)
            cr.stroke()
        if (marks := editor.marks) is not None:
            self.draw_marks(cr, marks, scale)

        indices = editor.selected_indices()
        mode = editor.active_tool
        for i, shape in enumerate(project.shapes):
            if shape.visible:
                self.draw_shape(cr, shape, i in indices, scale)
        point_edit = mode == 'Point Edit' and len(indices) == 1
        if indices and not editor.drawing and not point_edit:
            box = editor_model.bounds(project.shapes[i] for i in indices)
            cr.rectangle(box.x, box.y, box.w, box.h)
            cr.set_source_rgba(0.16, 0.36, 0.73, 0.85)
            cr.set_line_width(1 / scale)
            cr.stroke()
        if mode in TRANSFORM_MODES:
            drag = editor.drag
            handle = rotation_handle(project.shapes, indices, scale)
            if handle:
                if mode in SCALE_MODES:
                    self.draw_scale_handles(cr, handle, scale)
                if mode in ROTATE_MODES:
                    self.draw_rotation_handle(cr, drag.rotation if drag and drag.rotation else handle,
                                              drag.angle if drag else 0, scale)
        if point_edit:
            shape = project.shapes[indices[0]]
            if path_edit.editable(shape):
                outline = path_edit.nodes(shape)
                drag = editor.drag
                chosen = drag.node[2] if drag and drag.node else editor.active_node(shape)
                self.draw_nodes(cr, outline, chosen, scale)
            elif shape.visible and shape.kind in editor_model.POINT_EDIT_KINDS:
                for path in shape.paths():
                    self.draw_vertices(cr, path.points, scale)
        if editor.polygon:
            points = editor.polygon
            cr.move_to(*points[0])
            handles = path_edit.from_clicks(points, closed=False).handles if editor.active_tool == "Curve" else ()
            for index, point in enumerate(points[1:], 1):
                if handles:
                    cr.curve_to(*handles[index - 1][1], *handles[index][0], *point)
                else:
                    cr.line_to(*point)
            cr.set_source_rgb(0.16, 0.36, 0.73)
            cr.set_line_width(1.3 / scale)
            cr.stroke()
            self.draw_vertices(cr, editor.polygon, scale)
        if (box := editor.marquee) is not None:
            cr.rectangle(box.x, box.y, box.w, box.h)
            cr.set_source_rgba(0.12, 0.38, 0.75, 0.15)
            cr.fill_preserve()
            cr.set_source_rgba(0.12, 0.38, 0.75, 0.85)
            cr.stroke()

    @staticmethod
    def draw_scale_handles(cr, handle, scale):
        half = 3.5 / scale
        cr.set_line_width(1.3 / scale)
        for x, y in handle.corners():
            cr.rectangle(x - half, y - half, 2 * half, 2 * half)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgb(0.20, 0.44, 0.78)
            cr.stroke()

    @staticmethod
    def draw_rotation_handle(cr, handle, angle, scale):
        edge, (x, y) = handle.positions(angle)
        cx, cy = handle.center
        tick = 4 / scale
        color = (0.20, 0.44, 0.78)
        cr.save()
        cr.set_source_rgb(*color)
        cr.set_line_width(1.5 / scale)
        draw_strokes(cr, ((edge, (x, y)), ((cx - tick, cy), (cx + tick, cy)),
                          ((cx, cy - tick), (cx, cy + tick))))
        cr.arc(x, y, 10 / scale, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1)
        cr.fill_preserve()
        cr.set_source_rgb(*color)
        cr.stroke()
        cr.translate(x, y)
        cr.scale(0.75 / scale, 0.75 / scale)
        cr.translate(-12, -12)
        cr.set_line_width(1.8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        draw_strokes(cr, TURN_RIGHT)
        cr.restore()

    @staticmethod
    def draw_mat(cr, width, height, scale, show_grid=True):
        pad = 15
        cr.set_source_rgb(0.61, 0.65, 0.68)
        cr.rectangle(-pad, -pad, width + 2 * pad, height + 2 * pad)
        cr.fill()
        cr.set_source_rgb(0.49, 0.53, 0.56)
        cr.set_line_width(1 / scale)
        cr.rectangle(0, 0, width, height)
        cr.stroke()
        if show_grid:
            cr.set_source_rgb(0.55, 0.59, 0.62)
            _grid(cr, width, height, max(25.4, 4/scale))
        draw_feed_arrow(cr, width, (0.32, 0.37, 0.41))

    @staticmethod
    def draw_rulers(cr, width, height, scale):
        cr.set_source_rgb(0.22, 0.24, 0.26)
        tick = 10 / scale
        cr.set_font_size(9 / scale)
        step = max(10, math.ceil(28 / (10 * scale)) * 10)
        for value in range(0, int(width) + 1, step):
            cr.move_to(value, 0)
            cr.line_to(value, -tick / 2)
            cr.stroke()
            cr.move_to(value + 2 / scale, -tick)
            cr.show_text(str(value))
        for value in range(step, int(height) + 1, step):
            cr.move_to(0, value)
            cr.line_to(-tick / 2, value)
            cr.stroke()
            label = str(value)
            cr.move_to(-tick / 2 - cr.text_extents(label).width - 2 / scale, value)
            cr.show_text(label)

    @staticmethod
    def draw_marks(cr, marks, scale):
        for zone in marks.corner_zones():
            cr.save()
            cr.rectangle(zone.x, zone.y, zone.w, zone.h)
            cr.clip()
            step = max(2.5, 4 / scale)
            cr.set_source_rgba(0.31, 0.34, 0.36, 0.27)
            cr.set_line_width(0.8 / scale)
            for i in range(-math.ceil(zone.h / step), math.ceil((zone.w + zone.h) / step)):
                x = zone.x + i * step
                cr.move_to(x, zone.y)
                cr.line_to(x + zone.h, zone.y2)
                cr.move_to(x, zone.y)
                cr.line_to(x - zone.h, zone.y2)
            cr.stroke()
            cr.restore()
        cr.set_source_rgb(0.05, 0.05, 0.05)
        if marks.square:
            square = marks.square
            cr.rectangle(square.x, square.y, square.w, square.h)
            cr.fill()
        cr.set_line_width(marks.thickness_mm)
        for mark in marks.ells:
            for a, b, c, d in mark.arms():
                cr.move_to(a, b)
                cr.line_to(c, d)
        cr.stroke()

    @staticmethod
    def draw_nodes(cr, outline, chosen, scale):
        blue = (0.20, 0.42, 0.80)
        for index in path_edit.adjacent_points(outline, chosen):
            if not outline.handles or not outline.handles[index]:
                continue
            point = outline.points[index]
            for handle in outline.handles[index][:2]:
                if handle == point:
                    continue
                cr.move_to(*point)
                cr.line_to(*handle)
                cr.set_source_rgb(*blue)
                cr.set_line_width(1 / scale)
                cr.stroke()
                cr.arc(*handle, 3 / scale, 0, math.tau)
                cr.fill()
        DesignCanvas.draw_vertices(cr, outline.points, scale)
        if chosen is not None:
            cr.arc(*outline.points[chosen], 5 / scale, 0, math.tau)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgb(*blue)
            cr.set_line_width(2 / scale)
            cr.stroke()

    @staticmethod
    def draw_vertices(cr, points, scale):
        size = 5 / scale
        for x, y in points:
            cr.rectangle(x - size/2, y - size/2, size, size)
        cr.set_source_rgb(0.16, 0.36, 0.73)
        cr.fill()

    @staticmethod
    def draw_shape(cr, shape, selected, scale):
        if shape.sticker:
            image = _sticker_surface(shape.sticker.png_bytes())
            (x, y), (rx, ry), _, (bx, by) = shape.frame().points
            cr.save()
            cr.transform(cairo.Matrix((rx - x) / image.get_width(), (ry - y) / image.get_width(),
                                      (bx - x) / image.get_height(), (by - y) / image.get_height(), x, y))
            cr.set_source_surface(image, 0, 0)
            cr.paint()
            cr.restore()
        paths = shape.paths()
        if not paths:
            return
        for path in paths:
            cr.move_to(*path.points[0])
            for point in path.points[1:]:
                cr.line_to(*point)
            if path.closed:
                cr.close_path()
        if paths[0].closed and shape.fill:
            cr.save()
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.set_source_rgb(*_rgb(shape.fill))
            cr.fill_preserve()
            cr.restore()
        color = _rgb(shape.stroke or {1: '#b2323a', 2: '#244b9a'}.get(shape.holder, '#384552'))
        cr.set_source_rgb(*color)
        line_width = shape.line_width_mm or (2 if selected else 1.25) / scale
        cr.set_line_width(line_width)
        if shape.line_style == 'dotted':
            cr.set_dash([line_width, 2 * line_width])
        elif shape.line_style == 'dashed' or shape.action == 'Score':
            cr.set_dash([3 * line_width, 2 * line_width])
        cr.stroke()
        cr.set_dash([])
