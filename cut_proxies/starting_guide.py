"""State and checked single-spot plans for the material starting guide."""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import geometry, graphtec, importer, job as cut_job, presets, regmarks, template, testcut, transport

STARTING = presets.BUILTIN_BY_NAME[presets.STARTING_NAME].cut_settings()
GUIDE_TEXT = (
    "Use the same stock and laminate as your job. Choose a spot on the sheet and cut one pass."
)
TUNING_TIPS = (
    "After the cutter stops, keep the mat loaded and lift the sample corner. Repeat at the same spot "
    "until it separates, then use the counted passes. Compare force, speed and depth in that order, "
    "changing one setting at a time on a fresh spot.\n\n"
    "The starting settings are for a test on your own stock. Even matching machines, blades and materials "
    "can need different settings. Use the minimum blade exposure that works to protect the mat."
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
    "rough": "Reduce force first on a fresh spot, then test a higher speed separately. "
             "Heavy pressure at low speed can drag and tear laminate. Repeat a pass if the cleaner cut still holds.",
    "skipped": "Wait for the cutter to stop, then power-cycle it to rehome. Check that both rollers grip "
               "the mat. Try less force or lower speed on a fresh spot. Skipping often means the blade "
               "is taking too much in one pass. If it caught in the mat, reduce blade exposure. "
               "If the next cut tracks cleanly but stays attached, add a pass.",
    "mat": "Reduce blade depth one step on a fresh spot. If the cut still gouges the mat, lower force or use fewer passes.",
}
MAX_JOB_PASSES = 10
SAMPLE_SIZES = {15: "15 mm", 20: "20 mm", 25: "25 mm", 30: "30 mm", 40: "40 mm"}


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
    size_mm: float = testcut.DEFAULT_SIZE_MM

    @property
    def marks(self):
        return regmarks.marks(self.width, self.height) if self.registered else None

    @property
    def margin(self):
        marks = self.marks
        return marks.inset_mm + marks.length_mm + testcut.DEFAULT_GAP_MM if marks else testcut.DEFAULT_MARGIN_MM

    def grid(self):
        margin, size, gap = self.margin, self.size_mm, testcut.DEFAULT_GAP_MM
        cols, rows = testcut.grid_dimensions(self.width, self.height, margin, size, gap)
        marks = self.marks
        x, y = marks.origin_mm if marks else (0, 0)
        right, bottom = (x + marks.reg_width_mm, y + marks.reg_length_mm) if marks else (self.width, self.height)
        dx, dy = self.offset
        cells = {}
        for index in range(cols * rows):
            box = testcut.cell_rect(index, cols, self.height, margin, size, gap, from_bottom=True)
            if x <= box.x + dx and box.x2 + dx <= right and y <= box.y + dy and box.y2 + dy <= bottom:
                cells[index + 1] = box
        return cells


@dataclass(frozen=True)
class Spot:
    number: int
    rect: geometry.Rect
    passes: int
    uncertain: bool = False
    trial: "Trial | None" = None
    interrupted: bool = False

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
        self.size_mm = testcut.DEFAULT_SIZE_MM
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
        return not self.interrupted and not self.archived_spot and self.completed < MAX_JOB_PASSES

    @property
    def archived_spot(self):
        return next((spot for spot in self.history if spot.number == self.spot), None)

    @property
    def sheet_status(self):
        return ("Marked test sheet · Registration scan before each pass" if self.registered else
                "Blank test stock · Registration off")

    @property
    def current_spot(self):
        if self.trial is not None:
            return Spot(self.spot, self.trial.sheet.cells[0].rect, self.completed,
                        self.uncertain, self.trial, self.interrupted)

    @property
    def spots(self):
        current = self.current_spot
        return tuple(sorted((*self.history, current) if current else self.history, key=lambda spot: spot.number))

    def inspect(self, outcome):
        self.outcome = outcome if outcome in OUTCOMES else ""

    def advice(self):
        if self.outcome == "holding" and self.completed >= MAX_JOB_PASSES:
            return "This job supports up to 10 passes. Try a different setting on a fresh spot."
        return ADVICE.get(self.outcome, "")

    def select_spot(self, number, placement=None):
        placement = self.placement or placement
        if number == self.spot:
            return False
        if number < 1 or (placement and number not in placement.grid()):
            return False
        active = self.current_spot
        if active is not None:
            self.history += (active,)
        self._set_spot(number)
        saved = self.archived_spot
        if saved and saved.trial:
            self.history = tuple(spot for spot in self.history if spot.number != number)
            self.trial = saved.trial
            self.completed = saved.passes
            self.uncertain = saved.uncertain
            self.interrupted = saved.interrupted
        return True

    def next_spot(self, placement=None):
        placement = self.placement or placement
        used = {spot.number for spot in self.spots}
        if placement:
            numbers = tuple(placement.grid())
            available = [number for number in numbers if number != self.spot and number not in used]
            following = next((number for number in available if number > self.spot),
                             available[0] if available else None)
            return self.select_spot(following, placement) if following is not None else False
        return self.select_spot(self.spot + 1)

    def invalidate(self):
        if self.trial is not None:
            self.interrupted = True

    def moved(self):
        if not self.registered:
            self.invalidate()
            self.history = tuple(replace(spot, interrupted=True) for spot in self.history)

    def history_text(self):
        return "\n".join(f"Spot {spot.number} · {spot.count}" for spot in self.spots)

    def refusal(self, connection, cutter):
        if self.archived_spot:
            return "The original settings for this spot are unavailable. Choose a fresh spot."
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
        sheet = testcut.build(device, replace(settings, passes=1, track_enhancing=False),
                              spot=self.spot, size_mm=placement.size_mm, margin_mm=placement.margin, from_bottom=True,
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
        return Placement(width, height, options["offset_mm"], options["mat"], self.registered, self.size_mm)

    def preview_placement(self, job, options):
        if self.placement:
            return self.placement
        placement = self.make_placement(job, options)
        grid = placement.grid()
        if self.spot not in grid and grid:
            self._set_spot(next(iter(grid)))
        return placement

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
        if self.archived_spot:
            return (f"Test spot {self.spot} · {self.archived_spot.count}\n"
                    "The original settings for this spot are unavailable. Choose a fresh spot.")
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
                     "\nKeep the mat loaded and lift a corner to check the cut. Repeat here if it still holds.")
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
                    placement=asdict(placement), history=[self._spot_data(spot) for spot in self.history])
        if trial:
            data["trial"] = self._trial_data(trial)
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
                                       placement["mat"], registered=True,
                                       size_mm=placement.get("size_mm", testcut.DEFAULT_SIZE_MM))
            if (not all(math.isfinite(value) and value > 0 for value in (self.placement.width, self.placement.height))
                    or len(self.placement.offset) != 2 or not all(map(math.isfinite, self.placement.offset))):
                raise ValueError("invalid placement")
            if self.placement.size_mm not in SAMPLE_SIZES:
                raise ValueError("invalid sample size")
            self.size_mm = self.placement.size_mm
            self.registered = True
            self.spot = data["spot"]
            self.completed = data["completed"]
            self.interrupted = data["interrupted"]
            self.uncertain = self.interrupted
            if type(self.completed) is not int or not 0 <= self.completed <= MAX_JOB_PASSES:
                raise ValueError("invalid pass count")
            active = self.spot
            history = []
            for row in data["history"]:
                self.spot = row["number"]
                trial = self._restore_trial(row["trial"]) if row.get("trial") else None
                history.append(Spot(self.spot, geometry.Rect(**row["rect"]), row["passes"],
                                    row["uncertain"], trial, row.get("interrupted", row["uncertain"])))
            self.history = tuple(history)
            self.spot = active
            if "trial" in data:
                self.trial = self._restore_trial(data["trial"])
            elif self.completed or self.interrupted:
                raise ValueError("missing cut settings")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
            self.registered = False
            self.reset()
            self.resume_error = "Could not reopen the marked test sheet history."

    @staticmethod
    def _trial_data(trial):
        return dict(device=trial.sheet.device.name, settings=asdict(trial.settings),
                    connection=trial.connection, shape=trial.sheet.shape, track=trial.track_enhancing)

    def _spot_data(self, spot):
        return dict(number=spot.number, rect=asdict(spot.rect), passes=spot.passes,
                    uncertain=spot.uncertain, interrupted=spot.interrupted,
                    trial=self._trial_data(spot.trial) if spot.trial else None)

    def _restore_trial(self, trial):
        connection = tuple(tuple(value) if isinstance(value, list) else value for value in trial["connection"])
        device = graphtec.device_by_name(trial["device"])
        return self.build_trial(device, graphtec.CutSettings(**trial["settings"]), self.placement,
                                connection, trial["shape"], trial["track"])


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
