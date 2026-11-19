"""State and checked single-spot plans for the material starting guide."""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import geometry, graphtec, importer, job as cut_job, presets, regmarks, template, testcut, transport

STARTING = presets.BUILTIN_BY_NAME[presets.STARTING_NAME].cut_settings()
GUIDE_TEXT = (
    "Load a scrap or sheet of the same material. Cut one pass, check the sample, and repeat as needed."
)
TUNING_TIPS = (
    "Change one setting at a time on a fresh spot. Start with passes, then try force, speed and depth.\n\n"
    "Blade depth limits how far the blade can extend. Start with enough exposure for the stock. "
    "Excess exposure, force or passes can score the mat.\n\n"
    "Avoid jumping straight to maximum force or speed. If the carriage skips or the mat shifts, "
    "finish stopping the cutter and power-cycle it so the carriage can rehome."
)
OUTCOMES = {
    "": "Choose a symptom",
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


def has_cutter_endpoint(connection):
    return bool(connection[1])


def _connection_key(connection):
    return connection[:2] if len(connection) == 3 and connection[2] is None else connection


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
    registered: bool = False

    @property
    def marks(self):
        return regmarks.marks(self.width, self.height) if self.registered else None


@dataclass(frozen=True)
class Spot:
    number: int
    rect: geometry.Rect
    passes: int
    uncertain: bool = False

    @property
    def count(self):
        if self.uncertain:
            return f"{self.passes} confirmed · last pass uncertain"
        return f"{self.passes} {'pass' if self.passes == 1 else 'passes'}"


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
    def __init__(self, storage=None):
        self.storage = storage
        self.registered = False
        self.resume_error = ""
        self.reset()
        self.restore()

    def reset(self):
        self.placement = None
        self.history = ()
        self.resume_error = ""
        self._set_spot(1)

    def _set_spot(self, spot):
        self.spot = spot
        self.trial = None
        self.completed = 0
        self.interrupted = False
        self.uncertain = False
        self.outcome = ""

    @property
    def can_cut(self):
        return not self.interrupted and self.completed < MAX_JOB_PASSES

    @property
    def sheet_status(self):
        return ("Marked test sheet · Registration scan before each pass" if self.registered else
                "Blank test stock · Registration off")

    @property
    def current_spot(self):
        if self.trial is not None:
            return Spot(self.spot, self.trial.sheet.cells[0].rect, self.completed, self.uncertain)

    @property
    def spots(self):
        current = self.current_spot
        return (*self.history, current) if current is not None else self.history

    def inspect(self, outcome):
        self.outcome = outcome if outcome in OUTCOMES else ""

    def advice(self):
        if self.outcome == "holding" and self.completed >= MAX_JOB_PASSES:
            return "This job supports up to 10 passes. Try a different setting on a fresh spot."
        return ADVICE.get(self.outcome, "")

    def next_spot(self):
        if self.current_spot is not None:
            self.history += (self.current_spot,)
        self._set_spot(self.spot + 1)

    def invalidate(self):
        if self.trial is not None:
            self.interrupted = True

    def moved(self):
        if not self.registered:
            self.invalidate()

    def history_text(self):
        return "\n".join(f"Spot {spot.number} · {spot.count}" for spot in self.spots)

    def refusal(self, connection, cutter):
        if self.interrupted:
            return "Use a fresh spot to get a reliable pass count."
        if self.completed >= MAX_JOB_PASSES:
            return f"This spot already has {MAX_JOB_PASSES} passes. Use a fresh spot."
        if connection[0] != "auto" and not connection[1]:
            return "Select a Bluetooth cutter first."
        if self.trial and (self.trial.connection != _connection_key(connection) or self.trial.sheet.device.name != cutter):
            return "The cutter changed. Return to the original cutter or start a fresh spot."
        return ""

    def prepare(self, connection, options, job, shape):
        reason = self.refusal(connection, options["cutter"])
        if reason:
            raise ValueError(reason)
        if self.trial:
            if options["blade_options"].get("holder", 1) != self.trial.settings.holder:
                raise ValueError("Switch back to the tested tool holder or use a fresh spot.")
            return self.trial
        device = graphtec.device_by_name(options["cutter"])
        blade = dict(options["blade_options"], track_enhancing=False)
        if not blade["blade_diameter_mm"]:
            raise ValueError("Choose a blade setting for the material test.")
        settings = graphtec.CutSettings(force=options["force"], speed=options["speed"],
                                        depth=options["depth"], passes=1, **blade).clamped(device)
        placement = self.placement or self.make_placement(job, options)
        return self.build_trial(device, settings, placement, _connection_key(connection), shape,
                                options["blade_options"].get("track_enhancing", False))

    def build_trial(self, device, settings, placement, connection, shape, track):
        marks = placement.marks
        margin = marks.inset_mm + marks.length_mm + testcut.DEFAULT_GAP_MM if marks else testcut.DEFAULT_MARGIN_MM
        sheet = testcut.build(device, replace(settings, passes=1, track_enhancing=False),
                              spot=self.spot, size_mm=testcut.DEFAULT_SIZE_MM, margin_mm=margin, from_bottom=True,
                              sheet_w_mm=placement.width, sheet_h_mm=placement.height,
                              registration=False, offset_mm=placement.offset, shape=shape,
                              mat=placement.mat, end_position="start")
        if marks:
            placement_size = (placement.width, placement.height)
            image = importer.Imported(sheet.paths, sheet.size_mm, sheet.size_mm, "Material test",
                                      "millimetres", page_size_mm=placement_size, use_document_page=True)
            cut = asdict(sheet.cells[0].settings)
            setting = {key: cut.pop(key) for key in ("force", "speed", "depth", "passes")}
            plan = cut_job.build(cutter=device.name, imported=image, use_document_page=True,
                                 registration=True, pattern=marks.pattern, mark_inset_mm=marks.inset_mm,
                                 mark_length_mm=marks.length_mm, mark_thickness_mm=marks.thickness_mm,
                                 mat=placement.mat, end_position="start", offset_mm=placement.offset,
                                 blade_options=cut, **setting)
            if plan.blocked:
                raise ValueError(plan.blocked[0])
            sheet = replace(sheet, registration=True, stream=plan.stream, steps=plan.steps)
        return Trial(sheet, connection, placement, track)

    def make_placement(self, job, options):
        drawing = options.get("imported")
        if job:
            width, height = job.grid.sheet_w_mm, job.grid.sheet_h_mm
        elif options.get("use_document_page") and drawing and drawing.page_size_mm:
            width, height = drawing.page_size_mm
        else:
            width, height = geometry.sheet(options["sheet"]).size(options.get("portrait") is True)
        return Placement(width, height, options["offset_mm"], options["mat"], self.registered)

    def prompt(self, trial):
        if trial.placement.registered:
            action = "Load the marked test sheet face-up as shown. The cutter scans the marks before cutting."
        else:
            action = f"Load a {trial.placement.width:g} × {trial.placement.height:g} mm test sheet."
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
            self.uncertain = True

    @property
    def tested_settings(self):
        if self.trial and not self.interrupted and 1 <= self.completed <= MAX_JOB_PASSES:
            return replace(self.trial.settings, passes=self.completed, track_enhancing=self.trial.track_enhancing)
        return None

    def status(self):
        count = "pass" if self.completed == 1 else "passes"
        text = f"Test spot {self.spot} · {self.completed} completed {count}"
        if self.trial:
            text += "\n" + self.trial.describe()
        if self.interrupted:
            text += ("\nThe last pass is uncertain. Use the next spot." if self.uncertain else
                     "\nThe sheet moved. Use the next spot.")
        elif self.completed >= MAX_JOB_PASSES:
            text += f"\nThe cut job supports up to {MAX_JOB_PASSES} passes."
        elif self.trial:
            text += ("\nRepeat on the same marked sheet. You can keep it loaded or reload it." if self.registered else
                     "\nKeep the mat loaded to repeat this spot.")
        elif self.registered:
            text += "\nPrint the marked test sheet at actual size and load it face-up."
        return text

    def write_marked_sheet(self, path, job, options):
        placement = self.placement or self.make_placement(job, options)
        marks = placement.marks
        if marks is None:
            raise ValueError("Enable registration marks first.")
        width, height = placement.width, placement.height
        grid = geometry.Grid("imported", "Material test sheet", width <= height, width, height,
                             geometry.card("standard"), 0, 0, (), 0, 0)
        template.write_svg(path, grid, (), mk=marks, proof=True, page_outline=False)
        self.placement = placement
        self.persist()

    def persist(self, pending=None):
        if self.storage is None:
            return
        trial = pending or self.trial
        placement = self.placement or (trial.placement if trial else None)
        if not self.registered:
            return
        if placement is None:
            self.storage.unlink(missing_ok=True)
            return
        data = dict(version=1, spot=self.spot, completed=self.completed,
                    interrupted=self.interrupted or pending is not None,
                    placement=asdict(placement), history=[asdict(spot) for spot in self.history])
        if trial:
            data["trial"] = dict(device=trial.sheet.device.name, settings=asdict(trial.settings),
                                 connection=trial.connection, shape=trial.sheet.shape, track=trial.track_enhancing)
        self.storage.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.storage.parent, mode="w", encoding="utf-8", delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, allow_nan=False)
            temporary.replace(self.storage)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def restore(self):
        if self.storage is None:
            return
        try:
            data = json.loads(self.storage.read_text(encoding="utf-8"))
            if data["version"] != 1 or type(data["spot"]) is not int or data["spot"] < 1:
                raise ValueError("invalid history")
            placement = data["placement"]
            self.placement = Placement(placement["width"], placement["height"], tuple(placement["offset"]),
                                       placement["mat"], registered=True)
            if (not all(math.isfinite(value) and value > 0 for value in (self.placement.width, self.placement.height))
                    or len(self.placement.offset) != 2 or not all(map(math.isfinite, self.placement.offset))):
                raise ValueError("invalid placement")
            self.registered = True
            self.spot = data["spot"]
            self.completed = data["completed"]
            self.interrupted = data["interrupted"]
            self.uncertain = self.interrupted
            if type(self.completed) is not int or not 0 <= self.completed <= MAX_JOB_PASSES:
                raise ValueError("invalid pass count")
            self.history = tuple(Spot(row["number"], geometry.Rect(**row["rect"]), row["passes"], row["uncertain"])
                                 for row in data["history"])
            if "trial" in data:
                trial = data["trial"]
                connection = tuple(tuple(value) if isinstance(value, list) else value for value in trial["connection"])
                device = graphtec.device_by_name(trial["device"])
                self.trial = self.build_trial(device, graphtec.CutSettings(**trial["settings"]), self.placement,
                                              connection, trial["shape"], trial["track"])
            elif self.completed or self.interrupted:
                raise ValueError("missing cut settings")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
            self.registered = False
            self.reset()
            self.resume_error = "Could not reopen the marked test sheet history."


def run_pass(cutter, trial):
    if not cutter.can_read:
        raise transport.TransportError("A guided test needs a cutter connection that reports completion.")
    state = cutter.status()
    if state != "ready":
        raise transport.TransportError(f"The cutter is not ready ({state}).")
    try:
        return cutter.run(trial.sheet.steps, wait_done=True)
    except transport.RegistrationFailed:
        # The scan reply arrives before any path write.
        raise
    except Exception as error:
        raise PassUnconfirmed(f"The last pass could not be confirmed. {error}") from error
