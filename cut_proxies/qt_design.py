"""Qt Design editor for the existing versioned Design projects."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import math
from math import dist
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QSizePolicy, QSpinBox, QSplitter,
                               QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget)

from . import (design_text, editor_model as model, geometry, graphtec, importer, pixscan, regmarks, sticker_assets,
               sticker_print, sticker_trace, template)
from .design_tools import (ALIGNMENT_ROWS, CLICK_PATH_TOOLS, COMMAND_ICONS, DRAWING_TOOLS, OBJECT_ICONS, TRANSFORM_ICONS, TURN_RIGHT,
                           WORKSPACE_KEYS, WORKSPACE_TOOLS, rotation_handle, scale_from_corner)
from .qt_canvas import SheetView, ZoomControls, draw_contours, draw_feed_arrow, draw_marks, draw_paper, draw_path, pen, rect
from .qt_controls import (AdaptiveActions, DesignIconButton, Page, Tasks, ToolStrip, bar, button, design_divider,
                          design_icon, icon_outline, set_choices, styled_label, workspace_header, workspace_layout)

DESIGN_FILTER = "Design projects (*.scc-template);;Cut templates (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
ACTION_LABELS = {"Cut": "Cut", "Score": "Score", "None": "None (not cut)"}
# One clipboard for every Design document, so an outline traced on one page
# can be pasted into a new one, as in Studio's template workflow.
_clipboard = ()


@lru_cache(maxsize=4)
def _sticker_pixmap(png):
    pixmap = QPixmap()
    pixmap.loadFromData(png, "PNG")
    return pixmap


class Canvas(SheetView):
    def __init__(self, design):
        super().__init__()
        self.design = design
        self.setDragMode(self.DragMode.NoDrag)
        self.setAccessibleName("Design canvas")
        self.start = self.edit = self.anchor = self.pan = None
        self.polygon = []
        self.freehand = []
        self.measure = None
        self.marquee = None
        self.draft = None
        self.active = ()
        self.rotation = None
        self.scale_anchor = None
        self.angle = 0
        self.rotation_icon = icon_outline(TURN_RIGHT)

    def rotation_handle(self):
        return rotation_handle(self.design.project.shapes, self.design.selection(), self.transform().m11())

    def drawForeground(self, painter, area):
        super().drawForeground(painter, area)
        if self.design.tool.currentData() != "Select" or self.edit == "marquee":
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
        for x, y in handle.corners():
            painter.drawRect(QRectF(x - half, y - half, 2 * half, 2 * half))
        self.paint_rotation(painter, self.rotation or handle, scale, outline)
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
            self.grid(scene, page, editor.spacing.value(), "#dce1e5")
        if editor.rulers.isChecked():
            text_color = "#222f3c" if mat_visible else self.palette().windowText().color()
            self.draw_rulers(scene, page, text_color)
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
            color = "#3c70c5" if index in selection else shape.stroke or {1: "#b2323a", 2: "#244b9a"}.get(shape.holder, "#313945")
            fill = QColor(shape.fill) if shape.closed and shape.fill else Qt.BrushStyle.NoBrush
            line = pen(color, shape.line_width_mm, dashed=shape.action == "Score" or shape.line_style == "dashed")
            if shape.line_style == "dotted":
                line.setStyle(Qt.PenStyle.DotLine)
            draw_contours(scene, shape.paths(), line, fill)
        if editor.tool.currentData() == "Point Edit" and len(selection) == 1:
            shape = project.shapes[selection[0]]
            if shape.kind in model.POINT_EDIT_KINDS and shape.visible:
                dot = min(2.5, 4 / max(0.02, self.transform().m11()))
                for path in shape.paths():
                    for x, y in path.points:
                        scene.addEllipse(x-dot/2, y-dot/2, dot, dot, pen("#2264aa"), QBrush(Qt.GlobalColor.white))
        if self.polygon:
            curve = editor.tool.currentData() == "Curve"
            points = model.smooth_points(self.polygon, closed=False) if curve else tuple(self.polygon)
            draw_path(scene, template.CutPath(points, closed=False), pen("#2264aa", dashed=True))
            if curve:
                dot = min(2.5, 4 / max(0.02, self.transform().m11()))
                for x, y in self.polygon:
                    scene.addEllipse(x - dot/2, y - dot/2, dot, dot, pen("#2264aa"), QBrush(Qt.GlobalColor.white))
        if self.measure:
            (x1, y1), (x2, y2) = self.measure
            line = pen("#c2410c", 1.6 / max(0.02, self.transform().m11()))
            scene.addLine(x1, y1, x2, y2, line)
            tick = 3 / max(0.02, self.transform().m11())
            for x, y in self.measure:
                scene.addEllipse(x - tick/2, y - tick/2, tick, tick, line, QBrush(Qt.GlobalColor.white))
        if self.marquee:
            scene.addRect(self.marquee, pen("#3c70c5", dashed=True), QBrush(QColor(70, 140, 225, 45)))
        if refit:
            self.fit()

    @staticmethod
    def grid(scene, box, spacing, color):
        brush = pen(color)
        for tick in range(1, int(box.width() / spacing) + 1):
            x = tick * spacing
            scene.addLine(x, 0, x, box.height(), brush)
        for tick in range(1, int(box.height() / spacing) + 1):
            y = tick * spacing
            scene.addLine(0, y, box.width(), y, brush)

    @staticmethod
    def draw_rulers(scene, page, color):
        for axis, length in enumerate((page.width(), page.height())):
            for tick in range(0, int(length) + 1, 20):
                text = scene.addSimpleText(str(tick))
                text.setBrush(QColor(color))
                text.setScale(0.35)
                if axis == 0:
                    scene.addLine(tick, 0, tick, -2, pen("#59616a"))
                    text.setPos(tick, -6)
                else:
                    scene.addLine(0, tick, -2, tick, pen("#59616a"))
                    text.setPos(-10, tick - 2)

    def point(self, point):
        x, y = point.x(), point.y()
        if self.design.snap.isChecked():
            step = self.design.spacing.value()
            x, y = round(x / step) * step, round(y / step) * step
        return round(x, 4), round(y, 4)

    def select_at(self, position, shift=False, group=True):
        shapes = self.design.project.shapes
        tolerance = 5 / max(0.02, self.transform().m11())
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if shape.visible and (group or shape.kind in model.POINT_EDIT_KINDS) and model.hit(
                    shape, (position.x(), position.y()), tolerance):
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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        editor = self.design
        self.setFocus()
        self.start = self.mapToScene(event.position().toPoint())
        self.anchor = event.position().toPoint()
        tool = editor.tool.currentData()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if tool in CLICK_PATH_TOOLS:
            point = self.point(self.start)
            if len(self.polygon) >= 3 and dist(point, self.polygon[0]) <= 6 / self.transform().m11():
                self.finish_polygon()
            elif not self.polygon or self.polygon[-1] != point:
                self.polygon.append(point)
            self.start = None
            self.redraw()
        elif tool == "Point Edit":
            self.begin_point_edit()
        elif tool == "Select":
            self.begin_selection(shift)
        elif tool == "Type":
            point, self.start = self.start, None
            editor.type_at(point)
        elif tool == "Measure":
            self.edit = "measure"
            self.measure = None
        else:
            self.edit = "draw"
            self.freehand = [self.point(self.start)]
        event.accept()

    def begin_selection(self, shift):
        """Photoshop's Move tool with its transform controls showing: the round
        grip rotates, a corner scales from the opposite one, inside moves."""
        point = (self.start.x(), self.start.y())
        handle = None if shift else self.rotation_handle()
        scale_anchor = handle.opposite_corner(point) if handle else None
        if handle and handle.hit(point):
            self.edit, self.rotation = "rotate", handle
        elif scale_anchor is not None:
            self.edit, self.scale_anchor = "scale", scale_anchor
        elif not self.select_at(self.start, shift):
            self.edit = "marquee"
        else:
            self.edit = "move"
        self.active = self.design.selection()
        if self.edit in ("rotate", "scale"):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def begin_point_edit(self):
        editor = self.design
        selected = editor.selection()
        if len(selected) == 1:
            index = selected[0]
            shape = editor.project.shapes[index]
            if shape.visible and shape.kind in model.POINT_EDIT_KINDS:
                distance, vertex = model.nearest_vertex(shape, (self.start.x(), self.start.y()))
                if distance <= 7 / self.transform().m11():
                    self.active = (index, vertex)
                    self.edit = "point"
                    return
        self.select_at(self.start, group=False)
        self.start = None

    def mouseMoveEvent(self, event):
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
        if (self.anchor - position).manhattanLength() >= 2 or (
                self.edit in ("rotate", "scale") and self.draft is not None):
            self.update_drag(self.mapToScene(position), snap=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))

    def update_drag(self, point, *, snap=False):
        base = self.design.history.current.shapes
        delta = point - self.start
        if self.edit == "rotate" and self.rotation:
            angle = self.rotation.angle((self.start.x(), self.start.y()), (point.x(), point.y()), snap=snap)
            if angle is not None:
                try:
                    self.draft = model.rotate_selection(base, self.active, angle, center=self.rotation.center)
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
        elif self.edit == "move" and self.active:
            anchor = base[self.active[0]]
            x, y = self.point(QPointF(anchor.x + delta.x(), anchor.y + delta.y()))
            dx, dy = x - anchor.x, y - anchor.y
            if self.design.smart_snap.isChecked():
                project = self.design.project
                dx, dy = model.snap_move(base, self.active, dx, dy, project.width, project.height,
                                         6 / max(0.02, self.transform().m11()))
            self.draft = model.move(base, self.active, dx, dy)
        elif self.edit == "marquee":
            self.marquee = QRectF(self.start, point).normalized()
        elif self.edit == "measure":
            self.measure = ((self.start.x(), self.start.y()), (point.x(), point.y()))
            self.design.measured()
        elif self.edit == "point":
            index, vertex = self.active
            try:
                shape = model.move_vertex(base[index], vertex, self.point(point))
            except ValueError:
                self.draft = None
            else:
                self.draft = base[:index] + (shape,) + base[index + 1:]
        elif self.edit == "draw":
            try:
                shape = self.shape_between(self.point(self.start), self.point(point))
            except ValueError:
                shape = None
            self.draft = base + (self.design.styled(shape),) if shape else None
        self.redraw()

    def shape_between(self, start, end):
        tool = self.design.tool.currentData()
        if tool == "Freehand":
            if end != self.freehand[-1]:
                self.freehand.append(end)
            return model.shape_from_path(template.CutPath(tuple(self.freehand), closed=False)) if len(
                self.freehand) > 1 else None
        dx, dy = end[0]-start[0], end[1]-start[1]
        if max(map(abs, (*start, *end, dx, dy))) > model.SHAPE_LIMIT:
            return None
        if tool == "Line":
            return model.Shape(tool, *start, dx, dy) if dx or dy else None
        if min(abs(dx), abs(dy)) >= 0.1:
            return model.Shape(tool, min(start[0], end[0]), min(start[1], end[1]), abs(dx), abs(dy))
        return None

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan = None
            self.unsetCursor()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return super().mouseReleaseEvent(event)
        self.update_from_pointer(event)
        editor = self.design
        if self.marquee is not None:
            selected = set(self.active)
            for index, shape in enumerate(editor.project.shapes):
                if shape.visible and self.marquee.contains(rect(model.bounds((shape,)))):
                    selected.update(model.group_members(editor.project.shapes, index))
            editor.select(selected)
        shapes = self.draft
        selection = (len(shapes)-1,) if shapes and self.edit == "draw" else editor.selection()
        self.cancel()
        if shapes is not None:
            editor.commit_shapes(shapes, selection)
        else:
            self.redraw()

    def mouseDoubleClickEvent(self, event):
        tool = self.design.tool.currentData()
        if tool in CLICK_PATH_TOOLS and event.button() == Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
            if self.polygon:
                self.finish_polygon()
        elif tool == "Point Edit" and event.button() == Qt.MouseButton.LeftButton:
            self.cancel()
            point = self.mapToScene(event.position().toPoint())
            self.design.add_point((point.x(), point.y()), 6 / max(0.02, self.transform().m11()))
        else:
            super().mouseDoubleClickEvent(event)

    def finish_polygon(self):
        if len(set(self.polygon)) < 3:
            self.design.message("Place three or more points to close the outline.")
            return
        points = tuple(self.polygon)
        if self.design.tool.currentData() == "Curve":
            points = model.smooth_points(points)
        try:
            shape = model.shape_from_path(template.CutPath(points, closed=True))
        except ValueError as error:
            self.design.message(str(error))
            return
        self.polygon.clear()
        self.design.add_shape(shape)

    def cancel(self):
        if self.edit in ("rotate", "scale"):
            self.unsetCursor()
        self.rotation, self.angle = None, 0
        self.scale_anchor = None
        self.start = self.edit = self.anchor = self.marquee = self.draft = None
        self.freehand.clear()

    def keyPressEvent(self, event):
        editor = self.design
        if event.key() == Qt.Key.Key_Escape:
            transforming = self.edit in ("rotate", "scale")
            self.cancel()
            self.polygon.clear()
            if self.measure:
                self.measure = None
                self.design.measured()
                self.redraw()
                return
            if transforming:
                self.redraw()
            else:
                editor.select(())
            return
        if self.edit in ("rotate", "scale"):
            return
        if editor.tool.currentData() in CLICK_PATH_TOOLS and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_polygon()
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in arrows and editor.selection():
            x, y = arrows[event.key()]
            distance = editor.spacing.value() if editor.snap.isChecked() else 1
            editor.commit_shapes(model.move(editor.project.shapes, editor.selection(),
                                            x * distance, y * distance), editor.selection())
            return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() in WORKSPACE_KEYS:
            editor.tool.setCurrentIndex(WORKSPACE_KEYS[event.key()])
            return
        super().keyPressEvent(event)


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
        self.replicate_settings = (2, 2, 2.0, 2.0)
        self.last_marks = project.registration or model.Registration.from_marks(
            regmarks.marks(project.width, project.height))
        self.setWindowTitle("Design")
        self.setObjectName("workspace")

        body = workspace_layout(self)
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
        workspace.setSpacing(6)
        workspace.addWidget(self.tool)
        panes = QSplitter(Qt.Orientation.Horizontal)
        panes.setChildrenCollapsible(False)
        panes.setHandleWidth(8)
        workspace.addWidget(panes, 1)
        body.addLayout(workspace, 1)
        self.export_tasks = Tasks(self)
        self.exporting_stickers = False
        panes.addWidget(self.document_frame())

        self.dock = dock = QSplitter(Qt.Orientation.Vertical)
        dock.setChildrenCollapsible(False)
        dock.setHandleWidth(6)
        dock.setMinimumWidth(240)
        dock.addWidget(self.properties_panel())
        dock.addWidget(self.objects_panel())
        dock.setStretchFactor(0, 3)
        dock.setStretchFactor(1, 2)
        dock.setSizes([400, 240])
        panes.addWidget(dock)
        panes.setStretchFactor(0, 1)
        panes.setStretchFactor(1, 0)
        panes.setSizes([800, 320])
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
        self.show_mat = toggle("Cutting Mat", True)
        self.cut_border = toggle("Cut Border", True)
        self.print_border = toggle("Print Border", False, "Show the margin your printer cannot reach.")
        self.snap = toggle("Snap to Grid", False, "Snap drawn points and object moves to the grid spacing.")
        self.smart_snap = toggle("Smart Snapping", False, "Line up moving shapes with nearby edges or centers.")

        self.grid_dialog = dialog = QDialog(self)
        dialog.setWindowTitle("Grid and Guides")
        form = QFormLayout(dialog)
        self.spacing = QDoubleSpinBox(decimals=2, minimum=0.5, maximum=100, singleStep=0.5, value=5)
        self.print_margin = QDoubleSpinBox(decimals=1, minimum=0, maximum=25, singleStep=0.5, value=5)
        self.print_margin.setToolTip("Set the margin you use on your printer.")
        for field in (self.spacing, self.print_margin):
            field.valueChanged.connect(lambda _value: self.canvas.redraw())
        form.addRow("Grid spacing (mm)", self.spacing)
        form.addRow("Print margin (mm)", self.print_margin)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.hide)
        form.addRow(close)

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
        self.close_polygon = button("Close Shape", self.canvas.finish_polygon)
        self.close_polygon.setToolTip("Close the polygon (Enter).")
        draw.addWidget(self.close_polygon)
        row.addWidget(self.draw_options)

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

    def properties_panel(self):
        panel = QWidget()
        panel.setAccessibleName("Properties")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 2, 3, 0)
        layout.setSpacing(2)
        self.properties_title = styled_label("Document", role="panelTitle")
        layout.addWidget(self.properties_title)
        page = Page()
        page.body.setContentsMargins(0, 2, 6, 10)
        self.document_groups = self.page_panel(page)
        self.object_groups = self.object_panel(page)
        self.properties = page.finish()
        self.properties.setAccessibleName("Design properties")
        layout.addWidget(self.properties, 1)
        return panel

    def objects_panel(self):
        panel = QWidget()
        panel.setAccessibleName("Objects")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 2, 3, 0)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(2)
        header.addWidget(styled_label("Objects", role="panelTitle"), 1)
        self.duplicate_button = self.icon_button("duplicate", self.duplicate)
        self.delete_button = self.icon_button("delete", self.delete)
        options = DesignIconButton(COMMAND_ICONS["more"])
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
        for control in (self.duplicate_button, self.delete_button, options):
            header.addWidget(control)
        layout.addLayout(header)
        self.objects = QListWidget()
        self.objects.setIconSize(QSize(16, 16))
        self.objects.setAccessibleName("Object list")
        self.objects.setProperty("role", "designObjects")
        self.objects.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.objects.itemSelectionChanged.connect(self.refresh_selection)
        empty = styled_label("No objects yet. Pick a shape tool and drag on the page.", role="caption")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.object_list = QStackedWidget()
        self.object_list.addWidget(self.objects)
        self.object_list.addWidget(empty)
        layout.addWidget(self.object_list, 1)
        return panel

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

    def page_panel(self, page):
        """Properties with nothing selected: the document, as in Photoshop."""
        media = page.group("Page")
        self.media_form = media
        names = (*geometry.SHEETS, "Custom")
        self.media = media.choice("Size", names, changed=self.change_size)
        self.orientation = media.choice("Orientation", {True: "Portrait", False: "Landscape"},
                                        changed=self.change_orientation)
        self.media_width = media.number("Width (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.media_height = media.number("Height (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.mat = media.choice("Cutting mat", graphtec.MAT_OPTIONS, changed=self.change_mat)
        marks = page.group("Registration marks")
        self.marks_form = marks
        self.register = marks.check("Enable registration", changed=self.change_marks)
        self.pattern = marks.choice("Pattern", {regmarks.THREE: "Type 1 (3 marks)", regmarks.FOUR: "4 marks"},
                                    changed=self.change_marks)
        self.inset = marks.number("Inset (mm)", 10, 2, 40, 0.1, 3, self.change_marks)
        self.length = marks.number("Length (mm)", 20, regmarks.MIN_LENGTH_MM,
                                  regmarks.MAX_LENGTH_MM, 0.1, 4, self.change_marks)
        self.thickness = marks.number("Thickness (mm)", 1, regmarks.MIN_THICKNESS_MM,
                                     regmarks.MAX_THICKNESS_MM, 0.05, 3, self.change_marks)
        return media.parentWidget(), marks.parentWidget()

    def object_panel(self, page):
        """Properties for the selection, most important first: what the cutter does."""
        cutting = page.group("Cutting")
        self.cutting_form = cutting
        self.action = cutting.choice("Action", ACTION_LABELS, changed=self.change_action)
        self.action.setToolTip("Cut through, score, or leave the object out of the cut.")
        self.text_button = cutting.push("Edit Text…", self.text_dialog)
        self.trace_button = cutting.push("Trace and Bleed…", self.trace_sticker)

        form = page.group("Position and size (mm)")
        form.setContentsMargins(12, 13, 12, 8)
        self.size_form = form
        self.fields = {}
        for pairs in ((("x", "X"), ("y", "Y")), (("width", "Width"), ("height", "Height"))):
            row = QHBoxLayout()
            row.setSpacing(10)
            for field, label in pairs:
                column = QVBoxLayout()
                column.setSpacing(2)
                spin = QDoubleSpinBox(decimals=3, minimum=-1000, maximum=1000, singleStep=0.1,
                                      keyboardTracking=False)
                spin.setAccessibleName(f"{label} (mm)")
                spin.valueChanged.connect(lambda _value, key=field: self.change_number(key))
                caption = styled_label(label, role="caption")
                caption.setBuddy(spin)
                column.addWidget(caption)
                column.addWidget(spin)
                row.addLayout(column, 1)
                self.fields[field] = spin
            form.addRow(row)
        self.fields["x"].setToolTip("Left edge of the selection, from the left edge of the page.")
        self.fields["y"].setToolTip("Top edge of the selection, from the top edge of the page.")
        self.lock_ratio = form.check("Lock proportions")
        self.fields["radius"] = form.number("Corner radius (mm)", 0, 0, 1000, 0.1, 3,
                                            lambda: self.change_number("radius"))

        orient = page.group("Rotate and flip")
        orient.setContentsMargins(12, 13, 12, 8)
        self.transform_buttons = {
            name: self.icon_button(icon, lambda operation=name: self.transform_objects(operation))
            for name, icon in TRANSFORM_ICONS.items()
        }
        angle = QDoubleSpinBox(decimals=3, minimum=-360, maximum=360, singleStep=1, keyboardTracking=False)
        angle.valueChanged.connect(lambda: self.change_number("rotation"))
        angle.setAccessibleName("Angle (°)")
        angle.setToolTip("Enter an exact angle. Positive angles rotate clockwise. "
                         "With Select, drag the round grip on the canvas.")
        caption = styled_label("Angle (°)", role="caption")
        caption.setBuddy(angle)
        orient.addRow(AdaptiveActions((self.transform_row(self.transform_buttons.values()),
                                       self.transform_row((caption, angle)))))
        self.fields["rotation"] = angle

        align = page.group("Align and space")
        align.setContentsMargins(12, 13, 12, 8)
        self.alignment_buttons = {}
        rows = []
        for icons in ALIGNMENT_ROWS:
            buttons = {icon.name: self.icon_button(icon, lambda option=icon.name: self.align_objects(option))
                       for icon in icons}
            self.alignment_buttons.update(buttons)
            rows.append(self.transform_row(buttons.values()))
        align.addRow(AdaptiveActions(rows))
        self.align_group = align.parentWidget()

        style = page.group("Appearance")
        self.line_color, pick_line = self.color_field(style, "Line color", "stroke")
        self.fill_color, pick_fill = self.color_field(style, "Fill color", "fill")
        self.color_buttons = (pick_line, pick_fill)
        self.line_style = style.choice("Line style", model.LINE_STYLES,
                                       changed=lambda: self.change_selected(line_style=self.line_style.currentData()))
        self.line_width = style.number("Line width (mm)", 0, 0, 5, 0.1, 2,
                                       lambda: self.change_selected(line_width_mm=self.line_width.value()))
        self.line_style.setToolTip("Appearance only. The cutter follows the whole line.")
        self.line_width.setSpecialValueText("Hairline")
        return tuple(group.parentWidget() for group in (cutting, form, orient, align, style))

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
        entry = QLineEdit(placeholderText="#rrggbb or blank")
        entry.setAccessibleName(label)
        entry.editingFinished.connect(lambda: self.change_color(channel, entry))
        pick = self.icon_button("color", lambda: self.pick_color(channel, entry))
        title = f"Pick {label.lower()}…"
        pick.setAccessibleName(title)
        pick.setToolTip(title)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(entry, 1)
        row.addWidget(pick)
        caption = QLabel(label)
        caption.setBuddy(entry)
        form.addRow(caption, row)
        return entry, pick

    def pick_color(self, field, entry):
        initial = QColor(entry.text() or "#313945")
        picked = QColorDialog.getColor(initial, self)
        if picked.isValid():
            self.change_selected(**{field: picked.name()})

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
        show.addActions((self.show_grid, self.rulers, self.show_mat, self.cut_border, self.print_border))
        view.addActions((self.snap, self.smart_snap))
        command(view, "Grid and Guides…", self.grid_dialog.show)
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
        command(arrange, "Bring Forward", lambda: self.reorder(1), "Ctrl+]", canvas=True)
        command(arrange, "Send Backward", lambda: self.reorder(-1), "Ctrl+[", canvas=True)
        align = objects.addMenu("Align")
        for choice in model.ALIGNMENTS:
            command(align, choice, lambda option=choice: self.align_objects(option))
        objects.addSeparator()
        command(objects, "Group", self.group)
        command(objects, "Ungroup", lambda: self.group(release=True))
        command(objects, "Replicate…", self.replicate_dialog)
        objects.addSeparator()
        command(objects, "Convert to Path", self.convert_selection)
        command(objects, "Simplify", lambda: self.convert_selection(True), "Ctrl+J")
        modify = objects.addMenu("Modify")
        command(modify, "Offset…", self.offset_selection)
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
        """Nothing selected means Properties shows the page, as in Photoshop."""
        self.select(())
        self.properties.verticalScrollBar().setValue(0)
        self.canvas.setFocus()

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

    def offset_selection(self):
        if not self.selection():
            return
        amount, accepted = QInputDialog.getDouble(self, "Offset", "Distance (mm). Use a negative number to inset.",
                                                   1, -20, 20, 2)
        if accepted:
            before = self.project.shapes
            try:
                copies = tuple(model.offset_shape(before[index], amount) for index in self.selection())
            except ValueError as error:
                self.message(str(error))
            else:
                self.commit_shapes(before + copies, range(len(before), len(before) + len(copies)))

    def modify_shapes(self, operation):
        try:
            shapes, selected = model.modify_selection(self.project.shapes, self.selection(), operation)
        except ValueError as error:
            self.message(str(error))
        else:
            self.commit_shapes(shapes, selected)

    def text_dialog(self, at=None):
        selection = self.selection()
        shape = self.project.shapes[selection[0]] if len(selection) == 1 else None
        spec = shape.text if shape else None
        try:
            families, family, notice = design_text.font_choices(spec)
        except ValueError as error:
            self.message(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Text" if spec else "Add Text")
        form = QFormLayout(dialog)
        value = QPlainTextEdit(spec.value if spec else "")
        value.setFixedHeight(92)
        font = QComboBox()
        set_choices(font, families, family)
        size = QDoubleSpinBox()
        size.setRange(1, 100)
        size.setValue(spec.size_mm if spec else 10)
        form.addRow("&Text", value)
        form.addRow("&Font", font)
        form.addRow("&Size (mm)", size)
        status = QLabel(notice, wordWrap=True)
        font.currentIndexChanged.connect(status.clear)
        form.addRow(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)

        def apply():
            updated = model.TextSpec(value.toPlainText(), font.currentData(), size.value())
            try:
                shapes, selected = design_text.edit(self.project, updated, selection[0] if spec else None)
                if not spec:
                    added = self.styled(shapes[-1])
                    if at is not None:
                        added = replace(added, x=at[0], y=at[1])
                    shapes = (*shapes[:-1], added)
            except ValueError as error:
                status.setText(str(error))
            else:
                self.commit_shapes(shapes, selected)
                dialog.accept()

        buttons.accepted.connect(apply)
        buttons.rejected.connect(dialog.reject)
        value.setFocus()
        dialog.exec()

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
        # Properties follows the selection: the page when nothing is selected.
        for group in self.document_groups:
            group.setVisible(not indices)
        for group in self.object_groups:
            group.setVisible(bool(indices))
        self.align_group.setVisible(units > 1)
        if not indices:
            title = "Document"
        elif shape is not None:
            title = model.object_name(shape)
        else:
            title = f"{len(indices)} objects"
        self.properties_title.setText(title)

        is_text = bool(shape and shape.text)
        can_trace = bool(shape and shape.sticker)
        self.cutting_form.setRowVisible(self.text_button, is_text)
        self.cutting_form.setRowVisible(self.trace_button, can_trace)
        self.trace_button.setEnabled(can_trace)
        self.trace_action.setEnabled(can_trace)

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
        self.size_form.setRowVisible(self.fields["radius"], bool(shape and shape.kind == "Rounded rectangle"))
        self.lock_ratio.setEnabled(shape is not None and min(shape.width, shape.height) > 0)
        self.size_form.setRowVisible(self.lock_ratio, area is None)
        for key, field in (("stroke", self.line_color), ("fill", self.fill_color)):
            colors = {getattr(shapes[i], key) for i in indices}
            field.setText(colors.pop() if len(colors) == 1 else "")
            field.setPlaceholderText("Mixed" if colors else "#rrggbb or blank")
        roles = {shapes[i].action for i in indices}
        self.action.setCurrentIndex(self.action.findData(roles.pop()) if len(roles) == 1 else -1)
        styles = {shapes[i].line_style for i in indices}
        self.line_style.setCurrentIndex(self.line_style.findData(styles.pop()) if len(styles) == 1 else -1)
        self.line_width.setValue(shape.line_width_mm if shape else 0)
        for widget in (self.duplicate_button, self.delete_button, self.duplicate_action, self.delete_action,
                       self.cut_action, self.copy_action):
            widget.setEnabled(bool(indices))
        self.paste_action.setEnabled(bool(_clipboard))
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

    def change_color(self, field, widget):
        value = widget.text().strip()
        if widget.placeholderText() == "Mixed" and not widget.isModified():
            return
        try:
            normalized = model.stroke_color(value)
        except ValueError as error:
            self.message(str(error))
        else:
            self.change_selected(**{field: normalized})

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

    def paste(self):
        if not _clipboard:
            return
        shapes = self.project.shapes
        self.commit_shapes(model.paste(shapes, _clipboard), range(len(shapes), len(shapes) + len(_clipboard)))

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

    def type_at(self, point):
        """Type tool: click text to edit it, anywhere else to place new text there."""
        shapes = self.project.shapes
        tolerance = 5 / max(0.02, self.canvas.transform().m11())
        for index in range(len(shapes) - 1, -1, -1):
            shape = shapes[index]
            if shape.visible and shape.text and model.hit(shape, (point.x(), point.y()), tolerance):
                self.select((index,))
                self.text_dialog()
                return
        self.select(())
        self.text_dialog(at=self.canvas.point(point))

    def change_tool(self):
        self.canvas.setFocus()
        self.canvas.cancel()
        self.canvas.polygon.clear()
        tool = WORKSPACE_TOOLS[self.tool.currentIndex()]
        cursors = {"Select": None, "Point Edit": None, "Type": Qt.CursorShape.IBeamCursor}
        cursor = cursors.get(tool.name, Qt.CursorShape.CrossCursor)
        if cursor is None:
            self.canvas.unsetCursor()
        else:
            self.canvas.setCursor(cursor)
        self.draw_options.setVisible(tool.name in DRAWING_TOOLS)
        self.radius_option.setVisible(tool.name == "Rounded rectangle")
        self.close_polygon.setVisible(tool.name in CLICK_PATH_TOOLS)
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

    def replicate_dialog(self):
        if not self.selection():
            self.message("Select the objects to replicate first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Replicate")
        form = QFormLayout(dialog)
        columns, rows, x_gap, y_gap = self.replicate_settings
        fields = []
        for label, value, top, decimals in (("Columns", columns, 32, 0), ("Rows", rows, 32, 0),
                                            ("Column spacing (mm)", x_gap, 1000, 2),
                                            ("Row spacing (mm)", y_gap, 1000, 2)):
            if decimals:
                field = QDoubleSpinBox(decimals=decimals, minimum=0, maximum=top, singleStep=0.1, value=value)
            else:
                field = QSpinBox(minimum=1, maximum=top, value=value)
            form.addRow(label, field)
            fields.append(field)
        status = QLabel(wordWrap=True)
        form.addRow(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Replicate")
        form.addRow(buttons)

        def apply():
            settings = tuple(field.value() for field in fields)
            error = self.replicate(*settings)
            if error:
                status.setText(error)
            else:
                self.replicate_settings = settings
                dialog.accept()

        buttons.accepted.connect(apply)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

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
