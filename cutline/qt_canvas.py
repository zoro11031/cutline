"""Qt drawing primitives for the physical sheet and Design workspace."""

from math import floor

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QWidget

from . import toolpath
from .qt_controls import button, styled_label

PREVIEW_MODES = ("Cut paths", "Print PDF", "Overlay")


def rect(box):
    return QRectF(box.x, box.y, box.w, box.h)


def pen(color, width=0, dashed=False):
    pen_style = QPen(QColor(color), width)
    if dashed:
        pen_style.setStyle(Qt.PenStyle.DashLine)
    return pen_style


def draw_path(scene, contour, line, fill=Qt.BrushStyle.NoBrush):
    return draw_contours(scene, (contour,), line, fill)


def draw_contours(scene, contours, line, fill=Qt.BrushStyle.NoBrush):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for contour in contours:
        if contour.points:
            path.moveTo(*contour.points[0])
            for point in contour.points[1:]:
                path.lineTo(*point)
            if contour.closed:
                path.closeSubpath()
    return None if path.isEmpty() else scene.addPath(path, line, QBrush(fill))


def draw_marks(scene, marks, *, ink=True, outline=False):
    hatch = QBrush(QColor(124, 108, 97, 115), Qt.BrushStyle.DiagCrossPattern)
    for zone in marks.corner_zones():
        scene.addRect(rect(zone), QPen(Qt.PenStyle.NoPen), hatch)
    if outline:
        x, y = marks.origin_mm
        scene.addRect(x, y, marks.reg_width_mm, marks.reg_length_mm, pen("#ca4242"))
    if ink:
        no_pen = QPen(Qt.PenStyle.NoPen)
        if marks.square:
            scene.addRect(rect(marks.square), no_pen, QBrush(Qt.GlobalColor.black))
        for ell in marks.ells:
            stroke = pen("#101217", ell.thickness)
            for arm in ell.arms():
                scene.addLine(*arm, stroke)


def draw_paper(scene, page, shadow=True):
    no_pen = QPen(Qt.PenStyle.NoPen)
    if shadow:
        for shift, alpha in ((2.6, 12), (1.8, 17), (0.9, 24)):
            scene.addRect(page.translated(shift, shift), no_pen, QBrush(QColor(12, 23, 37, alpha)))
    scene.addRect(page, pen("#9aa7b4"), QBrush(Qt.GlobalColor.white))


def draw_guides(scene, page, guides):
    guide_pen = pen("#00a3cc", 0, dashed=True)
    for orient, pos in guides:
        if orient == "h" and 0 <= pos <= page.height():
            scene.addLine(page.left(), pos, page.right(), pos, guide_pen)
        elif orient == "v" and 0 <= pos <= page.width():
            scene.addLine(pos, page.top(), pos, page.bottom(), guide_pen)


def draw_feed_arrow(scene, width, color):
    center = width / 2
    arrow = QPolygonF([QPointF(center + dx, y) for dx, y in (
        (0, -11), (5, -6), (2, -6), (2, -2), (-2, -2), (-2, -6), (-5, -6))])
    item = scene.addPolygon(arrow, QPen(Qt.PenStyle.NoPen), QBrush(QColor(color)))
    item.setToolTip("Feed this edge into the cutter")


def crosshair(scene, x, y, color, radius):
    stroke = pen(color, 0.27)
    scene.addLine(x - radius, y, x + radius, y, stroke)
    scene.addLine(x, y - radius, x, y + radius, stroke)


class SheetView(QGraphicsView):
    zoom_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setMinimumSize(320, 250)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_bounds = QRectF()
        self.fitted = True
        self.fit_scale = 1.0
        self.setAccessibleName("Sheet preview")

    def fit(self):
        if not self.page_bounds.isEmpty():
            self.fitInView(self.page_bounds, Qt.AspectRatioMode.KeepAspectRatio)
            self.fitted = True
            self.fit_scale = self.transform().m11()
            self.zoom_changed.emit(100)

    def set_page_bounds(self, bounds):
        changed = bounds != self.page_bounds
        self.page_bounds = bounds
        if changed:
            self.setSceneRect(bounds.adjusted(-1, -1, 1, 1))
        return changed

    def zoom_by(self, factor, *, mouse=False):
        scale = self.transform().m11() * factor
        ratio = scale / self.fit_scale
        if not 0.2 <= ratio <= 20:
            return
        anchor = (self.ViewportAnchor.AnchorUnderMouse if mouse else self.ViewportAnchor.AnchorViewCenter)
        self.setTransformationAnchor(anchor)
        self.scale(factor, factor)
        self.fitted = False
        self.zoom_changed.emit(round(100 * ratio))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.25 ** (event.angleDelta().y() / 120), mouse=True)
            event.accept()
        else:
            super().wheelEvent(event)

    def drawBackground(self, painter, exposed):
        base = self.palette().window().color()
        dark = base.lightness() < 128
        painter.fillRect(exposed, base.lighter(113) if dark else base.darker(105))
        if self.transform().m11() >= 0.8:
            grid = QColor(255, 255, 255, 23) if dark else QColor(22, 42, 67, 28)
            painter.setPen(QPen(grid, 0))
            step = 6
            for x in range(floor(exposed.left() / step) * step, floor(exposed.right()) + 1, step):
                for y in range(floor(exposed.top() / step) * step, floor(exposed.bottom()) + 1, step):
                    painter.drawPoint(x, y)


class ZoomControls(QWidget):
    def __init__(self, canvas, *, fit_button=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.out_button = button("−", lambda: canvas.zoom_by(0.8))
        self.level = styled_label("100%", "metric")
        self.level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.level.setMinimumWidth(52)
        self.in_button = button("+", lambda: canvas.zoom_by(1.25))
        self.fit_button = fit_button if fit_button is not None else button("Fit", canvas.fit)
        for widget in (self.out_button, self.in_button):
            widget.setFixedWidth(30)
            widget.setProperty("role", "workspaceTool")
        if fit_button is None:
            self.fit_button.setFixedWidth(49)
            self.fit_button.setProperty("role", "workspaceTool")
        for widget, label in ((self.out_button, "Zoom out"), (self.in_button, "Zoom in"),
                              (self.fit_button, "Fit to view")):
            widget.setAccessibleName(label)
            widget.setToolTip(label)
        for widget in (self.out_button, self.level, self.in_button, self.fit_button):
            row.addWidget(widget)
        canvas.zoom_changed.connect(lambda percent: self.level.setText(f"{percent}%"))


class Preview(SheetView):
    def __init__(self):
        super().__init__()
        self.job = self.report = self.pdf_size = None
        self.trial = None
        self.test_placement = None
        self.test_spots = ()
        self.test_grid = {}
        self.active_spot_rect = None
        self.on_spot_clicked = None
        self.pdf = QImage()
        self.mode = "Overlay"
        self.colorblind = False

    def mousePressEvent(self, event):
        if self.test_placement and self.on_spot_clicked:
            scene_pos = self.mapToScene(event.pos())
            if self.on_spot_clicked(scene_pos.x(), scene_pos.y()):
                event.accept()
                return
        super().mousePressEvent(event)

    def show_job(self, job):
        self.job = job
        self.render_sheet()

    def show_pdf(self, png=None, size=None, report=None):
        self.pdf = QImage.fromData(png) if png else QImage()
        self.pdf_size, self.report = size, report
        self.render_sheet()

    def show_trial(self, trial, spots=(), placement=None, active_rect=None, *, grid=None):
        placement = trial.placement if trial else placement
        grid = grid or {}
        if ((trial, spots, placement, active_rect, grid)
                != (self.trial, self.test_spots, self.test_placement, self.active_spot_rect, self.test_grid)):
            self.trial, self.test_spots, self.test_placement, self.active_spot_rect = trial, spots, placement, active_rect
            self.test_grid = grid
            self.render_sheet()

    def render_sheet(self):
        scene = self.scene()
        scene.clear()
        if self.test_placement:
            self.draw_trial(scene)
            return
        job = self.job if self.mode != "Print PDF" or self.pdf.isNull() else None
        image = not self.pdf.isNull() and (self.mode != "Cut paths" or job is None)
        if not job and not image:
            self.set_page_bounds(QRectF(0, 0, 250, 160))
            text = scene.addSimpleText("Choose a layout or open a cut template")
            text.setBrush(self.palette().windowText().color())
            text.setPos(self.page_bounds.center() - text.boundingRect().center())
            if self.fitted:
                self.fit()
            return
        width, height = (job.grid.sheet_w_mm, job.grid.sheet_h_mm) if job else self.pdf_size
        page = QRectF(0, 0, width, height)
        bounds = page
        if image:
            bounds = page.united(QRectF(0, 0, *self.pdf_size))
        refit = self.set_page_bounds(bounds.adjusted(-12, -12, 12, 12))
        draw_paper(scene, page)
        draw_feed_arrow(scene, width, self.palette().windowText().color())
        if image:
            item = scene.addPixmap(QPixmap.fromImage(self.pdf))
            item.setTransform(item.transform().scale(self.pdf_size[0] / self.pdf.width(),
                                                      self.pdf_size[1] / self.pdf.height()))
        if job:
            self.draw_job(scene, job, image)
        if refit or self.fitted:
            self.fit()

    def draw_trial(self, scene):
        placement = self.test_placement
        page = QRectF(0, 0, placement.width, placement.height)
        refit = self.set_page_bounds(page.adjusted(-12, -12, 12, 12))
        draw_paper(scene, page)
        draw_feed_arrow(scene, page.width(), self.palette().windowText().color())
        if placement.marks:
            draw_marks(scene, placement.marks)
        active = self.trial.sheet.cells[0] if self.trial else None
        active_rect = active.rect if active else self.active_spot_rect
        for number, area in self.test_grid.items():
            if area == active_rect:
                continue
            box = rect(area)
            scene.addRect(box, pen("#b5c2cc", 0.45))
            label = scene.addSimpleText(str(number))
            label.setBrush(QColor("#4f616e"))
            label.setScale(0.45)
            label.setPos(box.left() + 1, box.top() + 2)
        if active_rect:
            box = rect(active_rect)
            scene.addRect(box, pen("#000000", 0.75), QBrush(QColor(46, 142, 177, 45)))
            scene.addRect(box.adjusted(0.35, 0.35, -0.35, -0.35), pen("#ffffff", 0.45, dashed=True))
        for spot in self.test_spots:
            box = rect(spot.rect)
            color = "#984d12" if spot.uncertain else "#166356"
            item = scene.addRect(box, pen(color, 0.35), QBrush(QColor(49, 141, 125, 28)))
            item.setToolTip(f"Spot {spot.number} · {spot.count}")
            unit = "pass" if spot.passes == 1 else "passes"
            label = scene.addSimpleText(f"{spot.number}\n{spot.passes} {unit}{'?' if spot.uncertain else ''}")
            label.setBrush(QColor(color))
            label.setScale(0.45)
            label.setPos(box.left() + 1, box.top() + 2)
        active_num = (active.index + 1) if active else None
        if active_rect and (active_num is None or all(spot.number != active_num for spot in self.test_spots)):
            label = scene.addSimpleText(str(active_num) if active_num else "")
            label.setBrush(QColor("#12394d"))
            label.setScale(0.45)
            label.setPos(active_rect.x + 1, active_rect.y + 2)
        if self.trial:
            for path in self.trial.sheet.paths:
                draw_path(scene, path, pen("#103e64", 0.6))
        if refit or self.fitted:
            self.fit()
        elif active_rect:
            self.ensureVisible(rect(active_rect), 20, 20)

    def draw_job(self, scene, job, image):
        good = "#1764ad" if self.colorblind else "#15784e"
        bad = "#b55713" if self.colorblind else "#ae3332"
        if not image:
            crowded = {hint["index"] for hint in job.mask_hints}
            skipped = set(job.skip)
            for index, box in enumerate(job.grid.cards):
                if index not in skipped:
                    if index in crowded:
                        tint = QColor(234, 157, 60, 51)
                        if self.colorblind:
                            item = scene.addRect(rect(box), QPen(QColor(181, 87, 19), 0.4, Qt.PenStyle.DashLine),
                                                 QBrush(tint, Qt.BrushStyle.DiagCrossPattern))
                        else:
                            item = scene.addRect(rect(box), QPen(Qt.PenStyle.NoPen), QBrush(tint))
                        # Say what the tint means where it is: this card sits by the
                        # bottom-left mark and is covered while the cutter scans.
                        item.setToolTip("Next to the bottom-left mark: cover this card while the cutter scans, "
                                        "then uncover it before cutting.")
                        note = scene.addSimpleText("Cover during scan")
                        note.setBrush(QColor("#8a4410"))
                        note.setScale(0.42)
                        width = note.boundingRect().width() * note.scale()
                        note.setPos(box.x + (box.w - width) / 2, box.y + box.h / 2 - 2)
                    else:
                        tint = QColor(48, 126, 165, 18)
                        scene.addRect(rect(box), QPen(Qt.PenStyle.NoPen), QBrush(tint))
        if job.registration:
            draw_marks(scene, job.marks, ink=not image)
            crosshair(scene, *job.marks.origin_mm, "#ad561b", 2.2)
        for contour in job.paths:
            weed = toolpath.is_weed_path(contour)
            item = draw_path(scene, contour, pen(bad if job.blocked else "#3477b8" if weed else good,
                                                 dashed=weed or (self.colorblind and bool(job.blocked))))
            if item:
                item.setPos(*job.offset_mm)
        if image and job.registration and self.report:
            for found in self.report.marks:
                credible = found.is_credible and (found.error_mm or 0) <= 0.5
                color = good if credible else bad
                if found.ink_box:
                    scene.addRect(rect(found.ink_box), pen(color, 0.35, not credible))
                    if self.colorblind:
                        tag = scene.addSimpleText("OK" if credible else "!")
                        tag.setBrush(QColor(color))
                        tag.setScale(0.26)
                        tag.setPos(found.ink_box.x, found.ink_box.y - 4)
                if found.measured is not None:
                    crosshair(scene, *found.measured, color, 1.5)
