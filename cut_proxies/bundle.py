"""Read the matched PDF, physical SVG and registration settings from ProxyPress."""
from __future__ import annotations

import dataclasses
import io
import json
import math
import pathlib
from shutil import copyfileobj
import tempfile
import zipfile

from . import geometry, importer, regmarks, sheet


class BundleError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Bundle:
    path: pathlib.Path
    signature: tuple
    pdf: str
    rows: tuple[dict, ...]
    layout: dict
    turned: bool

    def row(self, number=None):
        if number is None and len(self.rows) == 1:
            return self.rows[0]
        for row in self.rows:
            if row['sheet'] == number:
                return row
        raise BundleError('Choose --bundle-sheet from ' +
                          ', '.join(str(row['sheet']) for row in self.rows) + '.')

    def artwork(self, row):
        self.check_current()
        try:
            with zipfile.ZipFile(self.path) as archive:
                imported = importer.load_svg(io.BytesIO(archive.read(row['svg'])))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, importer.ImportError_) as exc:
            raise BundleError(f'Could not read sheet {row["sheet"]} SVG: {exc}') from exc
        if len(imported.paths) != row['cards'] or not imported.page_size_mm:
            raise BundleError(f'Sheet {row["sheet"]} SVG does not match the export.')
        return imported

    def check_current(self):
        try:
            if _signature(self.path) == self.signature:
                return
        except OSError:
            pass
        raise BundleError('The ZIP changed on disk. Open the current export again.')

    def measure(self, row, marks, dpi):
        self.check_current()
        try:
            with zipfile.ZipFile(self.path) as archive, \
                 archive.open(self.pdf) as src, tempfile.TemporaryDirectory() as tmp:
                pdf = pathlib.Path(tmp) / 'sheet.pdf'
                with pdf.open('wb') as dst:
                    copyfileobj(src, dst)
                image = sheet.render(pdf, row['frontPage'], dpi, colour=True)
                turn = (270 if self.layout['portrait'] else 90) if self.turned else 0
                if turn:
                    image = image.rotate(turn, expand=True)
                size = (marks.sheet_w_mm, marks.sheet_h_mm)
                if any(abs(px - mm*dpi/25.4) > 1.5 for px, mm in zip(image.size, size)):
                    raise BundleError('The PDF front page size does not match the cutter sheet.')
                report = sheet.measure(pdf, marks, page=row['frontPage'], dpi=dpi,
                                       image=image, swap_page_axes=self.turned)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, sheet.SheetError) as exc:
            raise BundleError(f'Could not measure {self.pdf}: {exc}') from exc
        return dataclasses.replace(report, pdf=self.pdf), image


def _signature(path):
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def load(path):
    path = pathlib.Path(path).expanduser()
    try:
        signature = _signature(path)
        with zipfile.ZipFile(path) as archive:
            entries = set(archive.namelist())
            with archive.open('registration.json') as manifest:
                data = json.loads(manifest.read(1_048_577))
        if (data['format'], data['version'], data['units'], data['origin']) != (
                'proxypress-cameo', 1, 'mm', 'top left'):
            raise BundleError('This ZIP needs a supported ProxyPress export version.')
        pdf, page, mat, reg, rows = (data[key] for key in
                                   ('pdf', 'pdfPage', 'mat', 'registration', 'sheets'))
        if not pdf.lower().endswith('.pdf') or pdf not in entries or not rows:
            raise BundleError('The matched ZIP must contain a PDF and at least one sheet.')
        portrait = mat['orientation'] == 'portrait'
        width, height = float(mat['width']), float(mat['height'])
        turned = mat['orientation'] != page['orientation']
        expected = (height, width) if turned else (width, height)
        if (any(o['orientation'] not in ('portrait', 'landscape') for o in (mat, page)) or
                portrait != (width < height) or
                any(not math.isclose(float(page[k]), value, abs_tol=0.01)
                    for k, value in zip(('width', 'height'), expected))):
            raise BundleError('The PDF and cutter orientations do not match the sheet dimensions.')
        paper = next((paper.name for paper in geometry.SHEETS.values()
                      if math.isclose(paper.portrait_width_mm, min(width, height), abs_tol=0.01)
                      and math.isclose(paper.portrait_height_mm, max(width, height), abs_tol=0.01)), None)
        if paper is None:
            raise BundleError('This paper size is unavailable in Control Center.')
        pattern = {3: regmarks.THREE, 4: regmarks.FOUR}[reg['pattern']]
        inset, arm, weight = (float(reg[k]) for k in ('inset', 'armLength', 'thickness'))
        if (not (0 < inset < min(width, height)/2) or
                regmarks.clamp(inset, arm, weight) != (inset, arm, weight)):
            raise BundleError('The registration settings are outside the cutter range.')
        seen = set()
        page_number = 1
        for row in rows:
            n = row['sheet']
            if (any(type(row[key]) is not int or row[key] < 1
                    for key in ('sheet', 'frontPage', 'cards')) or n in seen or
                    row['frontPage'] != page_number or
                    (row['backPage'] is not None and
                     (type(row['backPage']) is not int or row['backPage'] != page_number+1)) or
                    row['svg'] != f'sheet-{n}.svg' or row['svg'] not in entries):
                raise BundleError('A sheet number, PDF page or matching SVG is missing or invalid.')
            seen.add(n)
            page_number += 2 if row['backPage'] is not None else 1
    except BundleError:
        raise
    except (AttributeError, OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise BundleError('Could not read a matched ProxyPress ZIP. Export it again with the current ProxyPress.') from exc
    layout = dict(sheet=paper, portrait=portrait, mark_inset_mm=inset,
                  mark_length_mm=arm, mark_thickness_mm=weight, pattern=pattern)
    return Bundle(path, signature, pdf, tuple(rows), layout, turned)
