"""State and checked single-spot plans for the material starting guide."""

from dataclasses import dataclass, replace

from . import geometry, graphtec, presets, testcut, transport

STARTING = presets.BUILTIN_BY_NAME["Card Maker starting point"].cut_settings()
GUIDE_TEXT = (
    f"Start with Force {STARTING.force}, Speed {STARTING.speed}, Depth {STARTING.depth}, and 1 pass. "
    "Use a blank test sheet of the same stock. Load it on the mat and send one pass.\n\n"
    "After the cutter stops, keep the mat loaded. Lift a corner to check the cut. "
    "If it still holds, repeat the same spot with the same settings. "
    "When it separates cleanly, use the tested settings.\n\n"
    "To try a different setting, move to the next spot before cutting again."
)
MAX_JOB_PASSES = 10


def starting_values(device, holder, blade_diameter):
    """Apply the guide values within the selected cutter's supported ranges."""
    return {"force": min(STARTING.force, device.max_pressure),
            "speed": min(STARTING.speed, device.max_speed),
            "depth": STARTING.depth if device.autoblade and holder == 1 and blade_diameter > 0 else None,
            "passes": 1}


class PassUnconfirmed(transport.TransportError):
    """A pass may have moved the cutter before returning an error."""


@dataclass(frozen=True)
class Placement:
    width: float
    height: float
    offset: tuple[float, float]
    mat: str


@dataclass(frozen=True)
class Trial:
    sheet: testcut.TestSheet
    connection: tuple
    placement: Placement

    @property
    def settings(self):
        return self.sheet.cells[0].settings

    def describe(self):
        location = self.sheet.cells[0].rect
        settings = self.settings
        depth = f"Depth {settings.depth}" if settings.depth is not None else "Depth set on the tool"
        return (f"X {location.x:g} mm, Y {location.y:g} mm\n"
                f"Force {settings.force} · Speed {settings.speed} · {depth} · Holder {settings.holder}")


class Guide:
    def __init__(self):
        self.reset()

    def reset(self):
        self.placement = None
        self._set_spot(1)

    def _set_spot(self, spot):
        self.spot = spot
        self.trial = None
        self.completed = 0
        self.interrupted = False

    def next_spot(self):
        self._set_spot(self.spot + 1)

    def invalidate(self):
        if self.trial is not None:
            self.interrupted = True

    def refusal(self, connection, cutter):
        if self.interrupted:
            return "Use a fresh spot to get a reliable pass count."
        if connection[0] != "auto" and not connection[1]:
            return "Select a Bluetooth cutter first."
        if self.trial and (self.trial.connection != connection or self.trial.sheet.device.name != cutter):
            return "The cutter changed. Return to the original cutter or start a fresh spot."
        return ""

    def prepare(self, connection, options, job, shape):
        reason = self.refusal(connection, options["cutter"])
        if reason:
            raise ValueError(reason)
        if self.trial:
            return self.trial
        device = graphtec.device_by_name(options["cutter"])
        blade = dict(options["blade_options"], track_enhancing=False)
        if not blade["blade_diameter_mm"]:
            raise ValueError("Choose a blade setting for the material test.")
        settings = graphtec.CutSettings(force=options["force"], speed=options["speed"],
                                        depth=options["depth"], passes=1, **blade).clamped(device)
        placement = self.placement or self.make_placement(job, options)
        sheet = testcut.build(device, settings, spot=self.spot, size_mm=testcut.DEFAULT_SIZE_MM,
                              sheet_w_mm=placement.width, sheet_h_mm=placement.height,
                              registration=False, offset_mm=placement.offset, shape=shape,
                              mat=placement.mat, end_position="start")
        return Trial(sheet, connection, placement)

    @staticmethod
    def make_placement(job, options):
        drawing = options.get("imported")
        if job:
            width, height = job.grid.sheet_w_mm, job.grid.sheet_h_mm
        elif options.get("use_document_page") and drawing and drawing.page_size_mm:
            width, height = drawing.page_size_mm
        else:
            width, height = geometry.sheet(options["sheet"]).size(options.get("portrait") is True)
        return Placement(width, height, options["offset_mm"], options["mat"])

    def prompt(self, trial):
        action = ("Keep the same mat and sheet loaded." if self.trial else
                  f"Use the same stock on a {trial.placement.width:g} × {trial.placement.height:g} mm test sheet.")
        return (f"{trial.sheet.device.name} · {trial.describe()}\n\n{action}\n"
                "The cutter returns to the origin after this test.")

    def record(self, trial, error=None):
        if error is None:
            self.trial = trial
            self.placement = trial.placement
            self.completed += 1
        elif isinstance(error, PassUnconfirmed):
            self.trial = trial
            self.placement = trial.placement
            self.invalidate()

    @property
    def tested_settings(self):
        if self.trial and not self.interrupted and 1 <= self.completed <= MAX_JOB_PASSES:
            return replace(self.trial.settings, passes=self.completed)
        return None

    def status(self):
        text = f"Test spot {self.spot}"
        if self.trial:
            text += f" · {self.completed} completed " + ("pass" if self.completed == 1 else "passes")
            text += "\n" + self.trial.describe()
        if self.interrupted:
            text += "\nThe pass count is uncertain. Use the next spot."
        elif self.completed > MAX_JOB_PASSES:
            text += f"\nThe cut job supports up to {MAX_JOB_PASSES} passes."
        return text


def run_pass(cutter, trial):
    if not cutter.can_read:
        raise transport.TransportError("A guided test needs a cutter connection that reports completion.")
    state = cutter.status()
    if state != "ready":
        raise transport.TransportError(f"The cutter is not ready ({state}).")
    try:
        return cutter.run(trial.sheet.steps, wait_done=True)
    except Exception as error:
        raise PassUnconfirmed(f"The last pass could not be confirmed. {error}") from error
