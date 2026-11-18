"""State and checked single-spot plans for the material starting guide."""

from dataclasses import dataclass, replace

from . import geometry, graphtec, presets, testcut, transport

STARTING = presets.BUILTIN_BY_NAME[presets.STARTING_NAME].cut_settings()
GUIDE_TEXT = (
    "Load a blank sheet of the same material. Choose a material preset or start with "
    f"force {STARTING.force}, speed {STARTING.speed}, depth {STARTING.depth} and one pass. "
    "Cut one pass, leave the mat loaded, then lift a corner of the sample and report what you see."
)
TUNING_TIPS = (
    "Change one setting at a time on a fresh spot. Start with passes, then try force, speed and depth.\n\n"
    "Blade depth limits how far the blade can extend. Start with enough exposure for the stock. "
    "Excess exposure, force or passes can score the mat.\n\n"
    "Avoid jumping straight to maximum force or speed. If the carriage skips or the mat shifts, "
    "finish stopping the cutter and power-cycle it so the carriage can rehome."
)
OUTCOMES = {
    "": "Choose what happened",
    "clean": "Separated cleanly",
    "holding": "Still attached",
    "shallow": "Barely marked the material",
    "rough": "Rough or rippled edge",
    "skipped": "Cutter skipped or slipped",
    "mat": "Cut deeply into the mat",
}
ADVICE = {
    "clean": "Use the completed pass count. Save these settings under this material’s name if you want to reuse them.",
    "holding": "Keep the mat loaded and repeat one pass in the same spot. Each completed pass counts toward your total.",
    "shallow": "Check that the blade is seated and set for the stock. Try slightly more force on a fresh spot. "
               "If the blade cannot extend far enough to reach through the stock, increase depth one step.",
    "rough": "Reduce force first on a fresh spot. For rippled laminate, try a small speed increase separately "
             "once the cutter is tracking reliably. Add a pass if the cleaner cut still holds, and check the blade for wear.",
    "skipped": "Wait for the cutter to stop, then power-cycle it to rehome. Reload the test sheet. "
               "Try lower force first and reduce speed if needed. Check that the blade is not catching in the mat.",
    "mat": "Reduce blade depth one step on a fresh spot. If the cut still gouges the mat, lower force or use fewer passes.",
}
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
    track_enhancing: bool = False

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
        self.outcome = ""

    @property
    def awaiting_result(self):
        return self.completed > 0 and not self.interrupted and not self.outcome

    @property
    def can_cut(self):
        return not self.interrupted and (
            not self.completed or (self.outcome == "holding" and self.completed < MAX_JOB_PASSES))

    def inspect(self, outcome):
        self.outcome = outcome if outcome in OUTCOMES else ""

    def advice(self):
        if self.outcome == "holding" and self.completed >= MAX_JOB_PASSES:
            return "This job supports up to 10 passes. Try a different setting on a fresh spot."
        return ADVICE.get(self.outcome, "")

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
        return Trial(sheet, connection, placement, options["blade_options"].get("track_enhancing", False))

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
            self.outcome = ""
        elif isinstance(error, PassUnconfirmed):
            self.trial = trial
            self.placement = trial.placement
            self.invalidate()

    @property
    def tested_settings(self):
        if self.trial and not self.interrupted and 1 <= self.completed <= MAX_JOB_PASSES:
            return replace(self.trial.settings, passes=self.completed, track_enhancing=self.trial.track_enhancing)
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
