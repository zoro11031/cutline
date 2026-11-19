"""What the cutter reports is in each tool holder, and what to do about it.

The query itself lives in `transport.Transport.tool_codes` and is the
ESC NAK reply recovered by inkscape-silhouette. This module is the part
both frontends share: how to describe a reply, and the one settings
correction a reply justifies. It is pure so it can be tested offline.

Hardware status: a Cameo 4 on firmware 1.20 answered the query with
` 2, 0` on 2026-11-18 (docs/FINDINGS.md). Reading the reply is verified;
nothing here claims what the machine does with a tool it reports.
"""

from . import graphtec

AUTOBLADE = 2
EMPTY = 0
DETECTION_ERROR = 255

#: What the depth field means when the operator sets the blade by hand.
MANUAL_DEPTH = -1


SECOND_HOLDER_BLADES = {"rotary": "Rotary blade", "kraft-3mm": "Kraft blade (3 mm)"}


def tool_choices(device):
    """Blade names shown in the picker for the selected cutter."""
    options = {"": "Detect automatically"}
    if device.autoblade:
        options["autoblade"] = "AutoBlade"
    options.update({
        "ratchet": "Ratchet blade",
        "manual-1mm": "Manual blade (1 mm)",
        "manual-2mm": "Manual blade (2 mm)",
        "deep-cut": "Deep-cut blade",
        "fabric": "Fabric blade",
        "premium": "Premium blade",
        "kraft-2mm": "Kraft blade (2 mm)",
    })
    if device.two_tools and device.detects_tools:
        # Silhouette Cameo 4 User Manual, p. 24: Rotary and Kraft blades use Tool 2.
        options.update(SECOND_HOLDER_BLADES)
    options["manual"] = "Other manual blade"
    return options


def tool_holder(choice, current):
    if choice in ("", "manual"):
        return current
    return 2 if choice in SECOND_HOLDER_BLADES else 1


class ToolSelection:
    """Manual choices follow the selected cutter and are cleared on disconnect."""

    def __init__(self):
        self.binding = None
        self.overrides = {}

    def bind(self, connection, device):
        binding = connection, device.name
        if binding == self.binding:
            return False
        self.binding = binding
        had_choices = bool(self.overrides)
        self.overrides.clear()
        return had_choices

    def selected(self, holder):
        return self.overrides.get(holder, "")

    def select(self, holder, choice):
        if choice:
            self.overrides[holder] = choice
        else:
            self.overrides.pop(holder, None)

    @property
    def manual_depth(self):
        return self.selected(1) not in ("", "autoblade")


def holder_labels(codes, device=None, selection=None):
    """One label per holder the device has, for the tool holder tabs.

    An empty reply means the model cannot report its tools, or that the
    query has not run yet; both read as "not known", never as "empty".
    """
    holders = (1, 2) if device is not None and device.two_tools else (1,)
    labels = []
    for slot in holders:
        choice = selection.selected(slot) if selection else ""
        code = installed(codes, slot)
        name = tool_choices(device).get(choice) if choice else (
            graphtec.tool_name(code) if code is not None else None)
        labels.append(f"Tool Holder {slot} · {name}" if name else f"Tool Holder {slot}")
    return tuple(labels)


def installed(codes, holder):
    """The code in `holder`, or None when the reply does not cover it."""
    if not codes or holder < 1 or holder > len(codes):
        return None
    return codes[holder - 1]


def is_autoblade(codes, holder=1):
    return installed(codes, holder) == AUTOBLADE


def depth_correction(codes, depth, holder=1):
    """The depth to use instead, and why, when holder 1 holds no AutoBlade.

    Automatic depth is an AutoBlade command: the cutter refuses it with
    anything else in the holder. When the reply names another tool, the
    depth goes back to manual so the job is sendable. Returns
    `(None, "")` when the current depth is already fine, when the reply
    says nothing useful, or when the tool is an AutoBlade.
    """
    code = installed(codes, holder)
    if code is None or code in (AUTOBLADE, DETECTION_ERROR):
        return None, ""
    if depth is None or depth < 0:
        return None, ""
    return MANUAL_DEPTH, (
        f"Holder {holder} reports {graphtec.tool_name(code)}, which has no automatic "
        f"depth. Blade Depth is set to manual; set the blade's depth by hand.")
