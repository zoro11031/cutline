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
        if 'svg' not in row:
            raise BundleError('This ZIP has a Studio cut file. Open it from Studio, or export a fresh ZIP for direct cutting.')
        try:
            with zipfile.ZipFile(self.path) as archive:
                imported = importer.load_svg(io.BytesIO(archive.read(row['svg'])))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, importer.ImportError_) as exc:
            raise BundleError(f'Could not read sheet {row["sheet"]} SVG: {exc}') from exc
        if len(imported.paths) != row['cards'] or not imported.page_size_mm:
            raise BundleError(f'Sheet {row["sheet"]} SVG does not match the export.')
        return imported

    def marks(self):
        opts = self.layout
        size = geometry.SHEETS[opts['sheet']].size(opts['portrait'])
        return regmarks.marks(*size, opts['mark_inset_mm'], opts['mark_length_mm'],
                              opts['mark_thickness_mm'], opts['pattern'])

    def mat_placement(self):
        orientation = 'portrait' if self.layout['portrait'] else 'landscape'
        square = ' The black square goes at the top-left.' if self.layout['pattern'] == regmarks.THREE else ''
        return (f"Load the printed front face-up in {orientation}, aligned with "
                f"the top-left of the mat grid.{square}")

    def near_marks(self, row):
        hints = row.get('nearMarks')
        if not isinstance(hints, list):
            return None
        names = (('top-left square', 'top-right L', 'bottom-left L')
                 if self.layout['pattern'] == regmarks.THREE else
                 ('top-left L', 'top-right L', 'bottom-left L', 'bottom-right L'))
        return tuple(name for name in names if name in hints)

    def studio_file(self, row, folder):
        """Copy the paired Studio file or DXF into a caller-owned directory."""
        self.check_current()
        number = row['sheet']
        name = row.get('cutFile')
        if name not in (f'sheet-{number}.studio3', f'sheet-{number}.dxf'):
            raise BundleError(f'Sheet {number} has no matching Studio or DXF file.')
        target = pathlib.Path(folder) / name
        try:
            with zipfile.ZipFile(self.path) as archive, archive.open(name) as source:
                with target.open('wb') as destination:
                    copyfileobj(source, destination)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise BundleError(f'Could not copy the Studio file for sheet {number}: {exc}') from exc
        return target

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
            if 'registration.json' not in entries:
                raise BundleError('This ZIP is missing registration.json. In ProxyPress, use Download matching print and cut files.')
            with archive.open('registration.json') as manifest:
                data = json.loads(manifest.read(1_048_577))
        legacy = 'format' not in data and 'version' not in data
        if (data['units'], data['origin']) != ('mm', 'top left') or (
                not legacy and (data.get('format'), data.get('version')) != ('proxypress-cameo', 1)):
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
            svg = row.get('svg')
            cut_file = row.get('cutFile')
            valid_source = (svg == f'sheet-{n}.svg' and svg in entries) or (
                legacy and svg is None and cut_file in (f'sheet-{n}.studio3', f'sheet-{n}.dxf') and cut_file in entries)
            if (any(type(row[key]) is not int or row[key] < 1
                    for key in ('sheet', 'frontPage', 'cards')) or n in seen or
                    row['frontPage'] != page_number or
                    (row['backPage'] is not None and
                     (type(row['backPage']) is not int or row['backPage'] != page_number+1)) or
                    not valid_source):
                raise BundleError(f'Sheet {n} has an invalid PDF page mapping or is missing its matching cut file.')
            seen.add(n)
            page_number += 2 if row['backPage'] is not None else 1
    except BundleError:
        raise
    except zipfile.BadZipFile as exc:
        raise BundleError('This file could not be read as a ZIP archive.') from exc
    except KeyError as exc:
        raise BundleError(f'The ZIP is missing a required registration field ({exc.args[0]}).') from exc
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise BundleError(f'Could not read the ProxyPress ZIP: {exc}') from exc
    layout = dict(sheet=paper, portrait=portrait, mark_inset_mm=inset,
                  mark_length_mm=arm, mark_thickness_mm=weight, pattern=pattern)
    return Bundle(path, signature, pdf, tuple(rows), layout, turned)
