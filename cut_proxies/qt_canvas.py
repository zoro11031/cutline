"""Qt drawing primitives for the physical sheet and Design workspace."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from . import toolpath

PREVIEW_MODES = ("Cut paths", "Print PDF", "Overlay")


def rect(box):
    return QRectF(box.x, box.y, box.w, box.h)


def pen(color, width=0, dashed=False):
    pen_style = QPen(QColor(color), width)
    if dashed:
        pen_style.setStyle(Qt.PenStyle.DashLine)
    return pen_style


def draw_path(scene, contour, line, fill=Qt.BrushStyle.NoBrush):
    if not contour.points:
        return None
    path = QPainterPath()
    path.moveTo(*contour.points[0])
    for point in contour.points[1:]:
        path.lineTo(*point)
    if contour.closed:
        path.closeSubpath()
    return scene.addPath(path, line, QBrush(fill))


def draw_marks(scene, marks, *, ink=True, outline=False):
    hatch = QBrush(QColor("#849199"), Qt.BrushStyle.DiagCrossPattern)
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


class SheetView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setMinimumSize(320, 250)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_bounds = QRectF()
        self.setAccessibleName("Sheet preview")

    def fit(self):
        if not self.page_bounds.isEmpty():
            self.fitInView(self.page_bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if event.angleDelta().y() > 0 else 0.8
            scale = self.transform().m11()
            if 0.03 <= scale * factor <= 100:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class Preview(SheetView):
    def __init__(self):
        super().__init__()
        self.job = self.report = self.pdf_size = None
        self.pdf = QImage()
        self.mode = "Overlay"
        self.colorblind = False

    def show_job(self, job):
        self.job = job
        self.render_sheet()

    def show_pdf(self, png=None, size=None, report=None):
        self.pdf = QImage.fromData(png) if png else QImage()
        self.pdf_size, self.report = size, report
        self.render_sheet()

    def render_sheet(self):
        scene = self.scene()
        scene.clear()
        job = self.job if self.mode != "Print PDF" or self.pdf.isNull() else None
        image = not self.pdf.isNull() and (self.mode != "Cut paths" or job is None)
        if not job and not image:
            self.page_bounds = QRectF(0, 0, 250, 160)
            scene.addSimpleText("Choose a layout or open artwork").setPos(15, 15)
            self.fit()
            return
        width, height = (job.grid.sheet_w_mm, job.grid.sheet_h_mm) if job else self.pdf_size
        self.page_bounds = QRectF(0, 0, width, height)
        if image:
            self.page_bounds = self.page_bounds.united(QRectF(0, 0, *self.pdf_size))
        scene.setSceneRect(self.page_bounds.adjusted(-9, -9, 9, 9))
        scene.addRect(0, 0, width, height, pen("#81858b"), QBrush(Qt.GlobalColor.white))
        if image:
            item = scene.addPixmap(QPixmap.fromImage(self.pdf))
            item.setTransform(item.transform().scale(self.pdf_size[0] / self.pdf.width(),
                                                      self.pdf_size[1] / self.pdf.height()))
        if job:
            self.draw_job(scene, job, image)
        self.fit()

    def draw_job(self, scene, job, image):
        good = "#1764ad" if self.colorblind else "#15784e"
        bad = "#b55713" if self.colorblind else "#ae3332"
        if job.registration:
            draw_marks(scene, job.marks, ink=not image)
        for contour in job.paths:
            weed = toolpath.is_weed_path(contour)
            item = draw_path(scene, contour, pen(bad if job.blocked else "#3477b8" if weed else good,
                                                 dashed=weed or (self.colorblind and bool(job.blocked))))
            if item:
                item.setPos(*job.offset_mm)
        if image and job.registration and self.report:
            for found in self.report.marks:
                if found.ink_box:
                    credible = found.is_credible and (found.error_mm or 0) <= 0.5
                    scene.addRect(rect(found.ink_box), pen(good if credible else bad, 0.35, not credible))
