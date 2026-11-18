"""Printed-sheet presets, separate from material/blade presets.

Source: ProxyPress cameoPaperLayout() and STUDIO3_ARMS. These presets
describe printed geometry; they make no claim about a physical cut.
"""
import dataclasses

from . import regmarks, template


@dataclasses.dataclass(frozen=True)
class LayoutPreset:
    name: str
    sheet: str
    arm_mm: float

    def options(self):
        return dict(sheet=self.sheet, card='standard', portrait=False,
                    engine='proxypress', mode=template.ROUNDED,
                    radius_mm=3.2, mark_inset_mm=10.0, mark_length_mm=self.arm_mm,
                    mark_thickness_mm=1.0, pattern=regmarks.THREE,
                    bleed_mm=0.625)


BUILTIN = {
    'mtg-cameo-letter': LayoutPreset('Letter · MTG Cameo', 'Letter', 8.0264),
    'mtg-cameo-a4': LayoutPreset('A4 · MTG Cameo', 'A4', 9.398),
}
