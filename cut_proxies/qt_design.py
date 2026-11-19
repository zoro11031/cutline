"""Qt Design editor for the existing versioned Design projects."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from math import dist
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPixmap, QTransform
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QInputDialog, QLabel, QListWidget, QMessageBox,
                               QPlainTextEdit, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QWidget)

from . import design_text, editor_model as model, geometry, graphtec, importer, regmarks, sticker_assets, sticker_print, sticker_trace, template
from .qt_canvas import SheetView, ZoomControls, draw_contours, draw_feed_arrow, draw_marks, draw_paper, draw_path, pen, rect
from .qt_controls import (Page, ResponsiveInspector, Tasks, bar, button, set_choices, styled_label, workspace_header,
                          workspace_layout, workspace_tool_button, workspace_tools)

DESIGN_FILTER = "Design projects (*.scc-template);;Cut artwork (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
TOOLS = ("Select", "Point Edit", *model.KINDS, "Polygon", "Freehand")


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
        self.marquee = None
        self.draft = None
        self.active = ()

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
            draw_path(scene, template.CutPath(tuple(self.polygon), closed=False), pen("#2264aa", dashed=True))
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
        if tool == "Polygon":
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
            self.edit = "move" if self.select_at(self.start, shift) else "marquee"
            self.active = editor.selection()
        else:
            self.edit = "draw"
            self.freehand = [self.point(self.start)]
        event.accept()

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
        if self.start is not None and (self.anchor - event.position().toPoint()).manhattanLength() >= 2:
            self.update_drag(self.mapToScene(event.position().toPoint()))

    def update_drag(self, point):
        base = self.design.history.current.shapes
        delta = point - self.start
        if self.edit == "move" and self.active:
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
            self.draft = base + (shape,) if shape else None
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
        if (self.anchor - event.position().toPoint()).manhattanLength() >= 2:
            self.update_drag(self.mapToScene(event.position().toPoint()))
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
        if self.design.tool.currentData() == "Polygon" and event.button() == Qt.MouseButton.LeftButton:
            self.mousePressEvent(event)
            if self.polygon:
                self.finish_polygon()
        else:
            super().mouseDoubleClickEvent(event)

    def finish_polygon(self):
        if len(set(self.polygon)) < 3:
            self.design.message("Place three or more vertices to close a polygon.")
            return
        try:
            shape = model.shape_from_path(template.CutPath(tuple(self.polygon), closed=True))
        except ValueError as error:
            self.design.message(str(error))
            return
        self.polygon.clear()
        self.design.add_shape(shape)

    def cancel(self):
        self.start = self.edit = self.anchor = self.marquee = self.draft = None
        self.freehand.clear()

    def keyPressEvent(self, event):
        editor = self.design
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            self.polygon.clear()
            editor.select(())
            return
        if editor.tool.currentData() == "Polygon" and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
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

        self.tools_bar, tools = workspace_tools(self)
        self.document_title = styled_label()
        self.document_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tools.addWidget(self.document_title, 1)
        for action in (self.save_action, self.undo_action, self.redo_action):
            tools.addWidget(workspace_tool_button(action))
        self.canvas = Canvas(self)
        self.zoom = ZoomControls(self.canvas)
        tools.addWidget(self.zoom)
        body.addWidget(self.tools_bar)

        panes = QSplitter(Qt.Orientation.Horizontal)
        panes.setChildrenCollapsible(False)
        panes.setHandleWidth(12)
        body.addWidget(panes, 1)
        left = Page()
        palette = left.group("Drawing")
        self.tool = palette.choice("Tool", TOOLS, changed=self.change_tool)
        self.tool.setToolTip("Drag to draw or move objects. Shift-click extends the selection. Middle-drag pans the canvas.")
        palette.push("Add Shape", self.add_default)
        self.text_button = palette.push("Add Text…", self.text_dialog)
        palette.push("Place Image…", self.place_sticker)
        self.trace_button = palette.push("Trace and Bleed…", self.trace_sticker)
        self.export_tasks = Tasks(self)
        self.exporting_stickers = False
        objects = left.group("Objects")
        self.objects = QListWidget()
        self.objects.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.objects.itemSelectionChanged.connect(self.refresh_selection)
        objects.addRow(self.objects)
        self.duplicate_button = objects.push("Duplicate", self.duplicate)
        self.delete_button = objects.push("Delete", self.delete)
        more_objects = left.advanced("Object Options").group("Arrange")
        self.group_button = more_objects.push("Group", self.group)
        self.ungroup_button = more_objects.push("Ungroup", lambda: self.group(release=True))
        more_objects.push("Bring Forward", lambda: self.reorder(1))
        more_objects.push("Send Backward", lambda: self.reorder(-1))
        left_pane = left.finish()
        left_pane.setMinimumWidth(205)
        panes.addWidget(left_pane)
        panes.addWidget(self.canvas)

        self.inspector = ResponsiveInspector()
        self.inspector.setProperty("role", "settings")
        self.inspector.setAccessibleName("Design properties")
        self.inspector.setDocumentMode(True)
        self.inspector.tabBar().setDrawBase(False)
        self.inspector.setUsesScrollButtons(True)
        for label, build in (("Page Setup", self.page_panel), ("Transform", self.transform_panel),
                             ("Replicate", self.replicate_panel), ("Line and Fill", self.style_panel)):
            panel = Page()
            build(panel)
            self.inspector.addTab(panel.finish(), label)
        self.build_design_menus((new, open_file, self.save_action, save_as, import_art))
        panes.addWidget(self.inspector)
        panes.setStretchFactor(1, 1)
        panes.setSizes([205, 550, 328])

        footer_bar, footer = bar(self)
        self.status = styled_label(role="body")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.action_picker = QComboBox(self)
        set_choices(self.action_picker, model.REVIEW_ACTIONS)
        self.action_picker.setAccessibleName("Design Send action")
        self.action_picker.currentIndexChanged.connect(lambda: self.refresh_status())
        footer.addWidget(self.action_picker)
        self.review_button = button("Review in Send", lambda: self.review_paths(review),
                                    primary=True)
        footer.addWidget(self.review_button)
        body.addWidget(footer_bar)

        for label, key, callback in (("Group", "Ctrl+G", self.group),
                                    ("Ungroup", "Ctrl+Shift+G", lambda: self.group(release=True))):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(lambda checked=False, call=callback: call())
            self.canvas.addAction(action)
        self.refresh((0,) if project.shapes else ())

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
        media = page.group("Media")
        self.media_form = media
        names = (*geometry.SHEETS, "Custom")
        self.media = media.choice("Size", names, changed=self.change_size)
        self.orientation = media.choice("Orientation", {True: "Portrait", False: "Landscape"},
                                        changed=self.change_orientation)
        self.media_width = media.number("Width (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.media_height = media.number("Height (mm)", 100, 1, 1000, 0.1, 3, self.change_dimensions)
        self.mat = media.choice("Cutting mat", graphtec.MAT_OPTIONS, changed=self.change_mat)
        marks = page.group("Registration Marks")
        self.register = marks.check("Enable registration", changed=self.change_marks)
        self.page_advanced = advanced = page.advanced()
        marks = advanced.group("Mark Geometry")
        self.pattern = marks.choice("Pattern", {regmarks.THREE: "Type 1 (3 marks)", regmarks.FOUR: "4 marks"},
                                    changed=self.change_marks)
        self.inset = marks.number("Inset (mm)", 10, 2, 40, 0.1, 3, self.change_marks)
        self.length = marks.number("Length (mm)", 20, regmarks.MIN_LENGTH_MM,
                                  regmarks.MAX_LENGTH_MM, 0.1, 4, self.change_marks)
        self.thickness = marks.number("Thickness (mm)", 1, regmarks.MIN_THICKNESS_MM,
                                     regmarks.MAX_THICKNESS_MM, 0.05, 3, self.change_marks)
        view = advanced.group("Canvas")
        self.show_grid = view.check("Show Grid", True, self.canvas.redraw)
        self.show_mat = view.check("Show Cutting Mat", True, self.canvas.redraw)
        self.cut_border = view.check("Show Cut Border", True, self.canvas.redraw)
        self.print_border = view.check("Show Print Border", False, self.canvas.redraw)
        self.print_margin = view.number("Print margin (mm)", 5, 0, 25, 0.5, 1, self.canvas.redraw)
        self.print_margin.setToolTip("Set the margin you use on your printer.")
        self.rulers = view.check("Show Rulers", False, self.canvas.redraw)
        self.snap = view.check("Snap to Grid")
        self.snap.setToolTip("Snap drawn points and object moves to the grid spacing.")
        self.smart_snap = view.check("Enable Smart Snapping")
        self.smart_snap.setToolTip("Line up moving shapes with nearby edges or centers.")
        self.spacing = view.number("Grid spacing (mm)", 5, 0.5, 100, 0.5, 2, self.canvas.redraw)

    def transform_panel(self, page):
        form = page.group("Position and size")
        advanced = page.advanced().group("Shape options")
        self.fields = {}
        for key, label, lo, hi in (
                ("x", "X (mm)", -1000, 1000), ("y", "Y (mm)", -1000, 1000),
                ("width", "Width (mm)", -1000, 1000), ("height", "Height (mm)", -1000, 1000),
                ("rotation", "Rotation (°)", -360, 360), ("radius", "Corner radius (mm)", 0, 1000)):
            target = advanced if key == "radius" else form
            self.fields[key] = target.number(label, 0, lo, hi, 0.1, 3, lambda field=key: self.change_number(field))
        self.lock_ratio = form.check("Lock Aspect Ratio")
        self.flags = {}
        for key, label in (("flip_x", "Mirror Horizontally"), ("flip_y", "Mirror Vertically"), ("visible", "Visible")):
            self.flags[key] = advanced.check(label, changed=lambda field=key: self.change_flag(field))
        align = page.group("Align and space")
        self.alignment = align.choice("Alignment", model.ALIGNMENTS)
        self.align_button = align.push("Apply Alignment", self.align)

    def replicate_panel(self, page):
        panel = page.group("Rows and columns")
        self.columns = panel.number("Columns", 2, 1, 32)
        self.rows = panel.number("Rows", 2, 1, 32)
        self.x_gap = panel.number("Column spacing (mm)", 2, 0, 1000, 0.1, 2)
        self.y_gap = panel.number("Row spacing (mm)", 2, 0, 1000, 0.1, 2)
        self.replicate_button = panel.push("Replicate Selection", self.replicate)

    def style_panel(self, page):
        style = page.group("Object appearance")
        self.line_color = style.text("Line color", changed=lambda: self.change_color("stroke", self.line_color))
        self.fill_color = style.text("Fill color", changed=lambda: self.change_color("fill", self.fill_color))
        for widget in (self.line_color, self.fill_color):
            widget.setPlaceholderText("#rrggbb or blank")
        self.color_buttons = (button("Pick Line Color…", lambda: self.pick_color("stroke", self.line_color)),
                              button("Pick Fill Color…", lambda: self.pick_color("fill", self.fill_color)))
        style.actions(*self.color_buttons)
        self.line_style = style.choice("Line Style", model.LINE_STYLES,
                                       changed=lambda: self.change_selected(line_style=self.line_style.currentData()))
        self.line_width = style.number("Line Width (mm)", 0, 0, 5, 0.1, 2,
                                       lambda: self.change_selected(line_width_mm=self.line_width.value()))
        self.line_style.setToolTip("Appearance only. The cutter follows the whole line.")
        self.line_width.setSpecialValueText("Hairline")
        self.action = style.choice("Object action", model.ACTIONS, changed=self.change_action)

    def pick_color(self, field, entry):
        initial = QColor(entry.text() or "#313945")
        picked = QColorDialog.getColor(initial, self)
        if picked.isValid():
            self.change_selected(**{field: picked.name()})

    def build_design_menus(self, file_actions):
        def command(menu, label, callback, shortcut=None):
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False: callback())
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            return action

        files = self.menu_bar.addMenu("&File")
        files.addActions(file_actions)
        command(files, "Place Image…", self.place_sticker)
        command(files, "Export Print && Cut ZIP…", self.export_stickers)
        edit = self.menu_bar.addMenu("&Edit")
        edit.addActions((self.undo_action, self.redo_action))
        edit.addSeparator()
        command(edit, "Select All", lambda: self.select(
            i for i, shape in enumerate(self.project.shapes) if shape.visible), "Ctrl+A")
        self.duplicate_action = command(edit, "Duplicate", self.duplicate, "Ctrl+D")
        self.delete_action = command(edit, "Delete", self.delete, "Delete")
        for action in edit.actions()[-3:]:
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            self.canvas.addAction(action)

        view = self.menu_bar.addMenu("&View")
        zoom = view.addMenu("Zoom")
        for label, callback in (("Zoom In", lambda: self.canvas.zoom_by(1.25)),
                                ("Zoom Out", lambda: self.canvas.zoom_by(0.8)), ("Fit", self.canvas.fit)):
            command(zoom, label, callback)
        rotate = view.addMenu("Rotate Page")
        command(rotate, "Left", lambda: self.rotate_page(False))
        command(rotate, "Right", lambda: self.rotate_page(True))
        view.addSeparator()
        for label, field in (("Show Grid", self.show_grid), ("Snap to Grid", self.snap),
                             ("Enable Smart Snapping", self.smart_snap), ("Show Print Border", self.print_border),
                             ("Show Cut Border", self.cut_border)):
            action = view.addAction(label)
            action.setCheckable(True)
            action.setChecked(field.isChecked())
            action.toggled.connect(field.setChecked)
            field.toggled.connect(action.setChecked)
        command(view, "Grid Options…", lambda: self.show_page_options(self.show_grid))
        command(view, "Registration Marks…", lambda: self.show_page_options(self.pattern))
        command(view, "Show Design Page Settings…", lambda: self.inspector.setCurrentIndex(0), "Ctrl+F1")
        command(view, "Switch to Send", self.send_workspace.click, "Ctrl+F2")

        panels = self.menu_bar.addMenu("Panels")
        for label, index in (("Page Setup", 0), ("Fill Color", 3), ("Line Style", 3), ("Transform", 1), ("Replicate", 2)):
            command(panels, label, lambda selected=index: self.inspector.setCurrentIndex(selected))
        select = panels.addMenu("Select by Color")
        command(select, "Same Line Color", lambda: self.select_color("stroke"))
        command(select, "Same Fill Color", lambda: self.select_color("fill"))
        command(panels, "Offset…", self.offset_selection)

        objects = self.menu_bar.addMenu("Object")
        mirror = objects.addMenu("Mirror")
        command(mirror, "Horizontally", lambda: self.transform_objects("horizontal"))
        command(mirror, "Vertically", lambda: self.transform_objects("vertical"))
        arrange = objects.addMenu("Arrange")
        command(arrange, "Bring Forward", lambda: self.reorder(1))
        command(arrange, "Send Backward", lambda: self.reorder(-1))
        align = objects.addMenu("Align")
        for choice in model.ALIGNMENTS:
            command(align, choice, lambda option=choice: self.align_objects(option))
        rotate = objects.addMenu("Rotate")
        command(rotate, "Left", lambda: self.transform_objects("left"))
        command(rotate, "Right", lambda: self.transform_objects("right"))
        command(objects, "Replicate…", lambda: self.inspector.setCurrentIndex(2))
        command(objects, "Group", self.group)
        command(objects, "Ungroup", lambda: self.group(release=True))
        holders = objects.addMenu("Tool Holder")
        command(holders, "Use Current Settings", lambda: self.change_selected(holder=None))
        command(holders, "Tool Holder 1", lambda: self.change_selected(holder=1))
        second = command(holders, "Tool Holder 2", lambda: self.change_selected(holder=2))
        second.setEnabled(self.device.two_tools)
        command(objects, "Convert to Path", self.convert_selection)
        command(objects, "Simplify", lambda: self.convert_selection(True), "Ctrl+J")
        modify = objects.addMenu("Modify")
        command(modify, "Offset…", self.offset_selection)
        for operation in model.PATH_OPERATIONS:
            command(modify, operation, lambda name=operation: self.modify_shapes(name))
        command(objects, "Add or Edit Text…", self.text_dialog)
        self.trace_action = command(objects, "Trace and Bleed…", self.trace_sticker)

    def show_page_options(self, field=None):
        self.inspector.setCurrentIndex(0)
        self.page_advanced.disclosure.setChecked(True)
        page = self.inspector.currentWidget()
        QTimer.singleShot(0, self, lambda: page.ensureWidgetVisible(field or self.page_advanced.disclosure))

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
        self.alignment.setCurrentIndex(self.alignment.findData(option))
        self.align()

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

    def text_dialog(self):
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

    def review_paths(self, review):
        if any(shape.visible and shape.sticker for shape in self.project.shapes):
            self.export_stickers()
        else:
            review(self.project, self.action_picker.currentData())

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
        for number, shape in enumerate(self.project.shapes, 1):
            details = f" · Group {shape.group}" if shape.group else ""
            if shape.holder:
                details += f" · Tool {shape.holder}"
            self.objects.addItem(f"{number}. {shape.kind}{details}")
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
            field.setEnabled(self.register.isChecked())
        self.loading = prev

    def refresh_selection(self):
        if self.loading:
            return
        self.loading = True
        indices = self.selection()
        shapes = self.project.shapes
        shape = shapes[indices[0]] if len(indices) == 1 else None
        self.text_button.setText("Edit Text…" if shape and shape.text else "Add Text…")
        can_trace = bool(shape and shape.sticker)
        self.trace_button.setEnabled(can_trace)
        self.trace_action.setEnabled(can_trace)
        for key, field in self.fields.items():
            field.setEnabled(shape is not None and (key != "radius" or shape.kind == "Rounded rectangle"))
            if shape is not None:
                if key in ("width", "height"):
                    low = -1000 if shape.kind == "Line" else 0 if shape.kind == "Path" else 0.1
                    field.setRange(low, 1000)
                field.setValue(getattr(shape, key))
        self.lock_ratio.setEnabled(shape is not None and min(shape.width, shape.height) > 0)
        for key, field in self.flags.items():
            field.setEnabled(bool(indices))
            field.setChecked(shape is not None and getattr(shape, key))
        for key, field in (("stroke", self.line_color), ("fill", self.fill_color)):
            values = {getattr(shapes[i], key) for i in indices}
            field.setEnabled(bool(indices))
            field.setText(values.pop() if len(values) == 1 else "")
            field.setPlaceholderText("Mixed" if values else "#rrggbb or blank")
        roles = {shapes[i].action for i in indices}
        self.action.setEnabled(bool(indices))
        self.action.setCurrentIndex(self.action.findData(roles.pop()) if len(roles) == 1 else -1)
        for widget in (*self.color_buttons, self.line_style, self.line_width):
            widget.setEnabled(bool(indices))
        styles = {shapes[i].line_style for i in indices}
        self.line_style.setCurrentIndex(self.line_style.findData(styles.pop()) if len(styles) == 1 else -1)
        self.line_width.setValue(shape.line_width_mm if shape else 0)
        for widget in (self.duplicate_button, self.delete_button, self.replicate_button,
                       self.duplicate_action, self.delete_action):
            widget.setEnabled(bool(indices))
        enough = len(model.selected_units(shapes, indices)) > 1
        self.group_button.setEnabled(enough)
        self.align_button.setEnabled(enough)
        self.ungroup_button.setEnabled(any(shapes[i].group for i in indices))
        self.loading = False
        self.canvas.redraw()
        self.refresh_status()

    def refresh_status(self):
        project = self.project
        action = self.action_picker.currentData()
        count = len(project.artwork(action).paths)
        has_stickers = any(shape.visible and shape.sticker for shape in project.shapes)
        self.review_button.setText("Export Print && Cut…" if has_stickers else "Review in Send")
        self.review_button.setEnabled(count > 0 or has_stickers)
        self.action_picker.setEnabled(not has_stickers)
        self.undo_action.setEnabled(bool(self.history.past))
        self.redo_action.setEnabled(bool(self.history.future))
        self.save_action.setEnabled(self.path is None or self.saved != project)
        name = self.path.name if self.path else self.source or "Untitled"
        dirty = project != self.saved
        self.document_title.setText(name + (" *" if dirty else ""))
        self.document_title.setToolTip(f"{name} (unsaved changes)" if dirty else name)
        self.status.setText(f"{project.width:g} × {project.height:g} mm · {count} paths · "
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
        if len(selection) != 1:
            return
        value = self.fields[field].value()
        updates = {field: value}
        shape = self.project.shapes[selection[0]]
        if field in ("width", "height") and self.lock_ratio.isChecked() and value > 0 and min(
                shape.width, shape.height) > 0:
            other = "width" if field == "height" else "height"
            ratio = getattr(shape, other) / getattr(shape, field)
            value = max(self.fields[other].minimum() / ratio, min(value, 1000 / ratio))
            updates = {field: value, other: value * ratio}
        self.change_selected(**updates)

    def change_flag(self, field):
        self.change_selected(**{field: self.flags[field].isChecked()})

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

    def add_shape(self, shape):
        count = len(self.project.shapes)
        self.commit_shapes(self.project.shapes + (shape,), (count,))

    def add_default(self):
        tool = self.tool.currentData()
        if tool in model.KINDS:
            self.add_shape(model.Shape(tool, (self.project.width - 40)/2, (self.project.height - 30)/2))
        else:
            self.message("Select a shape tool before adding an object.")

    def change_tool(self):
        self.canvas.cancel()
        self.canvas.polygon.clear()
        if self.tool.currentData() in ("Select", "Point Edit"):
            self.canvas.unsetCursor()
        else:
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
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

    def align(self):
        try:
            shapes = model.align(self.project.shapes, self.selection(), self.alignment.currentData())
        except ValueError as error:
            self.message(str(error))
            return
        self.commit_shapes(shapes, self.selection())

    def replicate(self):
        shapes, selection = self.project.shapes, self.selection()
        try:
            copies = model.replicate(shapes, selection, self.columns.value(), self.rows.value(),
                                     self.x_gap.value(), self.y_gap.value())
        except ValueError as error:
            self.message(str(error))
            return
        self.commit_shapes(copies, (*selection, *range(len(shapes), len(copies))))

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
