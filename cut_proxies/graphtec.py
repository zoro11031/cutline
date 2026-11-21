"""The Graphtec / Silhouette command layer.

Every command byte in this file was read out of
`fablabnbg/inkscape-silhouette`, which recovered them from Silhouette Studio's
own traffic, and each one carries the reference it came from.  Nothing here is
inferred from a plausible-looking pattern: where the meaning of a command is
not known, that is said, and where a value is a magic constant copied verbatim
it is labelled as one.  The citations are to `silhouette/Graphtec.py` in that
project unless stated otherwise.

Two things about this protocol catch people out:

1. **Coordinates are sent (y, x), not (x, y).**  `M` and `D` take the long
   axis of the machine first.  See `move_mm_cmd` / `draw_mm_cmd` (Graphtec.py
   ~1339).  Every conversion from sheet mm to machine units happens in this
   module and nowhere else.

2. **A registration scan moves the origin.**  Once the sensor has found the
   marks the machine puts logical (0, 0) at the top-left mark, so cut
   coordinates must be measured from the mark, not from the paper corner.
   inkscape-silhouette does this by subtracting the mark origin from every
   point (Graphtec.py ~1537); so do we, in `CutJob`.

A cut is not a single blob of bytes - the registration scan has to be read
back before the paths can be sent - so a job is built as an ordered list of
`Step`s.  `stream()` flattens just the writes, which is what gets dumped and
inspected; `plan()` keeps the reads, which is what actually gets run.
"""

from __future__ import annotations

import dataclasses
import math

from . import toolpath

# --------------------------------------------------------------------------
# Framing (Graphtec.py 172-195)
# --------------------------------------------------------------------------

CMD_ETX = b"\x03"           # every command is terminated with this
CMD_ESC = b"\x1b"
ESC_EOT = b"\x04"           # escape: initialise the device
ESC_ENQ = b"\x05"           # escape: return status
ESC_NAK = b"\x15"           # escape: return tool setup (Cameo 4 and later)

# inkscape-silhouette Graphtec.py 198-205, identifiers returned by ESC NAK.
TOOL_NAMES = {0: "Empty", 1: "Ratchet blade", 2: "AutoBlade", 3: "Deep-cut blade",
              4: "Kraft blade", 5: "Rotary blade", 7: "Pen", 255: "Detection error"}


def tool_name(code: int) -> str:
    return TOOL_NAMES.get(code, f"Unknown tool (ID {code})")


QUERY_FIRMWARE_VERSION = b"FG"

RESP_READY = b"0"
RESP_MOVING = b"1"
RESP_UNLOADED = b"2"
RESP_DECODING = {RESP_READY: "ready", RESP_MOVING: "moving",
                 RESP_UNLOADED: "unloaded"}

# The reply a successful registration scan sends back: four spaces, a zero and
# an ETX (Graphtec.py ~1574). The sensor failure reply reports the overall
# result with no mark index. An unexpected reply is a separate transport error.
RESP_REGMARK_OK = b"    0\x03"
RESP_REGMARK_FAIL = b"    1\x03"

# 1 Silhouette unit = 1/20 mm = 0.05 mm (Graphtec.py 333-346).
SU_PER_MM = 20.0


def su(mm: float) -> int:
    """Millimetres to Silhouette units, rounded as the reference does."""
    return int(round(mm * SU_PER_MM))


def su_to_mm(units: int) -> float:
    return units / SU_PER_MM


def frame(commands) -> bytes:
    """ETX-terminate one command or a list of them (Graphtec.py 368-375)."""
    if isinstance(commands, (str, bytes)):
        commands = [commands]
    return b"".join((c.encode() if isinstance(c, str) else c) + CMD_ETX for c in commands)


# --------------------------------------------------------------------------
# Devices (Graphtec.py 131-263)
# --------------------------------------------------------------------------

VENDOR_GRAPHTEC = 0x0B4D
VENDOR_CAMEO5ALPHA = 0x3844


@dataclasses.dataclass(frozen=True)
class Device:
    name: str
    vendor_id: int
    product_id: int
    width_mm: float
    length_mm: float
    quad_regmarks: bool = False     # Cameo 5 Alpha's four-mark pattern
    max_pressure: int = 33
    max_speed: int = 10             # 30 on the Cameo 4 line, see below
    two_tools: bool = False
    cameo3_on: bool = False         # the newer command dialect

    @property
    def detects_tools(self) -> bool:
        # PRODUCT_LINE_CAMEO4 in inkscape-silhouette Graphtec.py 151-169.
        return self.max_speed == 30

    def supports_holder(self, holder: int) -> bool:
        return holder == 1 or (holder == 2 and self.two_tools)

    @property
    def autoblade(self) -> bool:
        return self.cameo3_on or self.product_id == 0x1132

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


def _d(name, pid, width, *, vendor=VENDOR_GRAPHTEC, quad=False,
       max_pressure=33, max_speed=10, two_tools=False, cameo3_on=False):
    return Device(name, vendor, pid, width, 3000.0, quad, max_pressure,
                  max_speed, two_tools, cameo3_on)


# Only the machines inkscape-silhouette has entries for.  `width_mm` is the
# cuttable width, which is what limits how wide a sheet can be.
DEVICES: tuple[Device, ...] = (
    _d("Silhouette Portrait", 0x1123, 206.0),
    _d("Silhouette Portrait 2", 0x1132, 203.0),
    _d("Silhouette Portrait 3", 0x113A, 203.0, max_speed=30, cameo3_on=True),
    _d("Silhouette Portrait 4", 0x113F, 216.0, max_speed=30, cameo3_on=True),
    _d("Silhouette Cameo", 0x1121, 304.0),
    _d("Silhouette Cameo 2", 0x112B, 304.0),
    _d("Silhouette Cameo 3", 0x112F, 304.8, two_tools=True, cameo3_on=True),
    _d("Silhouette Cameo 4", 0x1137, 304.8, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo 4 Plus", 0x1138, 372.0, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo 4 Pro", 0x1139, 600.0, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo 5", 0x1140, 330.2, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo 5 Plus", 0x1141, 372.0, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo Pro MK-II", 0x1146, 609.0, quad=True, max_speed=30,
       two_tools=True, cameo3_on=True),
    _d("Silhouette Cameo 5 Alpha", 0x0001, 330.2, vendor=VENDOR_CAMEO5ALPHA,
       quad=True, max_pressure=40, max_speed=30, two_tools=True,
       cameo3_on=True),
    _d("Silhouette Cameo 5 Alpha Plus", 0x0002, 372.0,
       vendor=VENDOR_CAMEO5ALPHA, quad=True, max_pressure=40, max_speed=30,
       two_tools=True, cameo3_on=True),
)

DEVICES_BY_ID = {(d.vendor_id, d.product_id): d for d in DEVICES}


def device_for(vendor_id: int, product_id: int) -> Device | None:
    return DEVICES_BY_ID.get((vendor_id, product_id))


def device_by_name(name: str) -> Device:
    want = name.lower().replace("_", " ").replace("-", " ")
    for d in DEVICES:
        if d.name.lower().replace("-", " ") == want:
            return d
    raise KeyError(f"unknown cutter {name!r}; known: "
                   + ", ".join(d.name for d in DEVICES))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Tool:
    """Per-tool-holder commands (Graphtec.py 378-415).

    Holder 1 is the left/only holder; holder 2 exists on the Cameo 3 and
    later.  On a two-tool machine the holder number is part of most commands,
    which is why these are built here rather than written as literals.
    """

    holder: int = 1

    def select(self) -> str:
        return f"J{self.holder}"

    def pressure(self, grams_index: int) -> str:
        """Blade force, 1-33 (1-40 on the Cameo 5 Alpha).  Unitless index."""
        return f"FX{grams_index},{self.holder}"

    def speed(self, speed: int) -> str:
        return f"!{speed},{self.holder}"

    def depth(self, depth: int) -> str:
        """AutoBlade depth, 0-10.  Holder 1 only."""
        return f"TF{depth},{self.holder}"

    def cutter_offset(self, x_mm: float, y_mm: float) -> str:
        return f"FC{su(x_mm)},{su(y_mm)},{self.holder}"

    def lift(self, on: bool) -> str:
        """Lift the head at sharp angles rather than pivoting in the media."""
        return f"FE{1 if on else 0},{self.holder}"

    def sharpen_corners(self, start: int, end: int) -> tuple[str, str]:
        return (f"FF{start},0,{self.holder}",
                f"FF{start},{end},{self.holder}")


def acceleration(level: int) -> str:
    """TJa.  Graphtec.py 1335; the scale is not documented upstream."""
    return f"TJ{level}"


def blade_diameter(mm: float) -> str:
    """FC without a holder: the circle the blade swings on a direction change.

    robocut's note, quoted in Graphtec.py ~1246: 0 for a pen, 18 for cutting,
    and a standard blade is 0.9 mm.  Values above ~46 are ignored by the
    device.
    """
    return f"FC{su(mm)}"


def move_mm(x_mm: float, y_mm: float) -> str:
    """Pen-up move.  Note the (y, x) order (Graphtec.py 1339)."""
    return f"M{su(y_mm)},{su(x_mm)}"


def draw_mm(x_mm: float, y_mm: float) -> str:
    """Pen-down move (Graphtec.py 1343)."""
    return f"D{su(y_mm)},{su(x_mm)}"


def upper_left_mm(x_mm: float, y_mm: float) -> str:
    r"""Clip-area top-left.  The command really is a backslash
    (Graphtec.py 1347)."""
    return f"\\{su(y_mm)},{su(x_mm)}"


def lower_right_mm(x_mm: float, y_mm: float) -> str:
    """Clip-area bottom-right (Graphtec.py 1351)."""
    return f"Z{su(y_mm)},{su(x_mm)}"


def auto_regmark_search_mm(length_mm: float, width_mm: float, top_mm: float,
                           left_mm: float) -> str:
    """TB123: search for three marks (Graphtec.py 1355)."""
    return (f"TB123,{su(length_mm)},{su(width_mm)},{su(top_mm)},"
            f"{su(left_mm)}")


def auto_quad_regmark_search_mm(length_mm: float, width_mm: float,
                                top_mm: float, left_mm: float) -> str:
    """TB124: search for four marks, Cameo 5 Alpha (Graphtec.py 1360)."""
    return (f"TB124,{su(length_mm)},{su(width_mm)},{su(top_mm)},"
            f"{su(left_mm)}")


def manual_regmark_mm(length_mm: float, width_mm: float) -> str:
    """TB23: the operator has already parked the head on the first mark."""
    return f"TB23,{su(length_mm)},{su(width_mm)}"


# The reference registration preamble (Graphtec.py 1549-1555). Use the
# recovered constants except on the Cameo 4, for which the reference docs
# also show a geometry-specific TB51/TB53 pair.
REGMARK_PREAMBLE = (
    "TB50,0",       # 0 = portrait mode.  With registration it is always 0.
    "TB99",         # undocumented upstream; always sent before a scan
    "TB52,2",       # mark type: 0 = original/SD, 2 = Cameo/Portrait
    "TB51,400",     # default arm length, copied from the reference driver
    "TB53,10",      # default stroke width, copied from the reference driver
    "TB55,1",       # undocumented upstream
)

# inkscape-silhouette Graphtec.py CAMEO_MATS and set_cutting_mat().
MAT_OPTIONS = {
    "auto": "Automatic",
    "none": "No cutting mat",
    "cameo-12x12": "Cameo 12 × 12 in",
    "cameo-12x24": "Cameo 12 × 24 in",
    "cameo-plus-15x15": "Cameo Plus 15 × 15 in",
    "cameo-pro-24x24": "Cameo Pro 24 × 24 in",
    "portrait-8x12": "Portrait 8 × 12 in",
    "portrait-8.5x12": "Portrait 8.5 × 12 in",
}
MATS = {
    "cameo-12x12": ("TG1", 304.8, 304.8),
    "cameo-12x24": ("TG2", 304.8, 609.6),
    "cameo-plus-15x15": ("TG8", 381.0, 381.0),
    "cameo-pro-24x24": ("TG9", 609.6, 609.6),
    "portrait-8x12": ("TG3", 203.2, 304.8),
    "portrait-8.5x12": ("TG11", 215.9, 304.8),
}
END_POSITIONS = {"below": "Feed", "start": "Return to Origin"}


def cutting_mat_commands(device: Device, mat: str, sheet_width: float,
                         sheet_height: float, registration: bool) -> tuple[str, ...]:
    if mat == "auto":
        return registration_mat_commands(device, sheet_width, sheet_height) if registration else ()
    if not device.cameo3_on:
        return ()
    command, width, height = (("TG0", device.width_mm, device.length_mm) if mat == "none"
                              else MATS[mat])
    return (command, "FN0", "TB50,0", upper_left_mm(0, 0), lower_right_mm(width, height))


def return_to_start_commands(device: Device) -> tuple[str, ...]:
    # inkscape-silhouette Graphtec.py plot(), endposition='start'.
    if not device.cameo3_on:
        return ("H",)
    return ("L0", upper_left_mm(0, 0), move_mm(0, 0), "J0", "FN0", "TB50,0")


def registration_mat_commands(device: Device, width_mm: float, height_mm: float) -> tuple[str, ...]:
    """Standard Cameo mat setup, from Graphtec.py set_cutting_mat."""
    short, long = 304.8, 609.6
    if not device.cameo3_on or device.width_mm < short or width_mm > short or height_mm > long:
        return ()
    mat, height = ("TG1", short) if height_mm <= short else ("TG2", long)
    return (mat, "FN0", "TB50,0", upper_left_mm(0, 0), lower_right_mm(short, height))


def captured_letter_scan(device, length, width, origin, mark_shape):
    """The captured Letter window, including the standalone layout's 8 mm arms."""
    if device.product_id != 0x1137 or mark_shape is None:
        return False
    arm, stroke = mark_shape
    return (tuple(map(su, (length, width, *origin, stroke))) == (3918, 5188, 200, 200, 20)
            and su(arm) in (160, 161))


def registration_preamble(device: Device, mark_shape_mm: tuple[float, float] | None) -> tuple[str, ...]:
    # docs/cameo4-usbprint-observations.md: Cameo 4 uses 200 and 20 for
    # 10 mm arms and 1 mm strokes. Keep the recovered defaults on other models.
    if device.product_id != 0x1137 or mark_shape_mm is None:
        return REGMARK_PREAMBLE
    arm, stroke = mark_shape_mm
    return (*REGMARK_PREAMBLE[:3], f"TB51,{su(arm)}", f"TB53,{su(stroke)}",
            REGMARK_PREAMBLE[-1])



# --------------------------------------------------------------------------
# Media (Graphtec.py ~80-120)
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Media:
    id: int
    pressure: int
    speed: int
    depth: int
    blade: str
    name: str


# Only the entries that matter for card: the stock here is heavier than
# anything in the table, so these are a starting point, not an answer.
MEDIA = (
    Media(112, 10, 10, 1, "blue", "Cardstock, Plain"),
    Media(122, 30, 10, 1, "yellow", "Cardstock 40-60 lbs (90g)"),
    Media(123, 30, 10, 1, "yellow", "Cover 40-60 lbs (170g)"),
    Media(127, 30, 10, 1, "red", "Index 90 lbs (165g)"),
    Media(135, 20, 10, 1, "red", "Tag 100 lbs (275g)"),
    Media(132, 5, 10, 1, "blue", "Print Paper Light Weight"),
    Media(133, 25, 10, 1, "yellow", "Print Paper Medium Weight"),
)
MEDIA_BY_ID = {m.id: m for m in MEDIA}


# --------------------------------------------------------------------------
# A job
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Step:
    """One thing to do: write bytes, or read and check them."""

    kind: str                   # "write" | "expect"
    data: bytes = b""
    expect: bytes = b""
    timeout_ms: int = 10000
    note: str = ""
    pace: bool = False          # long path blocks use command-aligned batches
    wait_ready: bool = False    # finish a standalone movement before the next step


@dataclasses.dataclass
class CutSettings:
    """Blade settings for one pass.

    `force` and `speed` are the machine's own unitless indices, the same
    numbers Silhouette Studio shows.  There is no conversion to grams or
    mm/s to be had: the mapping is not published and was not recovered.
    """

    force: int = 20
    speed: int = 5
    depth: int | None = None            # AutoBlade only
    acceleration: int | None = None
    blade_diameter_mm: float = 0.9
    holder: int = 1
    passes: int = 1
    lift_head: bool = True
    sharpen_corners: bool = False
    media: int | None = None            # advisory; no command carries it

    # inkscape-silhouette Graphtec.py 1191-1196: FF takes tenths of a mm.
    corner_start_mm: float = 0.1
    corner_end_mm: float = 0.1
    path_order: str = "design"
    pass_order: str = "sheet"
    alternate_direction: bool = False
    overcut_mm: float = 0.0
    interior_first: bool = False
    delicate_media: bool = False
    track_enhancing: bool = False
    # Staged passes: the first `light_passes` of `passes` at a lighter force
    # and speed (None keeps the full value), the rest at `force`/`speed`.
    # No command of its own: job.build sends the stages as two setting
    # blocks through plan_matrix, exactly as a grouped Send does.
    light_passes: int = 0
    light_force: int | None = None
    light_speed: int | None = None

    @property
    def custom_toolpath(self) -> bool:
        return (self.path_order != "design" or self.pass_order != "sheet"
                or self.alternate_direction or self.overcut_mm != 0
                or self.interior_first or self.delicate_media)

    def clamped(self, device: Device) -> "CutSettings":
        out = dataclasses.replace(self)
        if not device.supports_holder(self.holder):
            raise ValueError(f"{device.name} has no tool holder {self.holder}")
        out.force = max(1, min(self.force, device.max_pressure))
        out.speed = max(1, min(self.speed, device.max_speed))
        if not device.autoblade or self.holder != 1:
            out.depth = None
        elif out.depth is not None:
            out.depth = max(0, min(out.depth, 10))
        out.passes = max(1, min(self.passes, 10))
        out.light_passes = max(0, min(self.light_passes, 9))
        if (self.path_order not in toolpath.PATH_ORDERS
                or self.pass_order not in toolpath.PASS_ORDERS):
            raise ValueError("Unknown path or pass order.")
        if not math.isfinite(self.overcut_mm) or self.overcut_mm < 0:
            raise ValueError("Overcut must be a finite distance of zero or more millimetres.")
        if not device.cameo3_on or not self.blade_diameter_mm:
            out.sharpen_corners = False
        if out.sharpen_corners and not all(math.isfinite(v) and v >= 0
                                         for v in (self.corner_start_mm, self.corner_end_mm)):
            raise ValueError("Line segment extensions must be finite and nonnegative.")
        return out

    def warnings(self, device: Device) -> list[str]:
        out = []
        if self.force > device.max_pressure:
            out.append(f"force {self.force} is above the {device.name} "
                       f"maximum of {device.max_pressure}; it will be clamped")
        if self.speed > device.max_speed:
            out.append(f"speed {self.speed} is above the {device.name} "
                       f"maximum of {device.max_speed}; it will be clamped")
        if self.depth is not None and not device.autoblade:
            out.append(f"{device.name} does not support automatic depth. "
                       "Set the blade depth on the tool.")
        elif self.depth is not None and self.holder != 1:
            out.append("AutoBlade depth only applies to tool holder 1; it "
                       "will not be sent")
        if self.sharpen_corners and (not device.cameo3_on or not self.blade_diameter_mm):
            out.append("Line Segment Overcut requires a blade on a cutter that accepts corner extensions.")
        if self.track_enhancing and min(self.force, device.max_pressure) < 19:
            out.append("The reference driver reports that Track Enhancing requires force 19 or higher.")
        return out


class CutJob:
    """Builds the full step list for one registered cut.

    The caller supplies paths in **sheet millimetres, top-left origin**.  This
    class is the only place that knows about the mark origin shift, the (y, x)
    order or Silhouette units.
    """

    def __init__(self, device: Device, settings: CutSettings,
                 origin_mm: tuple[float, float] = (0.0, 0.0),
                 reg_width_mm: float = 0.0, reg_length_mm: float = 0.0,
                 registration: bool = True, auto_search: bool = True,
                 search_mm: float | None = None, quad: bool = False,
                 offset_mm: tuple[float, float] = (0.0, 0.0),
                 mark_shape_mm: tuple[float, float] | None = None,
                 sheet_size_mm: tuple[float, float] | None = None,
                 mat: str = "auto", end_position: str = "below", end_feed_mm: float = 0.0):
        self.device = device
        self.settings = settings.clamped(device)
        self.origin_mm = origin_mm
        # A standing correction applied to every cut coordinate, for the
        # fixed error between where the sensor says the mark is and where
        # the blade actually comes down.  It is a property of the machine,
        # not of the sheet, so it is measured once and kept.
        self.offset_mm = offset_mm
        self.reg_width_mm = reg_width_mm
        self.reg_length_mm = reg_length_mm
        self.registration = registration
        self.auto_search = auto_search
        self.search_mm = search_mm
        self.mark_shape_mm = mark_shape_mm
        if mat not in MAT_OPTIONS or end_position not in END_POSITIONS:
            raise ValueError("Unknown cutting mat or end position.")
        self.mat = mat
        if not math.isfinite(end_feed_mm) or end_feed_mm < 0:
            raise ValueError("Feed distance must be finite and nonnegative.")
        if end_feed_mm and end_position == "start":
            raise ValueError("Select Feed to add a feed distance.")
        self.end_position = end_position
        self.end_feed_mm = end_feed_mm
        x, y = origin_mm
        self.sheet_size_mm = sheet_size_mm or (reg_width_mm + 2*x, reg_length_mm + 2*y)
        # A four-mark scan only exists on hardware that has it.
        self.quad = quad and device.quad_regmarks
        self.uses_captured_letter_scan = (
            self.registration and self.auto_search and not self.quad and self.search_mm is None
            and self.mat in ("auto", "cameo-12x12")
            and captured_letter_scan(device, reg_length_mm, reg_width_mm, origin_mm, mark_shape_mm))

    # ---- pieces ------------------------------------------------------
    def _setup_commands(self, settings: CutSettings | None = None) -> list[str]:
        s = (settings.clamped(self.device) if settings is not None
             else self.settings)
        dev = self.device
        tool = Tool(s.holder)
        cmds: list[str] = []
        start = round(s.corner_start_mm * 10) if s.sharpen_corners else 0
        end = round(s.corner_end_mm * 10) if s.sharpen_corners else 0
        corners = tool.sharpen_corners(start, end)
        if dev.two_tools:
            cmds.append(tool.select())

        if dev.max_speed > 10:           # the Cameo 4 / 5 dialect
            cmds.append(tool.pressure(s.force))
            cmds.append(acceleration(s.acceleration
                                     if s.acceleration is not None else 0))
            cmds.append(tool.speed(s.speed))
            cmds.append(tool.cutter_offset(0, 0.05))
            cmds.append(tool.lift(s.lift_head or s.sharpen_corners))
            cmds.extend(corners)
            # Upstream sends the pressure twice, with an acceleration change
            # between, and notes it does not know why.  Reproduced.
            cmds.append(tool.pressure(s.force))
            cmds.append(acceleration(s.acceleration
                                     if s.acceleration is not None else 3))
            cmds.append(tool.cutter_offset(s.blade_diameter_mm, 0.05))
        else:
            cmds.append(tool.speed(s.speed) if dev.cameo3_on
                        else f"!{s.speed}")
            # Older firmware takes bare FX; only Cameo 3 uses a holder here.
            # inkscape-silhouette Graphtec.py, setup().
            cmds.append(tool.pressure(s.force) if dev.cameo3_on else f"FX{s.force}")
            if dev.cameo3_on:
                # Cameo 3 setup, upstream Graphtec.py 1240–1262.
                cmds.append(tool.lift(s.lift_head or s.sharpen_corners))
                cmds.extend(corners)
                cmds.append(tool.cutter_offset(0, 0.05))
                cmds.append(tool.cutter_offset(s.blade_diameter_mm, 0.05))
            else:
                cmds.append(blade_diameter(s.blade_diameter_mm))
                cmds.append("FE0,0")
        if dev.autoblade and s.depth is not None and s.holder == 1:
            if dev.product_id == 0x1132:
                # Portrait 2 needs J1 immediately before TF, upstream line 1279.
                cmds.append(tool.select())
            cmds.append(tool.depth(s.depth))
        return cmds

    def _regmark_steps(self) -> list[Step]:
        if not self.registration:
            return []
        ox, oy = self.origin_mm
        steps = [Step("write", frame(registration_preamble(self.device, self.mark_shape_mm)),
                      note="registration preamble")]
        if self.auto_search:
            fn = (auto_quad_regmark_search_mm if self.quad
                  else auto_regmark_search_mm)
            margin = 10.0 if self.search_mm is None else self.search_mm
            cmd = fn(self.reg_length_mm, self.reg_width_mm, max(oy - margin, 0.0),
                     max(ox - margin, 0.0))
            steps.append(Step("write", frame(cmd),
                              note=f"scan for marks: {cmd}"))
        else:
            cmd = manual_regmark_mm(self.reg_length_mm, self.reg_width_mm)
            steps.append(Step("write", frame(cmd),
                              note=f"manual registration: {cmd}"))
        # Upstream allows 40 s; the head traverses the whole sheet looking.
        steps.append(Step("expect", expect=RESP_REGMARK_OK, timeout_ms=40000,
                          note="marks found"))
        return steps

    def _path_commands(self, paths, settings: CutSettings | None = None) -> list[str]:
        """Paths to M/D commands, shifted into mark-relative coordinates.

        Two shifts, in this order: subtract the registration origin, because
        the machine measures from the top-left mark; then add the standing
        calibration offset, because the blade does not come down exactly
        where the sensor says.
        """
        ox, oy = self.origin_mm if self.registration else (0.0, 0.0)
        dx, dy = self.offset_mm
        ox, oy = ox - dx, oy - dy
        cmds: list[str] = []
        for _, points in toolpath.schedule(paths, settings or self.settings, (ox, oy)):
            cmds.append(move_mm(points[0][0] - ox, points[0][1] - oy))
            cmds.extend(draw_mm(x - ox, y - oy) for x, y in points[1:])
        return cmds

    def _trailer_commands(self, paths) -> tuple[str, ...]:
        """Move the head to the chosen position after the cut."""
        if self.end_position == "start":
            return return_to_start_commands(self.device)
        oy = self.origin_mm[1] if self.registration else 0.0
        plot_origin_y = oy - self.offset_mm[1]
        last_y = max((y - plot_origin_y for path in paths for _, y in path.points), default=0.0)
        park_units = su(max(0.0, last_y)) + su(self.end_feed_mm)
        park_y = su_to_mm(park_units)
        if self.end_feed_mm:
            if self.registration:
                limit = self.reg_length_mm
            elif self.mat == "none":
                limit = self.device.length_mm
            else:
                limit = MATS[self.mat][2] if self.mat in MATS else self.sheet_size_mm[1]
            if park_units > su(limit):
                raise ValueError(f"Feed reaches {park_y:.2f} mm. The active feed window ends at {limit:g} mm.")
        # Inkscape Graphtec.py plot(): move below the cut, then set a new origin.
        return (move_mm(0.0, park_y), "SO0")

    # ---- assembly ----------------------------------------------------
    def _initial_steps(self) -> list[Step]:
        steps = [Step("write", CMD_ESC + ESC_EOT, note="initialise the device")]
        if not self.device.cameo3_on and not self.registration:
            # The reference setup selects portrait coordinates on legacy units.
            # This is also our wire orientation for landscape SVGs.
            steps.append(Step("write", frame(("FN0", "TB50,0")), note="set up legacy cutter"))
        commands = cutting_mat_commands(self.device, self.mat, *self.sheet_size_mm, self.registration)
        if commands:
            steps.append(Step("write", frame(commands), note="set up the cutting mat"))
        return steps

    def _registered_setup(self, settings):
        track_enhancing = settings is not None and settings.track_enhancing
        roller_step = Step("write", frame("FY0"), note="track enhancing", wait_ready=True)
        if self.uses_captured_letter_scan:
            # Graphtec.py setup() runs FY0 before registration. Set mat and
            # pressure for the rollers, wait, then use the Studio trace's
            # two scan writes and its registered cut window.
            preamble = registration_preamble(self.device, self.mark_shape_mm)
            scan = auto_regmark_search_mm(self.reg_length_mm, self.reg_width_mm, 5.9, 5.9)
            steps = []
            if track_enhancing:
                tool = Tool(settings.holder)
                steps.extend(self._initial_steps())
                steps.append(Step("write", frame((tool.select(), tool.pressure(settings.force))),
                                  note="set roller pressure"))
                steps.append(roller_step)
            steps.extend((
                Step("write", frame(("TG1", "FN0", *preamble[:2])), note="set up the Cameo cutting mat"),
                Step("write", frame((*preamble[2:], scan, "TB99")), note=f"scan for marks: {scan}"),
                Step("expect", expect=RESP_REGMARK_OK, timeout_ms=40000, note="marks found"),
                Step("write", frame((upper_left_mm(0, 0), lower_right_mm(
                    self.reg_width_mm, self.reg_length_mm))), note="registered cut window"),
            ))
            if settings is not None:
                steps.append(Step("write", frame(self._setup_commands(settings)), note="blade settings"))
            return steps
        steps = self._initial_steps()
        if settings is not None:
            steps.append(Step("write", frame(self._setup_commands(settings)), note="blade settings"))
        if track_enhancing:
            # Graphtec.py setup(): condition the media with the rollers before registration.
            steps.append(roller_step)
        steps.extend(self._regmark_steps())
        return steps

    def _legacy_cut_window(self) -> list[Step]:
        if self.device.cameo3_on:
            return []
        # Graphtec.py plot(): use mark-relative dimensions after a scan,
        # and the full media dimensions for an unregistered cut.
        width, length = ((self.reg_width_mm, self.reg_length_mm)
                         if self.registration else self.sheet_size_mm)
        return [Step("write", frame([upper_left_mm(0, 0),
                                    lower_right_mm(width, length),
                                    "L0", "FE0,0", "FF0,0,0"]), note="cut window")]

    def plan(self, paths) -> list[Step]:
        steps = self._registered_setup(self.settings)
        steps.extend(self._legacy_cut_window())
        steps.append(Step("write", frame(self._path_commands(paths)),
                          note="cut paths", pace=True))
        steps.append(Step("write", frame(self._trailer_commands(paths)),
                          note="park the head"))
        return steps

    def plan_matrix(self, groups, *, wait_between: bool = False) -> list[Step]:
        """One job that cuts each group of paths with its own settings.

        `groups` is a sequence of `(CutSettings, paths)`. The machine is set
        up once, registered once if asked, and then the force/speed/depth
        block is re-sent before each group - which is what makes a
        settings-finding test sheet possible in a single pass of the mat
        rather than one job per combination.

        Everything else is identical to `plan`, deliberately: a test sheet
        that reached the blade by a different route would not be testing the
        thing you are about to cut with.
        """
        steps = self._registered_setup(groups[0][0] if groups else None)
        steps.extend(self._legacy_cut_window())
        all_paths = []
        for settings, paths in groups:
            s = settings.clamped(self.device)
            steps.append(Step("write", frame(self._setup_commands(s)),
                              note=(f"force {s.force}, speed {s.speed}, "
                                    f"{s.passes} pass(es)"
                                    + (f", depth {s.depth}"
                                       if s.depth is not None else ""))))
            steps.append(Step("write",
                              frame(self._path_commands(paths, s)),
                              note=f"{len(paths)} path(s)", pace=True, wait_ready=wait_between))
            all_paths.extend(paths)
        steps.append(Step("write", frame(self._trailer_commands(all_paths)),
                          note="park the head"))
        return steps

    def stream(self, paths) -> bytes:
        """Just the bytes, in order, for dumping and for verification."""
        return b"".join(s.data for s in self.plan(paths)
                        if s.kind == "write")

    def stream_matrix(self, groups) -> bytes:
        return b"".join(s.data for s in self.plan_matrix(groups)
                        if s.kind == "write")
