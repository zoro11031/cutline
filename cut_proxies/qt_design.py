"""Qt Design editor for the existing versioned Design projects."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import math
from math import dist
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPainterPath, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QSizePolicy, QSpinBox, QSplitter,
                               QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)

from . import (design_text, editor_model as model, geometry, graphtec, importer, path_edit, pixscan, regmarks,
               transforms,
               sticker_assets,
               sticker_print, sticker_trace, template)
from .design_tools import (ALIGNMENT_ROWS, CLICK_PATH_TOOLS, COMMAND_ICONS, DRAWING_TOOLS, OBJECT_ICONS, PANEL_ICONS,
                           TRANSFORM_ICONS, TURN_RIGHT, WORKSPACE_KEYS, WORKSPACE_TOOLS, rotation_handle,
                           scale_from_corner)
from .qt_canvas import SheetView, ZoomControls, draw_contours, draw_feed_arrow, draw_marks, draw_paper, draw_path, pen, rect
from .qt_controls import (ColorSwatch, DesignIconButton, PanelDock, Tasks, ToolStrip, bar, button, design_divider, design_icon,
                          icon_outline, set_choices, styled_label, workspace_header, workspace_layout)

DESIGN_FILTER = "Design projects (*.scc-template);;Cut templates (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
ACTION_LABELS = {"Cut": "Cut", "Score": "Score", "None": "None (not cut)"}
# One clipboard for every Design document, so an outline traced on one page
# can be pasted into a new one, as in Studio's template workflow.
_clipboard = ()


class TextBox(QPlainTextEdit):
    """Studio's text box: typing goes straight onto the page inside a green
    dashed frame. Escape or a click elsewhere finishes it."""

    finished = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("Text")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QPlainTextEdit { background: rgba(255, 255, 255, 215); "
                           "border: 1px dashed #2e9b4f; border-radius: 2px; }")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finished.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finished.emit()


@lru_cache(maxsize=8)
def _trace_preview(sticker, area, threshold, high_pass, smoothing):
    """Studio paints what Trace will outline in yellow; cached per setting."""
    mask = sticker_trace.trace_mask(model.Shape("Sticker", sticker=sticker), area=area, threshold=threshold,
                                    high_pass=high_pass, smoothing=smoothing)
    Image = sticker_assets.pillow()
    overlay = Image.new("RGBA", mask.size, (250, 214, 0, 150))
    overlay.putalpha(mask.point(lambda v: 150 if v else 0))
    from io import BytesIO
    data = BytesIO()
    overlay.save(data, "PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(data.getvalue(), "PNG")
    return pixmap


@lru_cache(maxsize=4)
def _sticker_pixmap(png):
    pixmap = QPixmap()
    pixmap.loadFromData(png, "PNG")
    return pixmap


class Canvas(SheetView):
    """The page and the tools that act on it, as Silhouette Studio's Design area."""

    def __init__(self, design):
        super().__init__()
        self.design = design
        self.setDragMode(self.DragMode.NoDrag)
        self.setAccessibleName("Design canvas")
        self.start = self.edit = self.anchor = self.pan = None
        self.polygon = []
        self.freehand = []
        self.stroke = []
        self.knife = None
        self.measure = None
        self.marquee = None
        self.draft = None
        self.active = ()
        self.rotation = None
        self.scale_anchor = None
        self.angle = 0
        self.node_point = None      # the selected point in Point Edit
        self.node_drag = None       # (base nodes, kind, index, side) while dragging a point or handle
        self.space = False
        self.dragging = False
        self.sampling = None        # "stroke" or "fill" while Pick from Canvas waits for a click
        self.rotation_centre = None  # Studio's centre of rotation (O), in page mm
        self.last_selected = None
        self.trace_area = None       # (left, top, right, bottom) in 0..1 of the selected image
        self.tracing_area = False
        self.pointer = None          # last pointer position, for crosshairs
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.rotation_icon = icon_outline(TURN_RIGHT)

    # ---- painting -----------------------------------------------------------
    def px(self, pixels):
        """A length in scene millimetres that looks `pixels` wide on screen."""
        return pixels / max(0.02, self.transform().m11())

    def rotation_handle(self):
        return rotation_handle(self.design.project.shapes, self.design.selection(), self.transform().m11())

    def radius_handle(self):
        """Studio's red control point on a selected rounded rectangle."""
        selection = self.design.selection()
        if len(selection) != 1:
            return None
        shape = self.design.project.shapes[selection[0]]
        if shape.kind != "Rounded rectangle" or not shape.visible:
            return None
        r = max(0.0, min(shape.radius, shape.width / 2, shape.height / 2))
        return shape._cut_path(((shape.x + r, shape.y),), False).points[0]

    def drawForeground(self, painter, area):
        super().drawForeground(painter, area)
        self.paint_guides(painter)
        tool = self.design.tool.currentData()
        if tool == "Point Edit":
            self.paint_nodes(painter)
            return
        if tool != "Select" or self.edit == "marquee":
            return
        handle = self.rotation_handle()
        if handle is None:
            return
        scale = self.transform().m11()
        outline = "#3271c8"
        painter.save()
        painter.setPen(pen(outline, 1.3 / scale, dashed=True))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect(handle.box))
        half = 3.5 / scale
        painter.setPen(pen(outline, 1.3 / scale))
        painter.setBrush(QColor("white"))
        for x, y in (*handle.corners(), *handle.sides()):
            painter.drawRect(QRectF(x - half, y - half, 2 * half, 2 * half))
        self.paint_rotation(painter, self.rotation or handle, scale, outline)
        radius = self.radius_handle()
        if radius:
            painter.setPen(pen("#be1e2d", 1.2 / scale))
            painter.setBrush(QColor("#e0313f"))
            painter.drawEllipse(QPointF(*radius), 3.2 / scale, 3.2 / scale)
        painter.restore()

    def paint_guides(self, painter):
        """Centre of rotation, crosshairs and edge rulers, over everything else."""
        scale = self.transform().m11()
        if self.rotation_centre:
            x, y = self.rotation_centre
            painter.save()
            painter.setPen(pen("#1f8a4c", 1.4 / scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), 5 / scale, 5 / scale)
            painter.drawLine(QPointF(x - 9 / scale, y), QPointF(x + 9 / scale, y))
            painter.drawLine(QPointF(x, y - 9 / scale), QPointF(x, y + 9 / scale))
            painter.restore()
        if self.design.crosshairs.isChecked() and self.pointer is not None:
            rect_ = self.sceneRect()
            painter.save()
            painter.setPen(pen("#3c70c5", 1 / scale, dashed=True))
            painter.drawLine(QPointF(rect_.left(), self.pointer.y()), QPointF(rect_.right(), self.pointer.y()))
            painter.drawLine(QPointF(self.pointer.x(), rect_.top()), QPointF(self.pointer.x(), rect_.bottom()))
            painter.restore()
        if self.design.rulers.isChecked():
            self.paint_rulers(painter)

    def paint_rulers(self, painter):
        """Edge rulers in millimetres, fixed to the view as in Photoshop and Studio."""
        painter.save()
        painter.resetTransform()
        view = self.viewport().rect()
        band = 18
        background, ink = self.palette().window().color(), self.palette().windowText().color()
        painter.fillRect(0, 0, view.width(), band, background)
        painter.fillRect(0, 0, band, view.height(), background)
        scale = self.transform().m11()
        step = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200) if s * scale >= 7)
        label_every = next(s for s in (5, 10, 20, 50, 100, 200, 500) if s * scale >= 45 and s % step == 0)
        left, top = self.mapToScene(0, 0).x(), self.mapToScene(0, 0).y()
        right, bottom = self.mapToScene(view.width(), view.height()).x(), self.mapToScene(view.width(), view.height()).y()
        painter.setPen(ink)
        font = painter.font()
        font.setPointSizeF(7)
        painter.setFont(font)
        for value in range(int(left // step) * step, int(right) + step, step):
            x = round(self.mapFromScene(QPointF(value, 0)).x())
            if x < band:
                continue
            long = value % label_every == 0
            painter.drawLine(x, band, x, band - (8 if long else 4))
            if long:
                painter.drawText(x + 2, 9, str(value))
        for value in range(int(top // step) * step, int(bottom) + step, step):
            y = round(self.mapFromScene(QPointF(0, value)).y())
            if y < band:
                continue
            long = value % label_every == 0
            painter.drawLine(band, y, band - (8 if long else 4), y)
            if long:
                painter.drawText(1, y - 2, str(value))
        if self.pointer is not None:
            painter.setPen(QColor("#d0202e"))
            at = self.mapFromScene(self.pointer)
            painter.drawLine(at.x(), 0, at.x(), band)
            painter.drawLine(0, at.y(), band, at.y())
        painter.setPen(self.palette().mid().color())
        painter.drawLine(0, band, view.width(), band)
        painter.drawLine(band, 0, band, view.height())
        painter.restore()

    def paint_rotation(self, painter, handle, scale, outline):
        edge, grip = (QPointF(*point) for point in handle.positions(self.angle))
        center = QPointF(*handle.center)
        painter.setPen(pen(outline, 1.5 / scale))
        painter.drawLine(edge, grip)
        for dx, dy in ((4 / scale, 0), (0, 4 / scale)):
            delta = QPointF(dx, dy)
            painter.drawLine(center - delta, center + delta)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(grip, 10 / scale, 10 / scale)
        painter.translate(grip)
        painter.scale(0.75 / scale, 0.75 / scale)
        painter.translate(-12, -12)
        painter.setPen(pen(outline, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.rotation_icon)

    def edited_nodes(self):
        """(index, nodes) of the shape in Point Edit, or None."""
        selection = self.design.selection()
        if len(selection) != 1:
            return None
        shape = self.design.project.shapes[selection[0]]
        if not path_edit.editable(shape):
            return None
        return selection[0], path_edit.nodes(shape)

    def paint_nodes(self, painter):
        """Studio's Point Editing view: grey points, the chosen one white, its
        segment in bold red, and blue handles on curved segments."""
        found = self.edited_nodes()
        if not found:
            return
        _, nodes = found
        scale = self.transform().m11()
        painter.save()
        chosen = self.node_point if self.node_point is not None and self.node_point < len(nodes.points) else None
        if chosen is not None and chosen in nodes.segments():
            a, c1, c2, b = nodes.curve(chosen)
            outline = QPainterPath(QPointF(*a))
            outline.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*b))
            painter.setPen(pen("#d0202e", 2.4 / scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline)
        count = len(nodes.points)
        near = set() if chosen is None else {chosen, (chosen - 1) % count, (chosen + 1) % count}
        for i in near:
            if not nodes.handles or not nodes.handles[i]:
                continue
            p = nodes.points[i]
            for q in nodes.handles[i][:2]:
                if q != p:
                    painter.setPen(pen("#3d7be0", 1 / scale))
                    painter.drawLine(QPointF(*p), QPointF(*q))
                    painter.setBrush(QColor("#3d7be0"))
                    painter.drawEllipse(QPointF(*q), 3 / scale, 3 / scale)
        half = 3.2 / scale
        for i, (x, y) in enumerate(nodes.points):
            painter.setPen(pen("#2a2f36", 1 / scale))
            painter.setBrush(QColor("white" if i == chosen else "#5b636c"))
            painter.drawRect(QRectF(x - half, y - half, 2 * half, 2 * half))
        painter.restore()

    def redraw(self):
        editor, scene = self.design, self.scene()
        project = editor.project
        page = QRectF(0, 0, project.width, project.height)
        mat = editor.mat_bounds()
        mat_visible = editor.show_mat.isChecked() and mat is not None
        sizes = page.united(mat) if mat_visible else page
        bounds = sizes.adjusted(-19, -19, 12, 12)
        refit = self.set_page_bounds(bounds)
        scene.clear()
        if mat_visible:
            scene.addRect(mat.adjusted(-8, -13, 8, 8), pen("#59616a"), QBrush(QColor("#a2aab3")))
            self.grid(scene, mat, 25.4, "#87939d")
            draw_feed_arrow(scene, mat.width(), "#535e68")
        draw_paper(scene, page, shadow=not mat_visible)
        if editor.print_border.isChecked():
            margin = editor.print_margin.value()
            print_area = page.adjusted(margin, margin, -margin, -margin)
            if not print_area.isEmpty():
                scene.addRect(print_area, pen("#4273a1", dashed=True))
        if editor.show_grid.isChecked():
            self.page_grid(scene, page)
        marks = project.registration.on_page(project.width, project.height) if project.registration else None
        if marks:
            draw_marks(scene, marks)
        if editor.cut_border.isChecked():
            width = min(project.width, editor.device.width_mm, mat.width() if mat else project.width)
            height = min(project.height, editor.device.length_mm, mat.height() if mat else project.height)
            border = QRectF(0, 0, width, height)
            if marks:
                border = border.intersected(QRectF(*marks.origin_mm, marks.reg_width_mm, marks.reg_length_mm))
            if not border.isEmpty():
                scene.addRect(border, pen("#be404a"))
        selection = [] if self.edit == "draw" else list(editor.selection())
        point_edit = editor.tool.currentData() == "Point Edit"
        for index, shape in enumerate(project.shapes):
            if not shape.visible:
                continue
            if shape.sticker:
                pixmap = _sticker_pixmap(shape.sticker.png_bytes())
                (x, y), (rx, ry), _, (bx, by) = shape.frame().points
                item = scene.addPixmap(pixmap)
                item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                item.setTransform(QTransform((rx - x) / pixmap.width(), (ry - y) / pixmap.width(),
                                             (bx - x) / pixmap.height(), (by - y) / pixmap.height(), x, y))
                if index in selection:
                    draw_path(scene, shape.frame(), pen("#2264aa", dashed=True))
                    if self.trace_preview_wanted(selection):
                        try:
                            overlay = _trace_preview(shape.sticker, self.trace_area, editor.trace_threshold.value(),
                                                     editor.trace_high_pass.isChecked(),
                                                     editor.trace_smoothing.value())
                        except ValueError:
                            overlay = None
                        if overlay is not None:
                            tint = scene.addPixmap(overlay)
                            tint.setTransform(QTransform((rx - x) / overlay.width(), (ry - y) / overlay.width(),
                                                         (bx - x) / overlay.height(), (by - y) / overlay.height(),
                                                         x, y))
            selected = index in selection and not (point_edit and path_edit.editable(shape))
            color = "#3c70c5" if selected else shape.stroke or {1: "#b2323a", 2: "#244b9a"}.get(shape.holder, "#313945")
            fill = QColor(shape.fill) if shape.closed and shape.fill else Qt.BrushStyle.NoBrush
            line = pen(color, shape.line_width_mm, dashed=shape.action == "Score" or shape.line_style == "dashed")
            if shape.line_style == "dotted":
                line.setStyle(Qt.PenStyle.DotLine)
            draw_contours(scene, shape.paths(), line, fill)
        if point_edit and len(selection) == 1:
            shape = project.shapes[selection[0]]
            if shape.kind in model.POINT_EDIT_KINDS and not path_edit.editable(shape) and shape.visible:
                dot = min(2.5, self.px(4))
                for path in shape.paths():
                    for x, y in path.points:
                        scene.addEllipse(x - dot / 2, y - dot / 2, dot, dot, pen("#2264aa"), QBrush(Qt.GlobalColor.white))
        if self.polygon:
            tool = editor.tool.currentData()
            preview = (self.preview_nodes() if tool == "Curve" else
                       path_edit.Nodes(tuple(self.polygon), (), False))
            if len(preview.points) >= 2:
                draw_path(scene, path_edit.to_shape(preview, model.Shape("Path")).path(), pen("#2264aa", dashed=True))
            dot = min(2.5, self.px(4))
            for x, y in self.polygon:
                scene.addEllipse(x - dot / 2, y - dot / 2, dot, dot, pen("#2264aa"), QBrush(Qt.GlobalColor.white))
        if self.knife:
            (x1, y1), (x2, y2) = self.knife
            scene.addLine(x1, y1, x2, y2, pen("#d0202e", self.px(1.6), dashed=True))
        if len(self.stroke) > 1:
            size = editor.eraser_size.value()
            brush = pen("#d0202e", size)
            brush.setCapStyle(Qt.PenCapStyle.RoundCap)
            brush.setColor(QColor(208, 32, 46, 70))
            outline = QPainterPath(QPointF(*self.stroke[0]))
            for point in self.stroke[1:]:
                outline.lineTo(QPointF(*point))
            scene.addPath(outline, brush)
        if self.measure:
            (x1, y1), (x2, y2) = self.measure
            line = pen("#c2410c", self.px(1.6))
            scene.addLine(x1, y1, x2, y2, line)
            tick = self.px(3)
            for x, y in self.measure:
                scene.addEllipse(x - tick / 2, y - tick / 2, tick, tick, line, QBrush(Qt.GlobalColor.white))
        if self.marquee:
            scene.addRect(self.marquee, pen("#3c70c5", dashed=True), QBrush(QColor(70, 140, 225, 45)))
        if refit:
            self.fit()

    def trace_preview_wanted(self, selection):
        editor = self.design
        return (len(selection) == 1 and hasattr(editor, "trace_preview") and editor.trace_preview.isChecked()
                and "trace" in editor.dock.shown() and editor.dock.panels["trace"].body.isVisible())

    def preview_nodes(self):
        return path_edit.from_clicks(self.polygon, closed=False)

    @staticmethod
    def grid(scene, box, spacing, color):
        brush = pen(color)
        for tick in range(1, int(box.width() / spacing) + 1):
            x = tick * spacing
            scene.addLine(x, 0, x, box.height(), brush)
        for tick in range(1, int(box.height() / spacing) + 1):
            y = tick * spacing
            scene.addLine(0, y, box.width(), y, brush)

    def page_grid(self, scene, page):
        """Studio's grid: spacing, divisions, square or isometric, in a chosen color."""
        editor = self.design
        spacing, divisions = editor.spacing.value(), editor.grid_divisions.value()
        major = QColor(editor.grid_color.color or "#dce1e5")
        minor = QColor(major)
        minor.setAlpha(90)
        if editor.grid_style.currentData() == "isometric":
            h = spacing * math.sqrt(3) / 2
            rows = int(page.height() / h) + 1
            for row in range(rows + 1):
                y = row * h
                if y <= page.height():
                    scene.addLine(0, y, page.width(), y, pen(major))
            slope = math.tan(math.radians(60))
            span = page.height() / slope
            k = -span
            while k <= page.width() + span:
                for direction in (1, -1):
                    x1, x2 = k, k + direction * span
                    scene.addLine(*self.clip_line((x1, 0), (x2, page.height()), page), pen(major))
                k += spacing
            return
        step = spacing / divisions
        count_x, count_y = int(page.width() / step), int(page.height() / step)
        for tick in range(1, count_x + 1):
            x = tick * step
            scene.addLine(x, 0, x, page.height(), pen(major if tick % divisions == 0 else minor))
        for tick in range(1, count_y + 1):
            y = tick * step
            scene.addLine(0, y, page.width(), y, pen(major if tick % divisions == 0 else minor))

    @staticmethod
    def clip_line(a, b, box):
        """A line clipped to the page (Liang-Barsky), as four coordinates."""
        (x1, y1), (x2, y2) = a, b
        dx, dy = x2 - x1, y2 - y1
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, x1 - box.left()), (dx, box.right() - x1), (-dy, y1 - box.top()), (dy, box.bottom() - y1)):
            if p == 0:
                if q < 0:
                    return 0, 0, 0, 0
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
        if t0 > t1:
            return 0, 0, 0, 0
        return x1 + dx * t0, y1 + dy * t0, x1 + dx * t1, y1 + dy * t1

    # ---- pointer helpers ------------------------------------------------------
    def point(self, point):
        x, y = point.x(), point.y()
        if self.design.snap.isChecked():
            step = self.design.spacing.value()
            x, y = round(x / step) * step, round(y / step) * step
        return round(x, 4), round(y, 4)

    def shape_at(self, position, *, group=True):
        shapes = self.design.project.shapes
        tolerance = self.px(5)
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if shape.visible and model.hit(shape, (position.x(), position.y()), tolerance):
                return index
        return None

    def select_at(self, position, shift=False, group=True):
        shapes = self.design.project.shapes
        index = self.shape_at(position)
        if index is not None:
            self.last_selected = index
        if index is not None and (group or shapes[index].kind in model.POINT_EDIT_KINDS
                                  or path_edit.editable(shapes[index])):
            members = set(model.group_members(shapes, index)) if group else {index}
            selected = set(self.design.selection())
            if shift:
                self.design.select(selected - members if members <= selected else selected | members)
            elif not members <= selected or not group:
                self.design.select(members)
            return True
        if not shift:
            self.design.select(())
        return False

    @staticmethod
    def constrain(start, end):
        """Shift: the nearest 45° direction from `start`."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return round(start[0] + length * math.cos(angle), 4), round(start[1] + length * math.sin(angle), 4)

    # ---- mouse --------------------------------------------------------------
    def set_rotation_centre(self, on):
        if on:
            selection = self.design.selection()
            if selection:
                box = model.bounds(self.design.project.shapes[i] for i in selection)
                self.rotation_centre = (box.x + box.w / 2, box.y + box.h / 2)
            else:
                project = self.design.project
                self.rotation_centre = (project.width / 2, project.height / 2)
        else:
            self.rotation_centre = None
        self.viewport().update()

    def begin_trace_area(self):
        self.tracing_area = True
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.design.message("Drag over the part of the image to trace.")

    def sample_color(self, channel):
        self.sampling = channel
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.design.message("Click the object whose color you want.")

    def mousePressEvent(self, event):
        editor = self.design
        tool = editor.tool.currentData()
        if self.tracing_area and event.button() == Qt.MouseButton.LeftButton:
            self.start = self.mapToScene(event.position().toPoint())
            self.anchor = event.position().toPoint()
            self.edit = "trace-area"
            event.accept()
            return
        if self.sampling and event.button() == Qt.MouseButton.LeftButton:
            channel, self.sampling = self.sampling, None
            editor.change_cursor()
            index = self.shape_at(self.mapToScene(event.position().toPoint()))
            if index is None:
                editor.message("No object there; the color is unchanged.")
            else:
                source = editor.project.shapes[index]
                editor.change_selected(**{channel: getattr(source, channel)})
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton or (
                event.button() == Qt.MouseButton.LeftButton and (self.space or tool == "Hand")):
            self.pan = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        self.start = self.mapToScene(event.position().toPoint())
        self.anchor = event.position().toPoint()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if tool in CLICK_PATH_TOOLS:
            point = self.point(self.start)
            if shift and self.polygon and tool == "Polygon":
                point = self.constrain(self.polygon[-1], point)
            if len(self.polygon) >= 3 and dist(point, self.polygon[0]) <= self.px(6):
                self.finish_polygon(closed=True)
            elif not self.polygon or self.polygon[-1] != point:
                self.polygon.append(point)
            self.start = None
            self.redraw()
        elif tool == "Point Edit":
            self.begin_node_edit()
        elif tool == "Select":
            self.begin_selection(shift)
        elif tool == "Type":
            point, self.start = self.start, None
            editor.type_at(point)
        elif tool == "Measure":
            self.edit = "measure"
            self.measure = None
        elif tool == "Eyedropper":
            index = self.shape_at(self.start)
            self.start = None
            editor.transfer_properties(index)
        elif tool == "Zoom":
            self.start = None
            target = self.mapToScene(event.position().toPoint())
            self.zoom_by(0.8 if alt else 1.25)
            self.centerOn(target)
        elif tool == "Knife":
            self.edit = "knife"
            self.knife = None
        elif tool == "Eraser":
            self.edit = "erase"
            self.stroke = [(self.start.x(), self.start.y())]
        else:
            self.edit = "draw"
            self.freehand = [self.point(self.start)]
        event.accept()

    def begin_selection(self, shift):
        """Studio's selection box: the round grip rotates, a corner scales
        proportionally, a side stretches, the red point sets corner radius,
        and inside moves."""
        point = (self.start.x(), self.start.y())
        handle = None if shift else self.rotation_handle()
        radius = None if shift else self.radius_handle()
        scale_anchor = handle.opposite_corner(point) if handle else None
        side = handle.side_at(point) if handle else None
        if self.rotation_centre and math.dist(self.rotation_centre, point) <= self.px(7):
            self.edit = "centre"
        elif radius and math.dist(radius, point) <= self.px(6):
            self.edit = "radius"
        elif handle and handle.hit(point):
            self.edit, self.rotation = "rotate", handle
        elif scale_anchor is not None:
            self.edit, self.scale_anchor = "scale", scale_anchor
        elif side is not None:
            self.edit, self.scale_anchor = "stretch", side
        elif not self.select_at(self.start, shift):
            self.edit = "marquee"
        else:
            self.edit = "move"
        self.active = self.design.selection()
        if self.edit in ("rotate", "scale", "stretch", "radius", "centre"):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def begin_node_edit(self):
        """Studio's Point Editing: grab a point or a handle, click the outline
        to add a point, or click another shape to edit that one."""
        editor = self.design
        position = (self.start.x(), self.start.y())
        found = self.edited_nodes()
        if found:
            index, nodes = found
            distance, kind, i, side = path_edit.nearest_node(nodes, position)
            if distance <= self.px(7):
                self.node_point = i
                self.node_drag = (nodes, kind, i, side)
                self.edit = "node"
                self.active = (index,)
                self.redraw()
                return
            try:
                added, i = path_edit.insert_point(nodes, position, self.px(5))
            except ValueError:
                pass
            else:
                editor.commit_shapes(self.replaced(index, path_edit.to_shape(added, editor.project.shapes[index])),
                                     (index,))
                self.node_point = i
                self.node_drag = (added, "point", i, None)
                self.edit = "node"
                self.active = (index,)
                return
        selected = editor.selection()
        if len(selected) == 1 and not found:
            shape = editor.project.shapes[selected[0]]
            if shape.kind in model.POINT_EDIT_KINDS and shape.visible:
                distance, vertex = model.nearest_vertex(shape, position)
                if distance <= self.px(7):
                    self.active = (selected[0], vertex)
                    self.edit = "point"
                    return
        self.node_point = None
        self.select_at(self.start, group=False)
        self.start = None

    def replaced(self, index, shape, shapes=None):
        shapes = self.design.project.shapes if shapes is None else shapes
        return shapes[:index] + (shape,) + shapes[index + 1:]

    def mouseMoveEvent(self, event):
        self.pointer = self.mapToScene(event.position().toPoint())
        if self.design.crosshairs.isChecked() or self.design.rulers.isChecked():
            self.viewport().update()
        if self.pan is not None:
            delta = event.position() - self.pan
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            self.pan = event.position()
            return
        if self.start is not None:
            self.update_from_pointer(event)

    def update_from_pointer(self, event):
        position = event.position().toPoint()
        # The 2-pixel threshold only decides when a drag begins; after that
        # every movement counts, including a return to the starting point.
        if self.dragging or (self.anchor - position).manhattanLength() >= 2 or (
                self.edit in ("rotate", "scale", "stretch") and self.draft is not None):
            self.dragging = True
            modifiers = event.modifiers()
            self.update_drag(self.mapToScene(position), snap=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
                             alt=bool(modifiers & Qt.KeyboardModifier.AltModifier))

    def update_drag(self, point, *, snap=False, alt=False):
        base = self.design.history.current.shapes
        delta = point - self.start
        if self.edit == "centre":
            self.rotation_centre = self.point(point)
            self.viewport().update()
            return
        if self.edit == "trace-area":
            self.marquee = QRectF(self.start, point).normalized()
            self.redraw()
            return
        if self.edit == "rotate" and self.rotation:
            centre = self.rotation_centre or self.rotation.center
            if self.rotation_centre:
                turn = math.degrees(math.atan2(point.y() - centre[1], point.x() - centre[0])
                                    - math.atan2(self.start.y() - centre[1], self.start.x() - centre[0]))
                angle = 15 * round(turn / 15) if snap else round(turn, 3)
            else:
                angle = self.rotation.angle((self.start.x(), self.start.y()), (point.x(), point.y()), snap=snap)
            if angle is not None:
                try:
                    self.draft = model.rotate_selection(base, self.active, angle, center=centre)
                except ValueError:
                    self.draft, self.angle = None, 0
                else:
                    self.angle = angle
        elif self.edit == "scale":
            factor = scale_from_corner((self.start.x(), self.start.y()), (point.x(), point.y()), self.scale_anchor)
            try:
                self.draft = model.scale_selection(base, self.active, self.scale_anchor, factor)
            except ValueError:
                self.draft = None
        elif self.edit == "stretch":
            anchor, axis = self.scale_anchor
            reach = (self.start.x(), self.start.y())[axis] - anchor[axis]
            factor = ((point.x(), point.y())[axis] - anchor[axis]) / reach if reach else 1
            try:
                self.draft = path_edit.stretch_selection(base, self.active, anchor, axis, factor)
            except ValueError:
                self.draft = None
        elif self.edit == "radius":
            index = self.active[0]
            shape = base[index]
            local = shape.local_point((point.x(), point.y()))
            radius = max(0.0, min(local[0] - shape.x, shape.width / 2, shape.height / 2))
            self.draft = self.replaced(index, replace(shape, radius=round(radius, 3)), base)
        elif self.edit == "move" and self.active:
            anchor = base[self.active[0]]
            x, y = self.point(QPointF(anchor.x + delta.x(), anchor.y + delta.y()))
            dx, dy = x - anchor.x, y - anchor.y
            if self.design.smart_snap.isChecked():
                project = self.design.project
                dx, dy = model.snap_move(base, self.active, dx, dy, project.width, project.height, self.px(6))
            self.draft = model.move(base, self.active, dx, dy)
        elif self.edit == "marquee":
            self.marquee = QRectF(self.start, point).normalized()
        elif self.edit == "measure":
            self.measure = ((self.start.x(), self.start.y()), (point.x(), point.y()))
            self.design.measured()
        elif self.edit == "knife":
            end = (point.x(), point.y())
            start = (self.start.x(), self.start.y())
            self.knife = (start, self.constrain(start, end) if snap else end)
        elif self.edit == "erase":
            self.stroke.append((point.x(), point.y()))
        elif self.edit == "node":
            nodes, kind, i, side = self.node_drag
            target = self.point(point)
            moved = (path_edit.move_point(nodes, i, target) if kind == "point"
                     else path_edit.move_handle(nodes, i, side, target))
            index = self.active[0]
            try:
                self.draft = self.replaced(index, path_edit.to_shape(moved, base[index]), base)
            except ValueError:
                self.draft = None
        elif self.edit == "point":
            index, vertex = self.active
            try:
                shape = model.move_vertex(base[index], vertex, self.point(point))
            except ValueError:
                self.draft = None
            else:
                self.draft = self.replaced(index, shape, base)
        elif self.edit == "draw":
            try:
                shape = self.shape_between(self.point(self.start), self.point(point), shift=snap, alt=alt)
            except ValueError:
                shape = None
            self.draft = base + (self.design.styled(shape),) if shape else None
        self.redraw()

    def shape_between(self, start, end, *, shift=False, alt=False, final=False):
        tool = self.design.tool.currentData()
        if tool in ("Freehand", "Smooth Freehand"):
            if end != self.freehand[-1]:
                self.freehand.append(end)
            if len(self.freehand) < 2:
                return None
            points = tuple(self.freehand)
            closed = final and len(points) > 3 and dist(points[0], points[-1]) <= self.px(8)
            if closed:
                points = points[:-1]
            if tool == "Smooth Freehand":
                simple = model.simplified_points(points, max(0.3, self.px(2)), closed=closed)
                nodes = path_edit.from_clicks(simple, closed=closed and len(simple) >= 3)
            else:
                nodes = path_edit.Nodes(points, (), closed and len(set(points)) >= 3)
            return path_edit.to_shape(nodes, model.Shape("Path"))
        if tool == "Line" and shift:
            end = self.constrain(start, end)
        dx, dy = end[0] - start[0], end[1] - start[1]
        if tool in ("Rectangle", "Rounded rectangle", "Ellipse", "Regular Polygon") and shift:
            side = max(abs(dx), abs(dy))
            dx, dy = math.copysign(side, dx or 1), math.copysign(side, dy or 1)
        if max(map(abs, (*start, *end, dx, dy))) > model.SHAPE_LIMIT:
            return None
        if tool == "Line":
            return model.Shape(tool, *start, dx, dy) if dx or dy else None
        if tool == "Arc":
            if min(abs(dx), abs(dy)) < 0.1:
                return None
            return path_edit.to_shape(path_edit.arc(start, (start[0] + dx, start[1] + dy)), model.Shape("Path"))
        if alt:
            left, top, width, height = start[0] - abs(dx), start[1] - abs(dy), 2 * abs(dx), 2 * abs(dy)
        else:
            left, top, width, height = min(start[0], start[0] + dx), min(start[1], start[1] + dy), abs(dx), abs(dy)
        if min(width, height) < 0.1:
            return None
        if tool == "Regular Polygon":
            nodes = path_edit.regular_polygon(left, top, width, height, self.design.sides.value())
            return path_edit.to_shape(nodes, model.Shape("Path"))
        return model.Shape(tool, left, top, width, height)

    def mouseReleaseEvent(self, event):
        if self.pan is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self.pan = None
            if self.space or self.design.tool.currentData() == "Hand":
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return super().mouseReleaseEvent(event)
        self.update_from_pointer(event)
        editor = self.design
        tool = editor.tool.currentData()
        if self.edit == "draw" and tool in ("Freehand", "Smooth Freehand") and self.draft:
            final = self.shape_between(self.point(self.start), self.freehand[-1], final=True)
            if final:
                self.draft = editor.history.current.shapes + (editor.styled(final),)
        if self.edit == "trace-area":
            self.finish_trace_area()
            return
        if self.edit == "centre":
            self.cancel()
            self.viewport().update()
            return
        if self.marquee is not None:
            selected = set(self.active)
            for index, shape in enumerate(editor.project.shapes):
                if shape.visible and self.marquee.contains(rect(model.bounds((shape,)))):
                    selected.update(model.group_members(editor.project.shapes, index))
            editor.select(selected)
        if self.edit == "knife" and self.knife:
            line = self.knife
            self.cancel()
            editor.knife(*line)
            return
        if self.edit == "erase":
            stroke = list(self.stroke)
            self.cancel()
            editor.erase_stroke(stroke)
            return
        shapes = self.draft
        if self.edit == "node" and shapes is not None:
            index = self.active[0]
            nodes = path_edit.nodes(shapes[index])
            joined = path_edit.join_ends(nodes, self.px(6)) if self.node_drag[1] == "point" and \
                self.node_drag[2] in (0, len(self.node_drag[0].points) - 1) else None
            if joined:
                shapes = self.replaced(index, path_edit.to_shape(joined, shapes[index]), shapes)
                self.node_point = 0
        selection = (len(shapes) - 1,) if shapes and self.edit == "draw" else editor.selection()
        self.cancel()
        if shapes is not None:
            editor.commit_shapes(shapes, selection)
        else:
            self.redraw()

    def mouseDoubleClickEvent(self, event):
        editor = self.design
        tool = editor.tool.currentData()
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        position = self.mapToScene(event.position().toPoint())
        if tool in CLICK_PATH_TOOLS:
            # Studio: double-clicking stops drawing, leaving the path open.
            if len(self.polygon) >= 2:
                self.finish_polygon(closed=False)
        elif tool == "Select":
            index = self.shape_at(position)
            shape = editor.project.shapes[index] if index is not None else None
            if shape is not None and shape.text:
                self.cancel()
                editor.select((index,))
                editor.begin_text(index=index)
            elif shape is not None and (path_edit.editable(shape) or shape.kind in model.POINT_EDIT_KINDS):
                self.cancel()
                editor.select((index,))
                editor.tool.setCurrentText("Point Edit")
        elif tool == "Point Edit":
            if self.shape_at(position) is None:
                self.cancel()
                editor.tool.setCurrentText("Select")
        else:
            super().mouseDoubleClickEvent(event)

    def finish_trace_area(self):
        box, self.marquee = self.marquee, None
        self.tracing_area = False
        self.cancel()
        self.design.change_cursor()
        selection = self.design.selection()
        shape = self.design.project.shapes[selection[0]] if len(selection) == 1 else None
        if not shape or not shape.sticker or box is None:
            self.design.message("Select one placed image, then drag over the part to trace.")
            self.redraw()
            return
        corners = [shape.local_point((box.left(), box.top())), shape.local_point((box.right(), box.bottom()))]
        u = sorted(max(0.0, min(1.0, (x - shape.x) / shape.width)) for x, _ in corners)
        v = sorted(max(0.0, min(1.0, (y - shape.y) / shape.height)) for _, y in corners)
        self.trace_area = (u[0], v[0], u[1], v[1])
        self.redraw()

    def finish_polygon(self, closed=False):
        points = tuple(self.polygon)
        if len(set(points)) < (3 if closed else 2):
            self.design.message("Place three or more points to close the outline." if closed
                                else "Place two or more points to draw a line.")
            return
        curve = self.design.tool.currentData() == "Curve"
        nodes = path_edit.from_clicks(points, closed=closed) if curve else path_edit.Nodes(points, (), closed)
        try:
            shape = path_edit.to_shape(nodes, model.Shape("Path"))
        except ValueError as error:
            self.design.message(str(error))
            return
        self.polygon.clear()
        self.design.add_shape(shape)

    def cancel(self):
        if self.edit in ("rotate", "scale", "stretch", "radius", "centre"):
            self.unsetCursor()
        self.rotation, self.angle = None, 0
        self.scale_anchor = None
        self.start = self.edit = self.anchor = self.marquee = self.draft = self.knife = None
        self.node_drag = None
        self.dragging = False
        self.freehand.clear()
        self.stroke = []

    # ---- keys and menus ---------------------------------------------------------
    def keyPressEvent(self, event):
        editor = self.design
        tool = editor.tool.currentData()
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if event.key() == Qt.Key.Key_Escape:
            transforming = self.edit in ("rotate", "scale", "stretch", "radius", "node")
            self.cancel()
            self.polygon.clear()
            if self.measure:
                self.measure = None
                self.design.measured()
                self.redraw()
                return
            if transforming:
                self.redraw()
            elif tool == "Point Edit":
                editor.tool.setCurrentText("Select")
            else:
                editor.select(())
            return
        if self.edit in ("rotate", "scale", "stretch", "radius", "node"):
            return
        if tool in CLICK_PATH_TOOLS and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_polygon(closed=False)
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in arrows and editor.selection():
            x, y = arrows[event.key()]
            distance = editor.spacing.value() if editor.snap.isChecked() else editor.nudge_step()
            editor.commit_shapes(model.move(editor.project.shapes, editor.selection(),
                                            x * distance, y * distance), editor.selection())
            return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() == Qt.Key.Key_O:
            editor.centre_check.toggle()
            return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() in WORKSPACE_KEYS:
            editor.tool.setCurrentIndex(WORKSPACE_KEYS[event.key()])
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space = False
            if self.design.tool.currentData() == "Hand":
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.design.change_cursor()
            return
        super().keyReleaseEvent(event)

    def contextMenuEvent(self, event):
        """Studio's right-click menus: point actions in Point Edit, object actions elsewhere."""
        editor = self.design
        position = self.mapToScene(event.pos())
        menu = QMenu(self)
        found = self.edited_nodes() if editor.tool.currentData() == "Point Edit" else None
        if found:
            _, nodes = found
            distance, kind, i, _side = path_edit.nearest_node(nodes, (position.x(), position.y()))
            if kind == "point" and distance <= self.px(7):
                self.node_point = i
                self.redraw()
                for label in editor.POINT_ACTIONS:
                    menu.addAction(label, lambda name=label: editor.point_action(name))
                menu.exec(event.globalPos())
                return
        index = self.shape_at(position)
        if index is not None and index not in editor.selection():
            editor.select(set(model.group_members(editor.project.shapes, index)))
        editor.fill_context_menu(menu)
        menu.exec(event.globalPos())


class Design(QWidget):
    def __init__(self, project, device, open_send, review):
        super().__init__()
        self.history = model.History(project)
        self.saved = project
        self.path = None
        self.source = None
        self.device = device
        self.loading = False
        self.on_review = review
        self.last_marks = project.registration or model.Registration.from_marks(
            regmarks.marks(project.width, project.height))
        self.setWindowTitle("Design")
        self.setObjectName("workspace")

        body = workspace_layout(self)
        # Photoshop and GIMP run their panels edge to edge.
        body.setContentsMargins(6, 4, 6, 6)
        body.setSpacing(4)
        header, self.menu_bar, self.design_workspace, self.send_workspace = workspace_header(
            self, lambda: self.canvas.setFocus(), open_send, active="Design")
        body.addWidget(header)
        new = self.command("New", self.new_project, QKeySequence.StandardKey.New)
        open_file = self.command("Open…", self.open_project, QKeySequence.StandardKey.Open)
        self.save_action = self.command("Save", self.save, QKeySequence.StandardKey.Save)
        save_as = self.command("Save As…", lambda: self.save(as_new=True), QKeySequence.StandardKey.SaveAs)
        import_art = self.command("Import Artwork…", self.import_art)
        self.undo_action = self.command("Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self.command("Redo", self.redo, QKeySequence.StandardKey.Redo)

        # Photoshop's arrangement: the toolbox picks what the mouse does, the
        # options bar holds that tool's settings, Properties holds the
        # selection's, and View holds what the canvas shows.
        self.tool = ToolStrip(changed=self.change_tool)
        self.canvas = Canvas(self)
        self.canvas.setMinimumWidth(220)
        self.view_options()
        self.options_bar = self.tool_options_bar()
        body.addWidget(self.options_bar)

        workspace = QHBoxLayout()
        workspace.setSpacing(4)
        workspace.addWidget(self.tool)
        panes = QSplitter(Qt.Orientation.Horizontal)
        panes.setChildrenCollapsible(False)
        panes.setHandleWidth(4)
        workspace.addWidget(panes, 1)
        body.addLayout(workspace, 1)
        self.export_tasks = Tasks(self)
        self.exporting_stickers = False
        panes.addWidget(self.document_frame())

        panes.addWidget(self.build_dock())
        panes.setStretchFactor(0, 1)
        panes.setStretchFactor(1, 0)
        panes.setSizes([860, 290])
        self.build_design_menus((new, open_file, self.save_action, save_as, import_art))

        footer_bar, footer = bar(self)
        self.status = styled_label(role="body")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.zoom = ZoomControls(self.canvas, fit_button=self.icon_button("fit", self.canvas.fit))
        footer.addWidget(self.zoom)
        # As in Silhouette Studio, the Send switch sends the design; only a
        # sticker sheet has a step of its own, its Print & Cut export.
        self.review_button = button("Export Print && Cut…", self.export_stickers, primary=True)
        footer.addWidget(self.review_button)
        body.addWidget(footer_bar)

        for label, key, callback in (("Group", "Ctrl+G", self.group),
                                    ("Ungroup", "Ctrl+Shift+G", lambda: self.group(release=True))):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(lambda checked=False, call=callback: call())
            self.canvas.addAction(action)
        self.change_tool()
        self.refresh((0,) if project.shapes else ())

    @staticmethod
    def icon_button(icon, callback):
        widget = DesignIconButton(COMMAND_ICONS[icon] if isinstance(icon, str) else icon)
        widget.clicked.connect(lambda _checked: callback())
        return widget

    def view_options(self):
        """What the canvas shows lives in View, as Photoshop's Extras do."""
        def toggle(label, checked, tip=""):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(checked)
            action.setToolTip(tip or label)
            action.toggled.connect(lambda _on: self.canvas.redraw())
            return action

        self.show_grid = toggle("Grid", True)
        self.rulers = toggle("Rulers", False)
        self.crosshairs = toggle("Crosshairs", False, "Lines through the pointer across the whole page.")
        self.show_mat = toggle("Cutting Mat", True)
        self.cut_border = toggle("Cut Border", True)
        self.print_border = toggle("Print Border", False, "Show the margin your printer cannot reach.")
        self.snap = toggle("Snap to Grid", False, "Snap drawn points and object moves to the grid spacing.")
        self.smart_snap = toggle("Smart Snapping", False, "Line up moving shapes with nearby edges or centers.")


    def tool_options_bar(self):
        options, row = bar(self, (10, 3, 8, 3))
        options.setObjectName("designToolOptions")
        options.setAccessibleName("Tool options")
        row.setSpacing(8)
        row.addWidget(self.tool.active_label)
        row.addWidget(design_divider(vertical=True))

        self.draw_options = QWidget()
        draw = QHBoxLayout(self.draw_options)
        draw.setContentsMargins(0, 0, 0, 0)
        draw.setSpacing(6)
        self.new_action = QComboBox()
        set_choices(self.new_action, ACTION_LABELS, "Cut")
        self.new_action.setAccessibleName("Action for new objects")
        self.new_action.setToolTip("What the cutter does with the objects this tool makes.")
        caption = QLabel("Action")
        caption.setBuddy(self.new_action)
        draw.addWidget(caption)
        draw.addWidget(self.new_action)
        self.radius_option = QWidget()
        radius = QHBoxLayout(self.radius_option)
        radius.setContentsMargins(6, 0, 0, 0)
        radius.setSpacing(6)
        self.new_radius = QDoubleSpinBox(decimals=2, minimum=0, maximum=100, singleStep=0.5, value=3, suffix=" mm")
        self.new_radius.setAccessibleName("Corner radius for new rectangles")
        caption = QLabel("Corner radius")
        caption.setBuddy(self.new_radius)
        radius.addWidget(caption)
        radius.addWidget(self.new_radius)
        draw.addWidget(self.radius_option)
        self.sides_option = QWidget()
        sides = QHBoxLayout(self.sides_option)
        sides.setContentsMargins(6, 0, 0, 0)
        sides.setSpacing(6)
        self.sides = QSpinBox(minimum=3, maximum=64, value=5)
        self.sides.setAccessibleName("Sides")
        caption = QLabel("Sides")
        caption.setBuddy(self.sides)
        sides.addWidget(caption)
        sides.addWidget(self.sides)
        draw.addWidget(self.sides_option)
        self.close_polygon = button("Close Shape", lambda: self.canvas.finish_polygon(closed=True))
        self.close_polygon.setToolTip("Close the outline. Double-click or Enter stops with it open.")
        draw.addWidget(self.close_polygon)
        row.addWidget(self.draw_options)

        self.eraser_options = QWidget()
        eraser = QHBoxLayout(self.eraser_options)
        eraser.setContentsMargins(0, 0, 0, 0)
        eraser.setSpacing(6)
        self.eraser_size = QDoubleSpinBox(decimals=1, minimum=0.2, maximum=50, singleStep=0.5, value=3, suffix=" mm")
        self.eraser_size.setAccessibleName("Eraser size")
        caption = QLabel("Size")
        caption.setBuddy(self.eraser_size)
        eraser.addWidget(caption)
        eraser.addWidget(self.eraser_size)
        row.addWidget(self.eraser_options)

        # Studio's Point Editing toolbar, shown while Point Edit is the tool.
        self.point_options = QWidget()
        points = QHBoxLayout(self.point_options)
        points.setContentsMargins(0, 0, 0, 0)
        points.setSpacing(3)
        self.point_buttons = {}
        for label in self.POINT_ACTIONS:
            widget = button(label, lambda name=label: self.point_action(name))
            widget.setMinimumHeight(26)
            self.point_buttons[label] = widget
            points.addWidget(widget)
        row.addWidget(self.point_options)

        self.measure_options = QWidget()
        measure = QHBoxLayout(self.measure_options)
        measure.setContentsMargins(0, 0, 0, 0)
        measure.setSpacing(8)
        self.measure_label = styled_label("No measurement", role="body")
        self.measure_label.setAccessibleName("Measurement")
        self.scale_to_button = button("Scale Selection to Length…", self.scale_to_length)
        self.scale_to_button.setToolTip("Scale the selected objects so the measured distance has the length you enter.")
        measure.addWidget(self.measure_label)
        measure.addWidget(self.scale_to_button)
        row.addWidget(self.measure_options)

        self.tool_hint = styled_label(role="caption")
        self.tool_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.tool_hint, 1)
        self.option_snap = DesignIconButton(COMMAND_ICONS["snap"], checkable=True)
        self.option_snap.setChecked(self.snap.isChecked())
        self.option_snap.toggled.connect(self.snap.setChecked)
        self.snap.toggled.connect(self.option_snap.setChecked)
        row.addWidget(self.option_snap)
        return options

    def document_frame(self):
        frame = QFrame()
        frame.setObjectName("designDocument")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.document_tab = QTabBar()
        self.document_tab.setProperty("role", "document")
        self.document_tab.setDrawBase(False)
        self.document_tab.setExpanding(False)
        self.document_tab.setAccessibleName("Open design")
        self.document_tab.addTab("Untitled")
        layout.addWidget(self.document_tab)
        layout.addWidget(self.canvas, 1)
        return frame

    def command(self, label, callback, shortcut=None):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.addAction(action)
        action.triggered.connect(lambda checked=False: callback())
        return action

    @property
    def project(self):
        if hasattr(self, "canvas") and self.canvas.draft is not None:
            return replace(self.history.current, shapes=self.canvas.draft)
        return self.history.current

    def mat_bounds(self):
        project = self.project
        key = project.mat
        if key == "auto" and project.registration:
            commands = graphtec.registration_mat_commands(self.device, project.width, project.height)
            if commands:
                key = next((key for key, spec in graphtec.MATS.items() if spec[0] == commands[0]), key)
        if key in graphtec.MATS:
            return QRectF(0, 0, *graphtec.MATS[key][1:])
        return None

    def build_dock(self):
        """Silhouette Studio's panels, docked and stacked as Photoshop's are:
        an icon strip on the outer edge opens each one."""
        self.dock = dock = PanelDock()
        dock.setMinimumWidth(250)
        for key, title, build, shown in (
                ("page", "Page Setup", self.page_panel, False),
                ("transform", "Transform", self.transform_panel, True),
                ("align", "Align", self.align_panel, False),
                ("replicate", "Replicate", self.replicate_panel, False),
                ("modify", "Modify", self.modify_panel, False),
                ("offset", "Offset", self.offset_panel, False),
                ("trace", "Trace", self.trace_panel, False),
                ("nesting", "Nesting", self.nesting_panel, False),
                ("text", "Text Style", self.text_panel, False),
                ("line", "Line and Fill", self.style_panel, True),
                ("grid", "Grid", self.grid_panel, False),
                ("objects", "Objects", self.objects_panel, True)):
            build(dock.add(key, title, PANEL_ICONS[key], shown=shown))
        return dock

    def small_button(self, icon, callback):
        widget = DesignIconButton(COMMAND_ICONS[icon] if isinstance(icon, str) else icon, size=26)
        widget.clicked.connect(lambda _checked: callback())
        return widget

    @staticmethod
    def dock_button(label, callback):
        widget = button(label, callback)
        widget.setMinimumHeight(24)
        return widget

    def page_panel(self, panel):
        media = panel.form("Media")
        self.media_form = media
        names = (*geometry.SHEETS, "Custom")
        self.media = media.choice("Size", names, changed=self.change_size)
        self.orientation = media.choice("Orientation", {True: "Portrait", False: "Landscape"},
                                        changed=self.change_orientation)
        self.media_width = media.number("Width (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.media_height = media.number("Height (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.mat = media.choice("Cutting mat", graphtec.MAT_OPTIONS, changed=self.change_mat)
        printing = panel.form("Print")
        self.print_margin = printing.number("Print margin (mm)", 5, 0, 25, 0.5, 1, self.canvas.redraw)
        self.print_margin.setToolTip("Set the margin you use on your printer. View → Show → Print Border shows it.")
        marks = panel.form("Registration marks")
        self.marks_form = marks
        self.register = marks.check("Enable registration", changed=self.change_marks)
        self.pattern = marks.choice("Pattern", {regmarks.THREE: "Type 1 (3 marks)", regmarks.FOUR: "4 marks"},
                                    changed=self.change_marks)
        self.inset = marks.number("Inset (mm)", 10, 2, 40, 0.1, 3, self.change_marks)
        self.length = marks.number("Length (mm)", 20, regmarks.MIN_LENGTH_MM,
                                  regmarks.MAX_LENGTH_MM, 0.1, 4, self.change_marks)
        self.thickness = marks.number("Thickness (mm)", 1, regmarks.MIN_THICKNESS_MM,
                                     regmarks.MAX_THICKNESS_MM, 0.05, 3, self.change_marks)

    POINTS_PER_MM = 72 / 25.4  # Studio sizes text in points: 72 pt is about an inch

    def text_panel(self, panel):
        """Studio's Text Style panel (manual chapter 05)."""
        self.font_search = QLineEdit(placeholderText="Search fonts")
        self.font_search.setAccessibleName("Search fonts")
        self.font_search.textChanged.connect(self.filter_fonts)
        panel.layout_.addWidget(self.font_search)
        self.font_list = QListWidget()
        self.font_list.setAccessibleName("Fonts")
        self.font_list.setMaximumHeight(130)
        try:
            families = design_text.installed_families()
        except ValueError:
            families = ()
        for family in families:
            self.font_list.addItem(family)
        default = "DejaVu Sans" if "DejaVu Sans" in families else (families[0] if families else "")
        matches = self.font_list.findItems(default, Qt.MatchFlag.MatchExactly)
        if matches:
            self.font_list.setCurrentItem(matches[0])
        self.font_list.currentItemChanged.connect(lambda *_: self.apply_text_style())
        panel.layout_.addWidget(self.font_list)

        self.text_style_buttons = {}
        for key, label, tip in (("bold", "B", "Bold"), ("italic", "I", "Italic")):
            widget = self.dock_button(label, self.apply_text_style)
            widget.setCheckable(True)
            widget.setAccessibleName(tip)
            widget.setToolTip(tip)
            self.text_style_buttons[key] = widget
        self.text_align = QComboBox()
        set_choices(self.text_align, {"left": "Left", "center": "Center", "right": "Right"}, "left")
        self.text_align.setAccessibleName("Justification")
        self.text_align.currentIndexChanged.connect(lambda _i: self.apply_text_style())
        panel.row(*self.text_style_buttons.values(), self.text_align)

        form = panel.form()
        self.text_size = form.number("Size (pt)", 28.3, 3, 283, 1, 1, self.apply_text_style)
        self.char_spacing = form.number("Character spacing (%)", 100, 10, 500, 5, 0, self.apply_text_style)
        self.line_spacing = form.number("Line spacing (%)", 100, 10, 500, 5, 0, self.apply_text_style)
        self.wrap_width = form.number("Box width (mm)", 0, 0, 1000, 5, 1, self.apply_text_style)
        self.wrap_width.setSpecialValueText("No wrap")

        path = panel.form("Text to path")
        self.path_offset = path.number("Offset (mm)", 0, -100, 100, 0.5, 2, self.apply_text_style)
        self.path_offset.setToolTip("Positive lifts the text above the line; negative drops it below.")
        self.path_start = path.number("Start (mm)", 0, 0, 10000, 1, 1, self.apply_text_style)
        self.text_to_path_button = self.dock_button("Text to Path", self.text_to_path)
        self.text_to_path_button.setToolTip("Select the text and the line to set it on.")
        self.release_path_button = self.dock_button("Release Path", self.release_text_path)
        panel.row(self.text_to_path_button, self.release_path_button)
        self.text_button = self.dock_button("Edit Text", lambda: self.begin_text(index=self.selection()[0]))
        panel.row(self.text_button)
        self.text_box = None
        self.text_session = None

    def filter_fonts(self, query):
        query = query.casefold()
        for row in range(self.font_list.count()):
            item = self.font_list.item(row)
            item.setHidden(bool(query) and query not in item.text().casefold())

    def text_settings(self, value="X", base=None):
        """A TextSpec from the panel, keeping a text's path when it has one."""
        item = self.font_list.currentItem()
        family = item.text() if item else (base.family if base else "DejaVu Sans")
        return model.TextSpec(
            value, family, round(self.text_size.value() / self.POINTS_PER_MM, 3),
            bold=self.text_style_buttons["bold"].isChecked(), italic=self.text_style_buttons["italic"].isChecked(),
            align=self.text_align.currentData(), char_spacing=self.char_spacing.value(),
            line_spacing=self.line_spacing.value(), wrap_mm=self.wrap_width.value(),
            path=base.path if base else (), path_offset=self.path_offset.value(), path_start=self.path_start.value())

    def show_text_settings(self, spec):
        prev, self.loading = self.loading, True
        matches = self.font_list.findItems(spec.family, Qt.MatchFlag.MatchExactly)
        if matches:
            self.font_list.setCurrentItem(matches[0])
        self.text_size.setValue(spec.size_mm * self.POINTS_PER_MM)
        self.text_style_buttons["bold"].setChecked(spec.bold)
        self.text_style_buttons["italic"].setChecked(spec.italic)
        self.text_align.setCurrentIndex(self.text_align.findData(spec.align))
        self.char_spacing.setValue(spec.char_spacing)
        self.line_spacing.setValue(spec.line_spacing)
        self.wrap_width.setValue(spec.wrap_mm)
        self.path_offset.setValue(spec.path_offset)
        self.path_start.setValue(spec.path_start)
        self.loading = prev

    def apply_text_style(self):
        """Panel changes restyle the text being typed, or the selected text."""
        if self.loading:
            return
        if self.text_box is not None:
            self.preview_text()
            return
        selection = self.selection()
        if len(selection) != 1 or not self.project.shapes[selection[0]].text:
            return
        index = selection[0]
        old = self.project.shapes[index]
        try:
            updated = design_text.shape(replace(self.text_settings(old.text.value, old.text), value=old.text.value),
                                        old.x, old.y, old)
        except ValueError as error:
            self.message(str(error))
            return
        shapes = self.project.shapes
        self.commit_shapes(shapes[:index] + (updated,) + shapes[index + 1:], (index,))

    # ---- typing on the canvas -------------------------------------------------------
    def begin_text(self, at=None, index=None):
        """Studio's Text tool: type straight onto the page; the outline follows."""
        self.finish_text()
        shapes = self.project.shapes
        original = shapes[index] if index is not None else None
        if original is not None and original.text:
            self.show_text_settings(original.text)
            at = (original.x, original.y)
        if at is None:
            at = (self.project.width / 3, self.project.height / 3)
        self.text_session = {"index": index, "at": at, "base": shapes}
        box = TextBox(self.canvas.viewport())
        corner = self.canvas.mapFromScene(QPointF(*at))
        box.move(corner.x(), corner.y() - 4)
        box.resize(260, 64)
        box.setPlainText(original.text.value if original is not None and original.text else "")
        box.textChanged.connect(self.preview_text)
        box.finished.connect(self.finish_text)
        self.text_box = box
        box.show()
        box.setFocus()
        box.moveCursor(box.textCursor().MoveOperation.End)
        self.dock.open("text")

    def preview_text(self):
        session = self.text_session
        if session is None or self.text_box is None:
            return
        value = self.text_box.toPlainText()
        base, index = session["base"], session["index"]
        if not value.strip():
            self.canvas.draft = base[:index] + base[index + 1:] if index is not None else base
            self.canvas.redraw()
            return
        original = base[index] if index is not None else None
        try:
            spec = self.text_settings(value, original.text if original is not None else None)
            shape = design_text.shape(spec, *session["at"], original)
        except ValueError as error:
            self.message(str(error))
            return
        if original is None:
            shape = self.styled(shape)
        self.canvas.draft = base[:index] + (shape,) + base[index + 1:] if index is not None else base + (shape,)
        self.canvas.redraw()

    def finish_text(self):
        box, session = self.text_box, self.text_session
        if box is None or session is None:
            return
        self.text_box = self.text_session = None
        box.hide()
        box.deleteLater()
        shapes, self.canvas.draft = self.canvas.draft, None
        if shapes is None or shapes == session["base"]:
            self.refresh(self.selection())
            return
        index = session["index"]
        if index is None:
            selection = (len(shapes) - 1,) if len(shapes) > len(session["base"]) else ()
        else:
            selection = (index,) if len(shapes) == len(session["base"]) else ()
        self.commit_shapes(shapes, selection)

    def text_to_path(self):
        selection = self.selection()
        shapes = self.project.shapes
        texts = [i for i in selection if shapes[i].text]
        guides = [i for i in selection if not shapes[i].text and not shapes[i].sticker]
        if len(texts) != 1 or len(guides) != 1:
            self.message("Select one text and the one line or shape to set it on.")
            return
        text_index, guide_index = texts[0], guides[0]
        line = shapes[guide_index].paths()[0]
        points = tuple(line.points) + ((line.points[0],) if line.closed else ())
        old = shapes[text_index]
        spec = replace(old.text, path=tuple((round(x, 4), round(y, 4)) for x, y in points),
                       path_offset=self.path_offset.value(), path_start=self.path_start.value())
        try:
            updated = design_text.shape(spec, old.x, old.y, old)
        except ValueError as error:
            self.message(str(error))
            return
        out = list(shapes)
        out[text_index] = updated
        # Studio greys the guide out: it stops being cut once text sits on it.
        out[guide_index] = replace(shapes[guide_index], action="None")
        self.commit_shapes(out, (text_index,))
        self.message("The guide line is set not to cut. Set its action to Cut in Line and Fill to cut it too.")

    def release_text_path(self):
        selection = self.selection()
        if len(selection) != 1 or not self.project.shapes[selection[0]].text \
                or not self.project.shapes[selection[0]].text.path:
            self.message("Select text that is set on a path.")
            return
        index = selection[0]
        old = self.project.shapes[index]
        try:
            updated = design_text.shape(replace(old.text, path=()), old.x, old.y, None)
        except ValueError as error:
            self.message(str(error))
            return
        updated = replace(updated, x=old.x, y=old.y, action=old.action, stroke=old.stroke, fill=old.fill,
                          group=old.group, holder=old.holder)
        shapes = self.project.shapes
        self.commit_shapes(shapes[:index] + (updated,) + shapes[index + 1:], (index,))

    def style_panel(self, panel):
        cutting = panel.form("Cut style")
        self.action = cutting.choice("Action", ACTION_LABELS, changed=self.change_action)
        self.action.setToolTip("Cut through, score, or leave the object out of the cut.")
        line = panel.form("Line")
        self.line_color = self.color_field(line, "Line color", "stroke")
        self.line_style = line.choice("Style", model.LINE_STYLES,
                                      changed=lambda: self.change_selected(line_style=self.line_style.currentData()))
        self.line_width = line.number("Width (mm)", 0, 0, 5, 0.1, 2,
                                      lambda: self.change_selected(line_width_mm=self.line_width.value()))
        self.line_style.setToolTip("Appearance only. The cutter follows the whole line.")
        self.line_width.setSpecialValueText("Hairline")
        fill = panel.form("Fill")
        self.fill_color = self.color_field(fill, "Fill color", "fill")

    def text_button_row(self, panel, *specs, columns=4):
        """Small labelled buttons in rows, as Studio's panel presets."""
        made = {}
        for start in range(0, len(specs), columns):
            widgets = []
            for label, callback in specs[start:start + columns]:
                widget = self.dock_button(label, callback)
                widget.setMinimumWidth(0)
                made[label] = widget
                widgets.append(widget)
            panel.row(*widgets)
        return made

    def apply_transform(self, operation, *args, **kwargs):
        """Run a chapter 06 operation on the selection as one undoable step."""
        selection = self.selection()
        try:
            result = operation(self.project.shapes, selection, *args, **kwargs)
        except ValueError as error:
            self.message(str(error))
            return
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], tuple):
            shapes, made = result
            self.commit_shapes(shapes, made or selection)
        else:
            self.commit_shapes(result, selection)

    def transform_panel(self, panel):
        size = panel.form("Position and size (mm)")
        self.size_form = size
        self.fields = {}
        for field, label in (("x", "X"), ("y", "Y"), ("width", "Width"), ("height", "Height")):
            spin = QDoubleSpinBox(decimals=3, minimum=-1000, maximum=1000, singleStep=0.1, keyboardTracking=False)
            spin.setAccessibleName(f"{label} (mm)")
            spin.valueChanged.connect(lambda _value, key=field: self.change_number(key))
            size.addRow(label, spin)
            self.fields[field] = spin
        self.fields["x"].setToolTip("Move To: left edge of the selection, from the left of the page.")
        self.fields["y"].setToolTip("Move To: top edge of the selection, from the top of the page.")
        self.lock_ratio = size.check("Lock aspect ratio")
        self.fields["radius"] = size.number("Corner radius", 0, 0, 1000, 0.1, 3,
                                            lambda: self.change_number("radius"))

        move = panel.form("Move")
        self.nudge = move.number("Step (mm)", 1, 0.01, 100, 0.5, 2)
        arrows = {}
        for label, dx, dy in (("←", -1, 0), ("→", 1, 0), ("↑", 0, -1), ("↓", 0, 1)):
            widget = self.dock_button(label, lambda dx=dx, dy=dy: self.apply_transform(
                transforms.move_by, dx * self.nudge.value(), dy * self.nudge.value()))
            widget.setAccessibleName({"←": "Nudge left", "→": "Nudge right", "↑": "Nudge up", "↓": "Nudge down"}[label])
            arrows[label] = widget
        panel.row(*arrows.values())
        self.move_by_x = QDoubleSpinBox(decimals=2, minimum=-1000, maximum=1000, singleStep=1)
        self.move_by_y = QDoubleSpinBox(decimals=2, minimum=-1000, maximum=1000, singleStep=1)
        for spin, name in ((self.move_by_x, "Move by X (mm)"), (self.move_by_y, "Move by Y (mm)")):
            spin.setAccessibleName(name)
        self.move_by_button = self.dock_button("Move By", lambda: self.apply_transform(
            transforms.move_by, self.move_by_x.value(), self.move_by_y.value()))
        panel.row(self.move_by_x, self.move_by_y, self.move_by_button)

        rotate = panel.form("Rotate")
        angle = QDoubleSpinBox(decimals=3, minimum=-360, maximum=360, singleStep=1, keyboardTracking=False)
        angle.valueChanged.connect(lambda: self.change_number("rotation"))
        angle.setAccessibleName("Rotate To (°)")
        angle.setToolTip("Rotate To: the object's angle from its original 0°.")
        rotate.addRow("Rotate To (°)", angle)
        self.fields["rotation"] = angle
        self.rotate_buttons = self.text_button_row(panel, *(
            (f"{value:+g}°", lambda value=value: self.apply_transform(
                transforms.rotate_by, value, self.canvas.rotation_centre)) for value in (-90, -45, 45, 90, 180)),
            columns=5)
        self.rotate_by = QDoubleSpinBox(decimals=2, minimum=-360, maximum=360, singleStep=5)
        self.rotate_by.setAccessibleName("Rotate By (°)")
        self.rotate_by_button = self.dock_button("Rotate By", lambda: self.apply_transform(
            transforms.rotate_by, self.rotate_by.value(), self.canvas.rotation_centre))
        panel.row(self.rotate_by, self.rotate_by_button)
        self.centre_check = QCheckBox("Center of rotation (O)")
        self.centre_check.setToolTip("Show a crosshair you can drag; rotations turn about it.")
        self.centre_check.toggled.connect(self.canvas.set_rotation_centre)
        panel.row(self.centre_check)
        self.transform_buttons = {
            name: self.small_button(icon, lambda operation=name: self.transform_objects(operation))
            for name, icon in TRANSFORM_ICONS.items()
        }
        panel.row(*self.transform_buttons.values())

        scale = panel.form("Scale")
        self.scale_buttons = self.text_button_row(panel, *(
            (f"{value}%", lambda value=value: self.apply_transform(transforms.scale_by, value))
            for value in (50, 90, 110, 200)))
        self.scale_percent = QDoubleSpinBox(decimals=1, minimum=1, maximum=1000, singleStep=5, value=100, suffix=" %")
        self.scale_percent.setAccessibleName("Scale (%)")
        self.scale_button = self.dock_button("Scale", lambda: self.apply_transform(
            transforms.scale_by, self.scale_percent.value()))
        panel.row(self.scale_percent, self.scale_button)
        del scale

        shear = panel.form("Shear")
        self.shear_h = shear.number("Horizontal (°)", 0, -80, 80, 1, 1)
        self.shear_v = shear.number("Vertical (°)", 0, -80, 80, 1, 1)
        self.shear_button = self.dock_button("Shear", lambda: self.apply_transform(
            transforms.shear, self.shear_h.value(), self.shear_v.value()))
        presets = self.text_button_row(panel, *(
            (label, lambda h=h, v=v: self.apply_transform(transforms.shear, h, v))
            for label, h, v in (("H −15°", -15, 0), ("H +15°", 15, 0), ("V −15°", 0, -15), ("V +15°", 0, 15))))
        panel.row(self.shear_button)
        self.shear_buttons = presets

    def align_panel(self, panel):
        self.alignment_buttons = {}
        for icons in ALIGNMENT_ROWS:
            buttons = {icon.name: self.small_button(icon, lambda option=icon.name: self.align_objects(option))
                       for icon in icons}
            self.alignment_buttons.update(buttons)
            panel.row(*buttons.values())

    def replicate_panel(self, panel):
        gap = panel.form()
        self.replicate_gap = gap.number("Spacing (mm)", 1, 0, 1000, 0.5, 2)
        self.replicate_gap.setToolTip("The space Studio's presets leave between copies.")
        spacing = lambda: self.replicate_gap.value()  # noqa: E731
        panel.layout_.addWidget(styled_label("Duplicate", role="dockSection"))
        buttons = self.text_button_row(panel, *(
            (side.title(), lambda side=side: self.apply_transform(transforms.duplicate_side, side, spacing()))
            for side in transforms.SIDES))
        panel.layout_.addWidget(styled_label("Mirror copy", role="dockSection"))
        buttons.update({f"Mirror {k}": v for k, v in self.text_button_row(panel, *(
            (side.title(), lambda side=side: self.apply_transform(transforms.mirror_copy, side, spacing()))
            for side in transforms.SIDES)).items()})
        panel.layout_.addWidget(styled_label("Rows and columns", role="dockSection"))
        buttons.update(self.text_button_row(panel, *(
            (label, lambda count=count, axis=axis: self.apply_transform(transforms.replicate_line, count, axis,
                                                                        spacing()))
            for label, count, axis in (("Row of 3", 3, 0), ("Row of 4", 4, 0), ("Column of 3", 3, 1),
                                       ("Column of 4", 4, 1))), columns=2))
        panel.layout_.addWidget(styled_label("Rotate copies", role="dockSection"))
        buttons.update({f"Rotate {k}": v for k, v in self.text_button_row(panel, *(
            (str(count), lambda count=count: self.apply_transform(transforms.rotate_copies, count))
            for count in (1, 2, 3, 5))).items()})
        buttons["Fill Page"] = self.dock_button("Fill Page", lambda: self.apply_transform(
            transforms.fill_page, self.cut_area(), spacing()))
        panel.row(buttons["Fill Page"])
        self.replicate_buttons = buttons

        grid = panel.form("Grid")
        self.columns = grid.number("Columns", 2, 1, 32)
        self.rows = grid.number("Rows", 2, 1, 32)
        self.x_gap = grid.number("Column spacing (mm)", 2, 0, 1000, 0.1, 2)
        self.y_gap = grid.number("Row spacing (mm)", 2, 0, 1000, 0.1, 2)
        self.replicate_button = self.dock_button("Replicate", self.replicate_from_panel)
        panel.row(self.replicate_button)

        advanced = panel.form("Advanced")
        self.copies = advanced.number("Copies", 3, 1, 100)
        self.copy_gap = advanced.number("Distance (mm)", 2, 0, 1000, 0.5, 2)
        self.copy_angle = advanced.number("Direction (°)", 0, -360, 360, 15, 1)
        self.copy_angle.setToolTip("0° to the right, 90° downward.")
        self.direction_button = self.dock_button("Replicate in Direction", lambda: self.apply_transform(
            transforms.replicate_direction, self.copies.value(), self.copy_gap.value(), self.copy_angle.value()))
        panel.row(self.direction_button)

        along = panel.form("Object to path")
        self.path_copies = along.number("Copies", 6, 1, 200)
        self.path_rotate = along.check("Turn to follow the path", True)
        self.path_button = self.dock_button("Place on Path", self.place_on_path)
        self.path_button.setToolTip("Select the object, then Shift-click the path last.")
        panel.row(self.path_button)

    def modify_panel(self, panel):
        self.modify_buttons = {}
        labels = (*model.PATH_OPERATIONS, "Detach Lines")
        made = self.text_button_row(panel, *(
            (name, lambda name=name: self.modify_shapes(name)) for name in labels), columns=2)
        self.modify_buttons.update(made)

    def offset_panel(self, panel):
        form = panel.form()
        self.offset_distance = form.number("Distance (mm)", 1, -20, 20, 0.1, 2)
        self.offset_distance.setToolTip("Positive outlines the shape; negative makes an internal offset.")
        self.offset_join = form.choice("Corners", {"corner": "Corner", "round": "Round"})
        self.offset_button = self.dock_button("Apply Offset", self.offset_from_panel)
        self.internal_offset_button = self.dock_button("Internal Offset", lambda: self.offset_from_panel(internal=True))
        panel.row(self.offset_button, self.internal_offset_button)

    def trace_panel(self, panel):
        panel.layout_.addWidget(styled_label("Select a placed image, then choose a trace style.", role="caption"))
        self.trace_area_button = self.dock_button("Select Trace Area", self.canvas.begin_trace_area)
        self.trace_area_clear = self.dock_button("Whole Image", self.clear_trace_area)
        panel.row(self.trace_area_button, self.trace_area_clear)
        form = panel.form()
        self.trace_threshold = form.number("Threshold", 128, 1, 254, 1, 0, self.canvas.redraw, slider=True)
        self.trace_high_pass = form.check("High pass filter", changed=self.canvas.redraw)
        self.trace_smoothing = form.number("Scale (smoothing)", 0, 0, 10, 0.5, 1, self.canvas.redraw)
        self.trace_preview = form.check("Show trace preview", True, self.canvas.redraw)
        self.trace_styles = self.text_button_row(panel, *(
            (style, lambda style=style: self.trace_image(style))
            for style in ("Trace", "Trace Outer Edge", "Trace and Detach")), columns=2)
        self.trace_button = self.dock_button("Trace and Bleed…", self.trace_sticker)
        self.trace_button.setToolTip("Print & Cut stickers: trace the image's own cut line with a print bleed.")
        panel.row(self.trace_button)

    def nesting_panel(self, panel):
        form = panel.form()
        self.nest_area = form.choice("Nest in", {"cut": "Cut area", "selected": "Selected border"})
        self.nest_area.setToolTip("Selected border: the front-most selected shape is the area to nest into.")
        self.nest_rotations = form.choice("Rotations", {1: "None", 2: "180°", 4: "90°", 8: "45°"}, 4)
        self.nest_padding = form.number("Padding (mm)", 1, 0, 50, 0.5, 2)
        self.nest_button = self.dock_button("Nest", self.nest_selection)
        panel.row(self.nest_button)

    def grid_panel(self, panel):
        form = panel.form()
        self.grid_check = form.check("Show grid", self.show_grid.isChecked(), lambda: self.show_grid.setChecked(
            self.grid_check.isChecked()))
        self.show_grid.toggled.connect(self.grid_check.setChecked)
        self.spacing = form.number("Spacing (mm)", 5, 0.5, 100, 0.5, 2, self.canvas.redraw)
        self.grid_divisions = form.number("Divisions", 1, 1, 10, 1, 0, self.canvas.redraw)
        self.grid_style = form.choice("Style", {"square": "Square", "isometric": "Isometric"},
                                      changed=self.canvas.redraw)
        self.grid_color = ColorSwatch("Grid color")
        self.grid_color.set_color("#dce1e5")
        self.grid_color.picked.connect(lambda _value: self.canvas.redraw())
        self.grid_color.pick_from_canvas.connect(lambda: self.message("Pick a grid color from the menu."))
        form.addRow("Color", self.grid_color)
        self.snap_check = form.check("Snap to grid", self.snap.isChecked(),
                                     lambda: self.snap.setChecked(self.snap_check.isChecked()))
        self.snap.toggled.connect(self.snap_check.setChecked)
        self.ruler_check = form.check("Rulers", self.rulers.isChecked(),
                                      lambda: self.rulers.setChecked(self.ruler_check.isChecked()))
        self.rulers.toggled.connect(self.ruler_check.setChecked)
        self.crosshair_check = form.check("Crosshairs", self.crosshairs.isChecked(),
                                          lambda: self.crosshairs.setChecked(self.crosshair_check.isChecked()))
        self.crosshairs.toggled.connect(self.crosshair_check.setChecked)

    # ---- chapter 06 actions ------------------------------------------------------
    def cut_area(self):
        """The area the cutter can reach on this page: Studio's cut area."""
        project = self.project
        width = min(project.width, self.device.width_mm)
        height = min(project.height, self.device.length_mm)
        marks = project.registration.on_page(project.width, project.height) if project.registration else None
        if marks:
            x, y = marks.origin_mm
            return x, y, min(width - x, marks.reg_width_mm), min(height - y, marks.reg_length_mm)
        return 0.0, 0.0, width, height

    def place_on_path(self):
        selection = self.selection()
        if len(selection) < 2:
            self.message("Select the object to place and the path to place it on (the path last).")
            return
        path_index = self.canvas.last_selected if self.canvas.last_selected in selection else selection[-1]
        self.apply_transform(transforms.along_path, path_index, self.path_copies.value(),
                             rotate=self.path_rotate.isChecked())

    def nest_selection(self):
        selection = list(self.selection())
        shapes = self.project.shapes
        if self.nest_area.currentData() == "selected":
            border = max((i for i in selection if shapes[i].closed), default=None)
            if border is None or len(selection) < 2:
                self.message("Select the shapes to nest and, front-most, the border to nest them in.")
                return
            box = model.bounds((shapes[border],))
            area = (box.x, box.y, box.w, box.h)
            selection.remove(border)
        else:
            area = self.cut_area()
        try:
            out, placed, left = transforms.nest(shapes, selection, area, padding=self.nest_padding.value(),
                                                rotations=self.nest_rotations.currentData())
        except ValueError as error:
            self.message(str(error))
            return
        self.commit_shapes(out, placed)
        self.message(f"Nested {len(placed)} object(s)" + (f"; {len(left)} did not fit and stayed where they were."
                                                         if left else "."))

    def clear_trace_area(self):
        self.canvas.trace_area = None
        self.canvas.redraw()

    def trace_image(self, style):
        selection = self.selection()
        shape = self.project.shapes[selection[0]] if len(selection) == 1 else None
        if not shape or not shape.sticker:
            self.message("Select one placed image to trace.")
            return
        try:
            image, traced = sticker_trace.trace_shapes(
                shape, area=self.canvas.trace_area, threshold=self.trace_threshold.value(),
                high_pass=self.trace_high_pass.isChecked(), smoothing=self.trace_smoothing.value(), style=style)
        except ValueError as error:
            self.message(str(error))
            return
        shapes = list(self.project.shapes)
        shapes[selection[0]] = image
        group = model._next_group(shapes) if len(traced) > 1 else None
        traced = tuple(replace(self.styled(t), group=group) for t in traced)
        start = len(shapes)
        self.canvas.trace_area = None
        self.commit_shapes((*shapes, *traced), range(start, start + len(traced)))
        self.message(f"{style}: {len(traced)} cut line object(s) made.")

    def objects_panel(self, panel):
        panel.setAccessibleName("Objects")
        self.duplicate_button = self.small_button("duplicate", self.duplicate)
        self.delete_button = self.small_button("delete", self.delete)
        options = DesignIconButton(COMMAND_ICONS["more"], size=26)
        menu = QMenu(options)
        self.group_button = self.command("Group", self.group)
        self.ungroup_button = self.command("Ungroup", lambda: self.group(release=True))
        menu.addActions([self.group_button, self.ungroup_button])
        menu.addSeparator()
        self.forward_action = self.command("Bring Forward", lambda: self.reorder(1))
        self.backward_action = self.command("Send Backward", lambda: self.reorder(-1))
        menu.addActions([self.forward_action, self.backward_action])
        options.setMenu(menu)
        options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.objects = QListWidget()
        self.objects.setIconSize(QSize(16, 16))
        self.objects.setAccessibleName("Object list")
        self.objects.setProperty("role", "designObjects")
        self.objects.setMinimumHeight(140)
        self.objects.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.objects.itemSelectionChanged.connect(self.refresh_selection)
        empty = styled_label("No objects yet. Pick a shape tool and drag on the page.", role="caption")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.object_list = QStackedWidget()
        self.object_list.addWidget(self.objects)
        self.object_list.addWidget(empty)
        panel.layout_.addWidget(self.object_list, 1)
        panel.row(self.duplicate_button, self.delete_button, options)

    @staticmethod
    def transform_row(widgets):
        group = QWidget()
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch()
        return group

    def color_field(self, form, label, channel):
        swatch = ColorSwatch(label)
        swatch.picked.connect(lambda value: self.change_selected(**{channel: value}))
        swatch.pick_from_canvas.connect(lambda: self.canvas.sample_color(channel))
        form.addRow("Color", swatch)
        return swatch

    def build_design_menus(self, file_actions):
        def command(menu, label, callback, shortcut=None, *, canvas=False):
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False: callback())
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                if canvas:
                    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                    self.canvas.addAction(action)
            return action

        new, open_file, save, save_as, import_art = file_actions
        files = self.menu_bar.addMenu("&File")
        files.addActions((new, open_file))
        files.addSeparator()
        files.addActions((save, save_as))
        files.addSeparator()
        files.addAction(import_art)
        command(files, "Place Image…", self.place_sticker)
        command(files, "Import PixScan Photo…", self.import_pixscan)
        files.addSeparator()
        command(files, "Export Print && Cut ZIP…", self.export_stickers)

        edit = self.menu_bar.addMenu("&Edit")
        edit.addActions((self.undo_action, self.redo_action))
        edit.addSeparator()
        command(edit, "Select All", lambda: self.select(
            i for i, shape in enumerate(self.project.shapes) if shape.visible), "Ctrl+A", canvas=True)
        command(edit, "Deselect", lambda: self.select(()), "Ctrl+Shift+A", canvas=True)
        same = edit.addMenu("Select Same")
        command(same, "Line Color", lambda: self.select_color("stroke"))
        command(same, "Fill Color", lambda: self.select_color("fill"))
        edit.addSeparator()
        self.cut_action = command(edit, "Cut", lambda: self.copy(cut=True), "Ctrl+X", canvas=True)
        self.copy_action = command(edit, "Copy", self.copy, "Ctrl+C", canvas=True)
        self.paste_action = command(edit, "Paste", self.paste, "Ctrl+V", canvas=True)
        self.paste_front_action = command(edit, "Paste in Front", lambda: self.paste(in_front=True), "Ctrl+Shift+V",
                                          canvas=True)
        edit.addSeparator()
        self.duplicate_action = command(edit, "Duplicate", self.duplicate, "Ctrl+D", canvas=True)
        self.delete_action = command(edit, "Delete", self.delete, "Delete", canvas=True)

        view = self.menu_bar.addMenu("&View")
        zoom = view.addMenu("Zoom")
        for label, callback, keys in (("Zoom In", lambda: self.canvas.zoom_by(1.25), ("Ctrl+=", "Ctrl++")),
                                      ("Zoom Out", lambda: self.canvas.zoom_by(0.8), ("Ctrl+-",)),
                                      ("Fit on Screen", self.canvas.fit, ("Ctrl+0",))):
            action = self.command(label, callback, keys[0])
            action.setShortcuts([QKeySequence(key) for key in keys])
            zoom.addAction(action)
        rotate = view.addMenu("Rotate Page")
        command(rotate, "Left", lambda: self.rotate_page(False))
        command(rotate, "Right", lambda: self.rotate_page(True))
        view.addSeparator()
        show = view.addMenu("Show")
        show.addActions((self.show_grid, self.rulers, self.crosshairs, self.show_mat, self.cut_border,
                         self.print_border))
        view.addActions((self.snap, self.smart_snap))
        command(view, "Grid…", lambda: self.dock.open("grid"))
        view.addSeparator()
        view.addAction(self.command("Show or Hide Panels", self.toggle_panels, "F9"))
        command(view, "Document Properties", self.show_document, "Ctrl+F1")
        command(view, "Switch to Send", self.send_workspace.click, "Ctrl+F2")

        objects = self.menu_bar.addMenu("&Object")
        transform = objects.addMenu("Transform")
        command(transform, "Rotate 90° Counterclockwise", lambda: self.transform_objects("left"))
        command(transform, "Rotate 90° Clockwise", lambda: self.transform_objects("right"))
        command(transform, "Flip Horizontally", lambda: self.transform_objects("horizontal"))
        command(transform, "Flip Vertically", lambda: self.transform_objects("vertical"))
        arrange = objects.addMenu("Arrange")
        command(arrange, "Bring to Front", lambda: self.arrange("front"), "Ctrl+Shift+]", canvas=True)
        command(arrange, "Bring Forward", lambda: self.reorder(1), "Ctrl+]", canvas=True)
        command(arrange, "Send Backward", lambda: self.reorder(-1), "Ctrl+[", canvas=True)
        command(arrange, "Send to Back", lambda: self.arrange("back"), "Ctrl+Shift+[", canvas=True)
        align = objects.addMenu("Align")
        for choice in model.ALIGNMENTS:
            command(align, choice, lambda option=choice: self.align_objects(option))
        objects.addSeparator()
        command(objects, "Group", self.group)
        command(objects, "Ungroup", lambda: self.group(release=True))
        command(objects, "Replicate…", lambda: self.dock.open("replicate"))
        objects.addSeparator()
        command(objects, "Convert to Path", self.convert_selection)
        command(objects, "Simplify", lambda: self.convert_selection(True), "Ctrl+J")
        modify = objects.addMenu("Modify")
        command(modify, "Offset…", lambda: self.dock.open("offset"))
        for operation in model.PATH_OPERATIONS:
            command(modify, operation, lambda name=operation: self.modify_shapes(name))
        holders = objects.addMenu("Tool Holder")
        command(holders, "Use Current Settings", lambda: self.change_selected(holder=None))
        command(holders, "Tool Holder 1", lambda: self.change_selected(holder=1))
        second = command(holders, "Tool Holder 2", lambda: self.change_selected(holder=2))
        second.setEnabled(self.device.two_tools)
        objects.addSeparator()
        command(objects, "Add or Edit Text…", self.text_dialog)
        self.trace_action = command(objects, "Trace and Bleed…", self.trace_sticker)

    def show_document(self):
        self.dock.open("page")

    def toggle_panels(self):
        show = self.tool.isHidden()
        for panel in (self.tool, self.options_bar, self.dock):
            panel.setVisible(show)
        self.canvas.setFocus()

    def rotate_page(self, clockwise):
        try:
            project = model.rotate_page(self.project, clockwise)
        except ValueError as error:
            self.message(str(error))
            return
        self.history.commit(project)
        self.refresh(self.selection())

    def transform_objects(self, operation):
        try:
            shapes = model.transform_selection(self.project.shapes, self.selection(), operation)
        except ValueError as error:
            self.message(str(error))
            return
        self.commit_shapes(shapes, self.selection())

    def align_objects(self, option):
        selection = self.selection()
        try:
            shapes = model.align(self.project.shapes, selection, option)
        except ValueError as error:
            self.message(str(error))
            return
        self.commit_shapes(shapes, selection)

    def select_color(self, field):
        self.select(model.matching_color(self.project.shapes, self.selection(), field))

    def convert_selection(self, simplify=False):
        selected = self.selection()
        try:
            shapes = tuple(model.convert_path(shape, simplify) if index in selected else shape
                           for index, shape in enumerate(self.project.shapes))
        except ValueError as error:
            self.message(str(error))
        else:
            self.commit_shapes(shapes, selected)

    def offset_from_panel(self, internal=False):
        selection = [i for i in self.selection() if self.project.shapes[i].closed]
        if not selection:
            self.message("Select a closed shape to offset.")
            return
        before = self.project.shapes
        distance = -abs(self.offset_distance.value()) if internal else self.offset_distance.value()
        try:
            copies = tuple(model.offset_shape(before[index], distance, self.offset_join.currentData())
                           for index in selection)
        except ValueError as error:
            self.message(str(error))
        else:
            self.commit_shapes(before + copies, range(len(before), len(before) + len(copies)))

    def modify_shapes(self, operation):
        if operation == "Detach Lines":
            try:
                shapes, made = model.detach_lines(self.project.shapes, self.selection())
            except ValueError as error:
                self.message(str(error))
            else:
                self.commit_shapes(shapes, made)
            return
        self._modify_shapes(operation)

    def _modify_shapes(self, operation):
        try:
            shapes, selected = model.modify_selection(self.project.shapes, self.selection(), operation)
        except ValueError as error:
            self.message(str(error))
        else:
            self.commit_shapes(shapes, selected)

    def text_dialog(self):
        """Object → Add or Edit Text: the on-canvas text box, as the Type tool."""
        selection = self.selection()
        shapes = self.project.shapes
        if len(selection) == 1 and shapes[selection[0]].text:
            self.begin_text(index=selection[0])
        else:
            self.begin_text()

    def message(self, text):
        self.status.setText(text)

    def place_sticker(self):
        name, _ = QFileDialog.getOpenFileName(self, "Place Sticker Image", "",
                                             "Images (*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG *.webp *.WEBP)")
        if not name:
            return
        project = self.project
        try:
            shape = sticker_trace.place(Path(name), project.width, project.height)
        except (OSError, ValueError) as error:
            self.message(str(error))
        else:
            self.commit_shapes((*project.shapes, shape), (len(project.shapes),))
            self.message("Select Trace and Bleed to create the cut outline.")

    def import_pixscan(self):
        """A photo of a PixScan mat, straightened to true size, as in Studio's
        PixScan import. The mat geometry is Studio's (FINDINGS section 38)."""
        name, _ = QFileDialog.getOpenFileName(self, "Import PixScan Photo", "",
                                              "Photos (*.jpg *.JPG *.jpeg *.JPEG *.png *.PNG *.webp *.WEBP)")
        if not name:
            return
        path = Path(name)
        Image = sticker_assets.pillow()
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            with Image.open(path) as photo:
                if photo.width * photo.height > 60_000_000:
                    raise ValueError("Choose a photo under 60 megapixels.")
                result = pixscan.scan(photo)
            shape = result.sticker_shape(path.name)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.message(str(error))
            return
        finally:
            self.unsetCursor()
        shapes = self.project.shapes
        self.commit_shapes((*shapes, shape), (len(shapes),))
        self.message(f"{result.report()} Placed at true size and not cut. "
                     "Where the photo area sits on the cutting mat is not known yet, so position it yourself.")

    def trace_sticker(self):
        selection = self.selection()
        if len(selection) != 1:
            return
        shape = self.project.shapes[selection[0]]
        settings = shape.sticker
        if settings is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Trace and Bleed")
        form = QFormLayout(dialog)
        source = QComboBox()
        set_choices(source, sticker_assets.SOURCES, settings.trace_source)
        threshold = QSpinBox()
        threshold.setRange(1, 254)
        threshold.setValue(settings.threshold)
        holes = QCheckBox("Include interior holes")
        holes.setChecked(settings.keep_holes)
        bleed = QDoubleSpinBox()
        bleed.setRange(0, 5)
        bleed.setSingleStep(0.25)
        bleed.setValue(settings.bleed_mm)
        form.addRow("Trace from", source)
        form.addRow("Threshold", threshold)
        form.addRow(holes)
        form.addRow("Print bleed (mm)", bleed)
        status = QLabel(wordWrap=True)
        form.addRow(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Trace")
        form.addRow(buttons)

        def apply():
            try:
                traced = sticker_trace.trace(shape, source=source.currentData(), threshold=threshold.value(),
                                             keep_holes=holes.isChecked(), bleed_mm=bleed.value())
            except ValueError as error:
                status.setText(str(error))
            else:
                self.change_selected(contours=traced.contours, sticker=traced.sticker)
                dialog.accept()

        buttons.accepted.connect(apply)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def export_stickers(self):
        if self.exporting_stickers:
            return
        name, _ = QFileDialog.getSaveFileName(self, "Export Print & Cut", "stickers.zip", "Matched ZIP (*.zip)")
        if not name:
            return
        try:
            target = sticker_print.destination(Path(name))
        except ValueError as error:
            self.message(str(error))
            return
        project, cutter = self.project, self.device.name
        self.exporting_stickers = True
        self.message("Exporting Print & Cut…")

        def done(path, error):
            self.exporting_stickers = False
            self.message(str(error) if error else f"Saved {path.name}. Open it in Send → Sheet.")

        self.export_tasks.run(lambda: sticker_print.export(target, project, cutter=cutter), done)

    def selection(self):
        return tuple(sorted(index.row() for index in self.objects.selectedIndexes()))

    def select(self, indices):
        old = self.objects.blockSignals(True)
        self.objects.clearSelection()
        for index in indices:
            if 0 <= index < self.objects.count():
                self.objects.item(index).setSelected(True)
        self.objects.blockSignals(old)
        self.refresh_selection()

    def refresh(self, selection):
        prev, self.loading = self.loading, True
        self.objects.clear()
        shapes = self.project.shapes
        color = self.objects.palette().text().color()
        icons = {kind: design_icon(OBJECT_ICONS.get(kind, COMMAND_ICONS["path"]), color)
                 for kind in {shape.kind for shape in shapes}}
        for number, shape in enumerate(shapes, 1):
            details = f" · Group {shape.group}" if shape.group else ""
            if shape.holder:
                details += f" · Tool {shape.holder}"
            name = f"{number}. {model.object_name(shape)}{details}"
            item = QListWidgetItem(icons[shape.kind], name)
            item.setToolTip(name)
            self.objects.addItem(item)
        self.object_list.setCurrentIndex(0 if self.objects.count() else 1)
        self.sync_page()
        self.loading = prev
        self.select(selection)

    def sync_page(self):
        prev, self.loading = self.loading, True
        project = self.project
        named = geometry.match_sheet((project.width, project.height))
        if not named or self.media.currentData() == "Custom":
            name = "Custom"
        else:
            name = named[0]
        self.media.setCurrentIndex(self.media.findData(name))
        for field, value in ((self.media_width, project.width), (self.media_height, project.height)):
            field.setValue(value)
            field.setEnabled(name == "Custom")
            self.media_form.setRowVisible(field, name == "Custom")
        self.orientation.setCurrentIndex(self.orientation.findData(project.height >= project.width))
        self.mat.setCurrentIndex(self.mat.findData(project.mat))
        self.register.setChecked(project.registration is not None)
        reg = project.registration or self.last_marks
        self.pattern.setCurrentIndex(self.pattern.findData(reg.pattern))
        for field, value in ((self.inset, reg.inset_mm), (self.length, reg.length_mm),
                             (self.thickness, reg.thickness_mm)):
            field.setValue(value)
        for field in (self.pattern, self.inset, self.length, self.thickness):
            self.marks_form.setRowVisible(field, self.register.isChecked())
        self.loading = prev

    def refresh_selection(self):
        if self.loading:
            return
        self.loading = True
        indices = self.selection()
        shapes = self.project.shapes
        shape = shapes[indices[0]] if len(indices) == 1 else None
        units = len(model.selected_units(shapes, indices))
        is_text = bool(shape and shape.text)
        if is_text and self.text_box is None:
            self.show_text_settings(shape.text)
        texts = [i for i in indices if shapes[i].text]
        self.text_to_path_button.setEnabled(len(indices) == 2 and len(texts) == 1)
        self.release_path_button.setEnabled(bool(is_text and shape.text.path))
        can_trace = bool(shape and shape.sticker)
        self.text_button.setEnabled(is_text)
        self.trace_button.setEnabled(can_trace)
        self.trace_action.setEnabled(can_trace)
        closed = [i for i in indices if shapes[i].closed]
        self.offset_button.setEnabled(bool(closed))
        self.replicate_button.setEnabled(bool(indices))
        for name, command in self.modify_buttons.items():
            command.setEnabled(len(indices) >= (1 if name in ("Release Compound Path", "Detach Lines") else 2))
        for widget in (*self.rotate_buttons.values(), self.rotate_by_button, *self.scale_buttons.values(),
                       self.scale_button, self.shear_button, *self.shear_buttons.values(), self.move_by_button,
                       *self.replicate_buttons.values(), self.direction_button, self.nest_button):
            widget.setEnabled(bool(indices))
        self.path_button.setEnabled(len(indices) >= 2)
        image = bool(shape and shape.sticker)
        for widget in (*self.trace_styles.values(), self.trace_area_button, self.trace_area_clear):
            widget.setEnabled(image)
        if self.canvas.rotation_centre and not indices:
            self.centre_check.setChecked(False)
        for widget in (self.action, self.line_color, self.line_style, self.line_width, self.fill_color):
            widget.setEnabled(bool(indices))

        area = model.bounds(shapes[i] for i in indices) if len(indices) > 1 else None
        values = ({"x": area.x, "y": area.y, "width": area.w, "height": area.h, "rotation": 0, "radius": 0}
                  if area else shape)
        for key, field in self.fields.items():
            usable = shape is not None or (area is not None and key in ("x", "y", "width", "height"))
            field.setEnabled(usable)
            if values is not None:
                if key in ("width", "height"):
                    low = -1000 if shape and shape.kind == "Line" else 0 if shape and shape.kind == "Path" else 0.1
                    field.setRange(low, 1000)
                field.setValue(values[key] if area else getattr(shape, key))
        if values is None:
            for field in self.fields.values():
                field.setValue(0)
        self.fields["radius"].setEnabled(bool(shape and shape.kind == "Rounded rectangle"))
        self.lock_ratio.setEnabled(shape is not None and min(shape.width, shape.height) > 0)
        for key, field in (("stroke", self.line_color), ("fill", self.fill_color)):
            colors = {getattr(shapes[i], key) for i in indices}
            field.set_color(next(iter(colors)) if len(colors) == 1 else "", mixed=len(colors) > 1)
        roles = {shapes[i].action for i in indices}
        self.action.setCurrentIndex(self.action.findData(roles.pop()) if len(roles) == 1 else -1)
        styles = {shapes[i].line_style for i in indices}
        self.line_style.setCurrentIndex(self.line_style.findData(styles.pop()) if len(styles) == 1 else -1)
        self.line_width.setValue(shape.line_width_mm if shape else 0)
        for widget in (self.duplicate_button, self.delete_button, self.duplicate_action, self.delete_action,
                       self.cut_action, self.copy_action):
            widget.setEnabled(bool(indices))
        self.paste_action.setEnabled(bool(_clipboard))
        self.paste_front_action.setEnabled(bool(_clipboard))
        self.measured()
        self.group_button.setEnabled(units > 1)
        for command in self.transform_buttons.values():
            command.setEnabled(bool(indices))
        for option, command in self.alignment_buttons.items():
            space = option.startswith("Space ")
            command.setEnabled(units >= (3 if space else 2))
            command.setToolTip(f"{option}\nSelect three objects to space." if space and units < 3 else option)
        self.ungroup_button.setEnabled(any(shapes[i].group for i in indices))
        self.forward_action.setEnabled(bool(indices) and indices[-1] + 1 < len(shapes))
        self.backward_action.setEnabled(bool(indices) and indices[0] > 0)
        self.loading = False
        self.canvas.redraw()
        self.refresh_status()

    def refresh_status(self):
        project = self.project
        action = model.send_action(project.shapes)
        count = len(project.artwork(action).paths) if action else 0
        has_stickers = any(shape.visible and shape.sticker for shape in project.shapes)
        self.review_button.setVisible(has_stickers)
        self.undo_action.setEnabled(bool(self.history.past))
        self.redo_action.setEnabled(bool(self.history.future))
        self.save_action.setEnabled(self.path is None or self.saved != project)
        name = self.path.name if self.path else self.source or "Untitled"
        dirty = project != self.saved
        self.document_tab.setTabText(0, name + (" *" if dirty else ""))
        self.document_tab.setTabToolTip(0, f"{name} (unsaved changes)" if dirty else name)
        paths = "1 path" if count == 1 else f"{count} paths"
        self.status.setText(f"{project.width:g} × {project.height:g} mm · {paths} · "
                            f"{len(self.selection())} selected")

    def change_size(self):
        if self.loading:
            return
        name = self.media.currentData()
        if name in geometry.SHEETS:
            width, height = geometry.SHEETS[name].size(self.orientation.currentData())
            self.change_project(width=width, height=height)
        else:
            self.sync_page()

    def change_orientation(self):
        if self.loading:
            return
        width, height = sorted((self.project.width, self.project.height), reverse=not self.orientation.currentData())
        self.change_project(width=width, height=height)

    def change_dimensions(self):
        if not self.loading and self.media.currentData() == "Custom":
            self.change_project(width=self.media_width.value(), height=self.media_height.value())

    def change_mat(self):
        if not self.loading:
            self.change_project(mat=self.mat.currentData())

    def change_marks(self):
        if self.loading:
            return
        setup = None
        if self.register.isChecked():
            self.last_marks = setup = model.Registration(self.inset.value(), self.length.value(),
                                                         self.thickness.value(), self.pattern.currentData())
        self.change_project(registration=setup)

    def change_project(self, **updates):
        self.history.commit(replace(self.history.current, **updates))
        self.sync_page()
        self.canvas.redraw()
        self.refresh_status()

    def change_selected(self, **updates):
        if self.loading:
            return
        selection = self.selection()
        if selection:
            self.commit_shapes(tuple(replace(shape, **updates) if index in selection else shape
                                     for index, shape in enumerate(self.project.shapes)), selection)

    def change_number(self, field):
        if self.loading:
            return
        selection = self.selection()
        value = self.fields[field].value()
        if len(selection) > 1:
            self.change_selection_box(selection, field, value)
            return
        if len(selection) != 1:
            return
        updates = {field: value}
        shape = self.project.shapes[selection[0]]
        if field in ("width", "height") and self.lock_ratio.isChecked() and value > 0 and min(
                shape.width, shape.height) > 0:
            other = "width" if field == "height" else "height"
            ratio = getattr(shape, other) / getattr(shape, field)
            value = max(self.fields[other].minimum() / ratio, min(value, 1000 / ratio))
            updates = {field: value, other: value * ratio}
        self.change_selected(**updates)

    def change_selection_box(self, selection, field, value):
        """Several objects edit as one box: X and Y move it, a size scales it
        proportionally from its top-left corner."""
        shapes = self.project.shapes
        area = model.bounds(shapes[i] for i in selection)
        try:
            if field in ("x", "y"):
                dx, dy = (value - area.x, 0) if field == "x" else (0, value - area.y)
                updated = model.move(shapes, selection, dx, dy)
            elif field in ("width", "height"):
                current = area.w if field == "width" else area.h
                if current <= 0:
                    raise ValueError("The selection has no size in that direction to scale.")
                updated = model.scale_selection(shapes, selection, (area.x, area.y), value / current)
            else:
                return
        except ValueError as error:
            self.message(str(error))
            self.refresh_selection()
            return
        self.commit_shapes(updated, selection)

    def change_action(self):
        if self.action.currentData() is not None:
            self.change_selected(action=self.action.currentData())

    def commit_shapes(self, shapes, selection):
        self.history.commit(replace(self.history.current, shapes=tuple(shapes)))
        self.refresh(selection)

    def styled(self, shape):
        """A new object takes the options bar's settings, as a Photoshop shape does."""
        radius = self.new_radius.value() if shape.kind == "Rounded rectangle" else shape.radius
        return replace(shape, action=self.new_action.currentData(), radius=radius)

    def add_shape(self, shape):
        count = len(self.project.shapes)
        self.commit_shapes(self.project.shapes + (self.styled(shape),), (count,))

    def copy(self, *, cut=False):
        global _clipboard
        selection = self.selection()
        if not selection:
            return
        _clipboard = tuple(self.project.shapes[i] for i in selection)
        if cut:
            self.delete()
        else:
            self.refresh_selection()

    def paste(self, *, in_front=False):
        """Studio pastes a copy just to the right of the original; Paste in
        Front puts it exactly on top."""
        if not _clipboard:
            return
        shapes = self.project.shapes
        copies = _clipboard
        if not in_front:
            box = model.bounds(copies)
            copies = model.move(copies, range(len(copies)), box.w + 2, 0)
        self.commit_shapes(model.paste(shapes, copies), range(len(shapes), len(shapes) + len(copies)))

    def add_point(self, position, tolerance):
        selection = self.selection()
        if len(selection) != 1:
            self.message("Select one outline, then double-click its edge to add a point.")
            return
        index = selection[0]
        try:
            shape, _vertex = model.insert_vertex(self.project.shapes[index], position, tolerance)
        except ValueError as error:
            self.message(str(error))
            return
        shapes = self.project.shapes
        self.commit_shapes(shapes[:index] + (shape,) + shapes[index + 1:], (index,))

    def measurement(self):
        if not self.canvas.measure:
            return None
        (x1, y1), (x2, y2) = self.canvas.measure
        return math.hypot(x2 - x1, y2 - y1), math.degrees(math.atan2(-(y2 - y1), x2 - x1))

    def measured(self):
        found = self.measurement()
        if found is None:
            self.measure_label.setText("Drag across a known distance")
        else:
            length, angle = found
            self.measure_label.setText(f"{length:.2f} mm at {round(angle, 1) + 0.0:.1f}°")
        self.scale_to_button.setEnabled(bool(found and found[0] > 0.05 and self.selection()))

    def scale_to_length(self):
        """Photoshop's Ruler, applied: the measured distance becomes the length
        typed in, scaling the selection about the measurement's start."""
        found = self.measurement()
        selection = self.selection()
        if not found or not selection:
            return
        measured = found[0]
        length, accepted = QInputDialog.getDouble(
            self, "Scale to Length", "Real length of the measured distance (mm)", measured, 0.1, 1000, 2)
        if not accepted:
            return
        factor = length / measured
        start, end = self.canvas.measure
        try:
            shapes = model.scale_selection(self.project.shapes, selection, start, factor)
        except ValueError as error:
            self.message(str(error))
            return
        self.canvas.measure = (start, (start[0] + (end[0] - start[0]) * factor, start[1] + (end[1] - start[1]) * factor))
        self.commit_shapes(shapes, selection)
        self.message(f"Scaled by {factor:.4g} so the measured distance is {length:g} mm.")

    CURSORS = {"Select": None, "Point Edit": None, "Type": Qt.CursorShape.IBeamCursor,
               "Hand": Qt.CursorShape.OpenHandCursor, "Zoom": Qt.CursorShape.PointingHandCursor,
               "Eyedropper": Qt.CursorShape.PointingHandCursor}
    POINT_ACTIONS = ("Delete Point", "Break Path", "Corner", "Smooth", "Make Flat", "Make Curve", "Simplify")

    def change_cursor(self):
        cursor = self.CURSORS.get(self.tool.currentData(), Qt.CursorShape.CrossCursor)
        if cursor is None:
            self.canvas.unsetCursor()
        else:
            self.canvas.setCursor(cursor)

    def nudge_step(self):
        return 1.0

    def point_action(self, name):
        """One of Studio's Point Editing toolbar actions on the chosen point."""
        if name == "Simplify":
            self.convert_selection(True)
            return
        found = self.canvas.edited_nodes()
        i = self.canvas.node_point
        if not found or i is None or i >= len(found[1].points):
            self.message("Choose a point first: click it with Point Edit.")
            return
        index, nodes = found
        shape = self.project.shapes[index]
        operations = {"Corner": path_edit.corner, "Smooth": path_edit.smooth, "Make Flat": path_edit.make_flat,
                      "Make Curve": path_edit.make_curve, "Delete Point": path_edit.delete_point}
        try:
            if name == "Break Path":
                pieces = [path_edit.to_shape(part, shape) for part in path_edit.break_path(nodes, i)]
            else:
                pieces = [path_edit.to_shape(operations[name](nodes, i), shape)]
        except ValueError as error:
            self.message(str(error))
            return
        shapes = self.project.shapes
        self.commit_shapes(shapes[:index] + tuple(pieces) + shapes[index + 1:], (index,))
        if name == "Delete Point":
            self.canvas.node_point = max(0, i - 1)
        elif name == "Break Path":
            self.canvas.node_point = 0
        self.canvas.redraw()

    def transfer_properties(self, source):
        """Studio's Eyedropper (Transfer Properties): the clicked shape's cut
        style, line and fill go onto the selection."""
        selection = self.selection()
        if source is None:
            self.message("Click a shape to copy its properties.")
            return
        if not selection:
            self.message("Select the shapes to change first, then click the shape to copy with the Eyedropper.")
            return
        shape = self.project.shapes[source]
        self.change_selected(action=shape.action, stroke=shape.stroke, fill=shape.fill, line_style=shape.line_style,
                             line_width_mm=shape.line_width_mm, holder=shape.holder)

    def knife(self, start, end):
        """Studio's Knife: split every closed shape the line crosses (the
        selected ones, if any are selected)."""
        shapes = self.project.shapes
        targets = set(self.selection()) or set(range(len(shapes)))
        out, made = [], []
        try:
            for index, shape in enumerate(shapes):
                pieces = (model.slice_shape(shape, start, end) if index in targets and shape.visible
                          and shape.closed and not shape.sticker and shape.kind != "Line" else (shape,))
                if len(pieces) > 1:
                    made.extend(range(len(out), len(out) + len(pieces)))
                out.extend(pieces)
        except ValueError as error:
            self.message(str(error))
            return
        if not made:
            self.message("The knife line has to cross a closed shape all the way to split it.")
            self.canvas.redraw()
            return
        self.commit_shapes(out, made)

    def erase_stroke(self, stroke):
        shapes = self.project.shapes
        targets = self.selection() or range(len(shapes))
        try:
            out, changed = model.erase(shapes, targets, stroke, self.eraser_size.value() / 2)
        except ValueError as error:
            self.message(str(error))
            return
        if changed:
            self.commit_shapes(out, ())
        else:
            self.canvas.redraw()

    def arrange(self, where):
        """Bring to Front or Send to Back, keeping the selection's own order."""
        shapes = self.project.shapes
        chosen = set(self.selection())
        if not chosen:
            return
        moving = [shape for i, shape in enumerate(shapes) if i in chosen]
        rest = [shape for i, shape in enumerate(shapes) if i not in chosen]
        ordered = rest + moving if where == "front" else moving + rest
        start = len(rest) if where == "front" else 0
        self.commit_shapes(ordered, range(start, start + len(moving)))

    def fill_context_menu(self, menu):
        has = bool(self.selection())
        for label, call, enabled in (("Cut", lambda: self.copy(cut=True), has), ("Copy", self.copy, has),
                                     ("Paste", self.paste, bool(_clipboard)),
                                     ("Paste in Front", lambda: self.paste(in_front=True), bool(_clipboard)),
                                     ("Duplicate", self.duplicate, has), ("Delete", self.delete, has)):
            menu.addAction(label, call).setEnabled(enabled)
        menu.addSeparator()
        menu.addAction("Group", self.group).setEnabled(len(self.selection()) > 1)
        menu.addAction("Ungroup", lambda: self.group(release=True)).setEnabled(has)
        arrange = menu.addMenu("Arrange")
        arrange.addAction("Bring to Front", lambda: self.arrange("front"))
        arrange.addAction("Bring Forward", lambda: self.reorder(1))
        arrange.addAction("Send Backward", lambda: self.reorder(-1))
        arrange.addAction("Send to Back", lambda: self.arrange("back"))
        arrange.setEnabled(has)
        mirror = menu.addMenu("Mirror")
        mirror.addAction("Mirror Horizontally", lambda: self.transform_objects("horizontal"))
        mirror.addAction("Mirror Vertically", lambda: self.transform_objects("vertical"))
        mirror.setEnabled(has)
        menu.addSeparator()
        menu.addAction("Make Compound Path", lambda: self.modify_shapes("Make Compound Path")).setEnabled(
            len(self.selection()) > 1)
        menu.addAction("Release Compound Path", lambda: self.modify_shapes("Release Compound Path")).setEnabled(has)
        menu.addAction("Edit Points", lambda: self.tool.setCurrentText("Point Edit")).setEnabled(
            len(self.selection()) == 1)
        if not has:
            menu.addSeparator()
            menu.addAction("Select All", lambda: self.select(
                i for i, shape in enumerate(self.project.shapes) if shape.visible))
            menu.addAction(self.show_grid)

    def type_at(self, point):
        """Type tool: click text to edit it, anywhere else to start new text there."""
        shapes = self.project.shapes
        tolerance = 5 / max(0.02, self.canvas.transform().m11())
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if shape.visible and shape.text and model.hit(shape, (point.x(), point.y()), tolerance):
                self.select((index,))
                self.begin_text(index=index)
                return
        self.select(())
        self.begin_text(at=self.canvas.point(point))

    def change_tool(self):
        self.canvas.setFocus()
        self.canvas.cancel()
        self.canvas.polygon.clear()
        tool = WORKSPACE_TOOLS[self.tool.currentIndex()]
        self.change_cursor()
        self.draw_options.setVisible(tool.name in DRAWING_TOOLS)
        self.radius_option.setVisible(tool.name == "Rounded rectangle")
        self.sides_option.setVisible(tool.name == "Regular Polygon")
        self.close_polygon.setVisible(tool.name in CLICK_PATH_TOOLS)
        self.eraser_options.setVisible(tool.name == "Eraser")
        self.point_options.setVisible(tool.name == "Point Edit")
        self.measure_options.setVisible(tool.name == "Measure")
        self.canvas.measure = None
        self.measured()
        self.tool_hint.setText(tool.hint)
        self.tool_hint.setToolTip(tool.hint)
        self.canvas.redraw()

    def undo(self):
        selection = self.selection()
        self.canvas.cancel()
        self.history.undo()
        self.refresh(selection)

    def redo(self):
        selection = self.selection()
        self.canvas.cancel()
        self.history.redo()
        self.refresh(selection)

    def delete(self):
        if self.tool.currentData() == "Point Edit" and self.canvas.node_point is not None \
                and self.canvas.edited_nodes():
            self.point_action("Delete Point")
            return
        selected = set(self.selection())
        self.commit_shapes((shape for i, shape in enumerate(self.project.shapes) if i not in selected), ())

    def reorder(self, step):
        selection = self.selection()
        shapes = list(self.project.shapes)
        cursor = sorted(selection, reverse=step > 0)
        if not cursor or not all(0 <= i+step < len(shapes) for i in cursor):
            return
        for i in cursor:
            shapes[i+step], shapes[i] = shapes[i], shapes[i+step]
        self.commit_shapes(shapes, tuple(i+step for i in selection))

    def duplicate(self):
        shapes, selection = self.project.shapes, self.selection()
        try:
            copies = model.duplicate(shapes, selection)
        except ValueError as error:
            self.message(str(error))
        else:
            self.commit_shapes(copies, range(len(shapes), len(copies)))

    def group(self, *, release=False):
        selection = self.selection()
        shapes = self.project.shapes
        members = {member for index in selection for member in model.group_members(shapes, index)}
        self.commit_shapes(model.group_selection(shapes, selection, release=release), members)

    def replicate_from_panel(self):
        error = self.replicate(self.columns.value(), self.rows.value(), self.x_gap.value(), self.y_gap.value())
        if error:
            self.message(error)

    def replicate(self, columns, rows, x_gap, y_gap):
        shapes, selection = self.project.shapes, self.selection()
        try:
            copies = model.replicate(shapes, selection, columns, rows, x_gap, y_gap)
        except ValueError as error:
            return str(error)
        self.commit_shapes(copies, (*selection, *range(len(shapes), len(copies))))
        return None

    def load_vector(self, path):
        try:
            return importer.load(path)
        except importer.InkscapePageRequired as error:
            page, ok = QInputDialog.getInt(self, "Choose a Page", "Inkscape page", 1, 1, error.count)
            return importer.load(path, page=page) if ok else None

    def open_project(self):
        value, _ = QFileDialog.getOpenFileName(self, "Open Design", "", DESIGN_FILTER)
        if not value:
            return
        path = Path(value)
        native = path.suffix.casefold() == ".scc-template"
        try:
            if native:
                project = model.load_project(path, legacy_registration=self.project.registration)
                details = ""
            else:
                drawing = self.load_vector(path)
                if drawing is None:
                    return
                shapes, details = model.editable_import(drawing)
                size = drawing.page_size_mm or (self.project.width, self.project.height)
                if not all(1 <= value <= model.SHAPE_LIMIT for value in size):
                    raise ValueError("Design supports page dimensions from 1 to 1,000 mm.")
                project = model.Project(*size, shapes, mat=self.project.mat)
        except (ValueError, OSError) as error:
            self.message(str(error))
            return
        if self.may_replace():
            self.replace_document(project, path if native else None, path.name)
            notice = design_text.unavailable_fonts(project) or details
            if notice:
                self.message(notice)

    def import_art(self):
        value, _ = QFileDialog.getOpenFileName(self, "Import Artwork", "", DESIGN_FILTER.split(";;")[-1])
        if not value:
            return
        try:
            drawing = self.load_vector(Path(value))
            if drawing is not None:
                shapes, details = model.editable_import(drawing)
                start = len(self.project.shapes)
                self.commit_shapes(self.project.shapes + shapes, range(start, start + len(shapes)))
                self.message(details)
        except (ValueError, OSError) as error:
            self.message(str(error))

    def replace_document(self, project, path=None, source=None):
        self.canvas.cancel()
        self.canvas.polygon.clear()
        self.path, self.source = path, source
        self.history = model.History(project)
        self.saved = project
        if project.registration:
            self.last_marks = project.registration
        prev, self.loading = self.loading, True
        self.media.setCurrentIndex(0)
        self.loading = prev
        self.refresh((0,) if project.shapes else ())
        self.canvas.fit()

    def new_project(self):
        if self.may_replace():
            self.replace_document(replace(self.project, shapes=()))

    def may_replace(self):
        if self.project == self.saved:
            return True
        choice = QMessageBox.question(self, "Save Design?", "Save the current design before closing it?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if choice == QMessageBox.StandardButton.Save:
            return self.save()
        return choice == QMessageBox.StandardButton.Discard

    def save(self, *, as_new=False):
        path = self.path
        if path is None or as_new:
            default = str(path) if path else str(Path(self.source or "design").with_suffix(".scc-template"))
            value, _ = QFileDialog.getSaveFileName(self, "Save Design", default, "Design projects (*.scc-template)")
            if not value:
                return False
            path = Path(value)
            if path.suffix.casefold() != ".scc-template":
                path = path.with_name(path.name + ".scc-template")
                if path.exists():
                    self.message(f"Choose the full name {path.name} to confirm replacement.")
                    return False
        try:
            model.save_project(path, self.project)
        except (OSError, ValueError) as error:
            self.message(str(error))
            return False
        self.path, self.saved = path, self.project
        self.refresh_status()
        self.message(f"Saved {path.name}.")
        return True
