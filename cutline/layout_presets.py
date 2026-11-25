"""Printed-sheet presets, separate from material/blade presets.

Source: the eight-up centred card sheet and STUDIO3_ARMS. These presets
describe printed geometry; they make no claim about a physical cut.
"""
import dataclasses
from functools import lru_cache
import json
from pathlib import Path

from . import config, importer, regmarks, template

_SCM_SOURCE = "Silhouette Card Maker template"


DEFAULT_KEY = 'default'


class DefaultLayout:
    name = 'Letter · Standard (8 cards)'

    @staticmethod
    def options():
        return dict(sheet=config.DEFAULT_SHEET, card=config.DEFAULT_CARD, portrait=None,
                    engine='scm', mode=template.ROUNDED, radius_mm=3.2,
                    mark_inset_mm=10.0, mark_length_mm=None, mark_thickness_mm=1.0,
                    pattern=regmarks.THREE, bleed_mm=0.0)


def uses_default(options):
    return (options["imported"] is None and not options["skip"]
            and options["registration"] and options["auto_search"]
            and all(options[key] == value for key, value in DefaultLayout.options().items()))


@dataclasses.dataclass(frozen=True)
class LayoutPreset:
    name: str
    sheet: str
    arm_mm: float

    def options(self):
        return dict(sheet=self.sheet, card='standard', portrait=False,
                    engine='centered', mode=template.ROUNDED,
                    radius_mm=3.2, mark_inset_mm=10.0, mark_length_mm=self.arm_mm,
                    mark_thickness_mm=1.0, pattern=regmarks.THREE,
                    bleed_mm=0.625)


BUILTIN = {
    'mtg-cameo-letter': LayoutPreset('Letter · MTG Cameo', 'Letter', 8.0264),
    'mtg-cameo-a4': LayoutPreset('A4 · MTG Cameo', 'A4', 9.398),
}


@dataclasses.dataclass(frozen=True)
class PublishedTemplate:
    name: str
    source: str
    sheet: str
    size: tuple[float, float]
    portrait: bool
    inset_mm: float
    arm_mm: float
    thickness_mm: float
    paths: tuple[template.CutPath, ...]

    def options(self):
        return dict(sheet=self.sheet, card='standard', portrait=self.portrait,
                    engine='scm', mode=template.ROUNDED, radius_mm=3.0,
                    mark_inset_mm=self.inset_mm, mark_length_mm=self.arm_mm,
                    mark_thickness_mm=self.thickness_mm, pattern=regmarks.THREE, bleed_mm=0)

    def artwork(self):
        xs, ys = zip(*(point for path in self.paths for point in path.points))
        return importer.Imported(self.paths, max(xs) - min(xs), max(ys) - min(ys),
                                 _SCM_SOURCE, Path(self.source).name, page_size_mm=self.size,
                                 use_document_page=True)


@lru_cache(maxsize=1)
def available():
    source = Path(__file__).with_name('assets') / 'scm' / 'cutting-templates.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    result = dict(BUILTIN)
    for row in data['templates']:
        paths = tuple(template.CutPath(tuple(map(tuple, ring)), True, label='Cut|#ff0000')
                      for ring in row['paths'])
        name = f"SCM {row['sheet']} · {row['card']} ({len(paths)})"
        if row['inset'] < 10:
            name += ' · Borderless'
        result[row['id']] = PublishedTemplate(name, row['source'], row['sheet'], tuple(row['size']),
                                               row['orientation'] == 'portrait', row['inset'], row['arm'],
                                               row['thickness'], paths)
    return result


def desktop_presets():
    return {**available(), DEFAULT_KEY: DefaultLayout()}


def is_published(drawing):
    return drawing is not None and drawing.source == _SCM_SOURCE
