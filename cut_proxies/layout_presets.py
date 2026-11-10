"""Printed-sheet presets, separate from material/blade presets.

Source: the local Proxy Press proxy-press.html DEFAULTS, layout(), regArm(),
btnCameo.onclick and cameoApply handlers inspected 2026-11-09. These presets
describe printed geometry; they make no claim about a physical cut.
"""
import dataclasses
import math

from . import config, geometry, regmarks, template


@dataclasses.dataclass(frozen=True)
class LayoutPreset:
    name: str
    sheet: str

    def options(self):
        settings = config.PROXYPRESS_8UP
        grid = geometry.proxypress_layout(self.sheet, False, settings)
        # Proxy Press's regArm uses the OUTER slot grid, including bleed,
        # rather than the card faces. Preserve its quarter-mm floor exactly.
        bounds = grid.bounds.grown(settings.bleed_mm)
        inset = config.PROXYPRESS_8UP_MARKS['regInset']
        clearance_x = min(bounds.x - inset,
                          grid.sheet_w_mm - inset - bounds.x2) - 1.25
        clearance_y = min(bounds.y - inset,
                          grid.sheet_h_mm - inset - bounds.y2) - 1.25
        arm = max(5.0, min(math.floor(max(clearance_x, clearance_y) * 4) / 4, 20.0))
        return dict(sheet=self.sheet, card='standard', portrait=False,
                    engine='proxypress', mode=template.ROUNDED,
                    radius_mm=3.2, mark_inset_mm=10.0, mark_length_mm=arm,
                    mark_thickness_mm=1.0, pattern=regmarks.THREE,
                    bleed_mm=0.625)


BUILTIN = {
    'mtg-cameo-letter': LayoutPreset('Letter · MTG Cameo', 'Letter'),
    'mtg-cameo-a4': LayoutPreset('A4 · MTG Cameo', 'A4'),
}
