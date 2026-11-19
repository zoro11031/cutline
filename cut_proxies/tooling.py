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


def holder_labels(codes, device=None):
    """One label per holder the device has, for the tool holder tabs.

    An empty reply means the model cannot report its tools, or that the
    query has not run yet; both read as "not known", never as "empty".
    """
    holders = (1, 2) if device is not None and device.two_tools else (1,)
    if not codes:
        return tuple(f"Tool Holder {slot}" for slot in holders)
    labels = []
    for slot in holders:
        code = codes[slot - 1] if slot <= len(codes) else None
        labels.append(f"Tool Holder {slot}" if code is None
                      else f"Tool Holder {slot} · {graphtec.tool_name(code)}")
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
