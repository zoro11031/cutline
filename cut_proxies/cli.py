"""silhouette-control-center on the command line.

Subcommands, in the order a sheet goes through them:

  template   lay out a sheet and write the SVG / DXF / proof
  layouts    what fits on what, including the ones that do not fit
  presets    the blade presets, and where each number came from
  status     the cutter, the transports, and the WinBoat guest
  cut        register and cut (or --dry-run to build and check only)
  studio     hand a template to Silhouette Studio in the WinBoat guest
  gui        the window

`cut` refuses rather than guesses.  Anything in `Job.blocked` stops it.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from . import (config, geometry, graphtec, job as job_mod, layout_presets, presets, regmarks,
               template, transport, verify, winboat)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    _console = Console(stderr=True, soft_wrap=True)
except ImportError:                   # rich is optional
    _console = None
    Table = Panel = None


def say(msg="", style=None):
    if _console:
        _console.print(msg, style=style, highlight=False, markup=False)
    else:
        print(msg, file=sys.stderr)


def warn(msg):
    say(f"! {msg}", style="bold yellow")


def fail(msg):
    say(f"x {msg}", style="bold red")
    raise SystemExit(1)


def ask(prompt, default=""):
    try:
        answer = input(f"{prompt} ").strip()
    except EOFError:
        return default
    return answer or default


def ask_yes(prompt, default="n"):
    return ask(f"{prompt} [{default}]", default).lower().startswith("y")


# --------------------------------------------------------------------------
# shared output
# --------------------------------------------------------------------------

def show_summary(j):
    rows = j.summary()
    if _console:
        table = Table(show_header=False, box=None, pad_edge=False)
        for label, value in rows:
            table.add_row(label, value)
        _console.print(Panel(table, title="Job", expand=False))
    else:
        for label, value in rows:
            say(f"{label:16} {value}")


def show_checks(j):
    say()
    for r in j.rows:
        mark = "ok  " if r.ok else "FAIL"
        style = None if r.ok else "bold red"
        say(f"  [{mark}] {r.label:26} {r.got}", style=style)
    live = not j.liveness_failures
    say(f"  [{'ok  ' if live else 'FAIL'}] {'checks proven live':26} "
        f"{len(verify.PROBES) - len(j.liveness_failures)}/{len(verify.PROBES)}"
        f" probes caught",
        style=None if live else "bold red")
    for m in j.liveness_failures:
        say(f"  [FAIL] {'liveness':26} {m}", style="bold red")
    for c in j.collisions:
        say(f"  [FAIL] collision                 {c}", style="bold red")
    for o in j.off_sheet:
        say(f"  [FAIL] off sheet                 {o}", style="bold red")
    for w in j.warnings:
        warn(w)
    if j.mask_hints:
        say()
        say("Near registration marks (conservative proximity check):", style="bold yellow")
        for h in j.mask_hints:
            say(f"  - {h['text']}")
        say(regmarks.MASK_GUIDANCE, style="dim")
        say("Proximity alone does not mean every nearby card needs covering.", style="dim")


def common_layout_args(p):
    p.add_argument('--layout-preset', choices=tuple(layout_presets.BUILTIN),
                   help='apply a complete printed-sheet layout; overrides layout '
                        'options and leaves blade settings unchanged')
    p.add_argument("--sheet", default=config.DEFAULT_SHEET,
                   choices=sorted(geometry.SHEETS))
    p.add_argument("--card", default=config.DEFAULT_CARD,
                   help="card size name (default: standard = 63x88 mm)")
    orient = p.add_mutually_exclusive_group()
    orient.add_argument("--portrait", dest="portrait", action="store_true",
                        default=config.DEFAULT_PORTRAIT)
    orient.add_argument("--landscape", dest="portrait", action="store_false",
                        default=config.DEFAULT_PORTRAIT)
    p.add_argument("--engine", default="scm", choices=("scm", "proxypress"),
                   help="which layout to match: Silhouette Card Maker's, or "
                        "the grid Proxy Press draws")
    p.add_argument("--mode", default=template.ROUNDED, choices=template.MODES,
                   help="rounded corners, square corners, or straight "
                        "cross-cuts for a corner punch")
    p.add_argument("--radius", type=float, default=None,
                   metavar="MM", help="corner radius (default: per card size)")
    p.add_argument("--pattern", default=regmarks.THREE,
                   choices=regmarks.PATTERNS,
                   help="three = every Cameo; four = Cameo 5 Alpha")
    p.add_argument("--mark-inset", type=float, default=None, metavar="MM",
                   help="distance from the paper edge to the marks "
                        "(10 = standard, 3.5 = borderless)")
    p.add_argument("--mark-length", type=float, default=None, metavar="MM")
    p.add_argument("--mark-thickness", type=float, default=None, metavar="MM")
    p.add_argument("--bleed", type=float, default=0.0, metavar="MM",
                   help="bleed around each card, for the collision check")
    p.add_argument("--from", dest="source", metavar="FILE",
                   help="cut the paths in this SVG or DXF instead of a "
                        "generated grid")
    p.add_argument("--color", "--colour", dest="colour", metavar="COLOUR",
                   help="cut only the paths with this stroke colour, the "
                        "way Studio selects by line colour (e.g. red, "
                        "#ff0000)")
    p.add_argument("--skip", type=int, action="append", default=[],
                   metavar="N",
                   help="leave card N uncut (0-based). Use it for the slot "
                        "left empty so a card stops crowding a mark; repeat "
                        "for more than one")


def common_cut_args(p):
    p.add_argument("--cutter", default="Silhouette Cameo 4",
                   help="cutter model (default: Silhouette Cameo 4)")
    p.add_argument("--preset", default=config.DEFAULT_PRESET)
    p.add_argument("--force", type=int, default=None)
    p.add_argument("--speed", type=int, default=None)
    p.add_argument("--passes", type=int, default=None)


def build_from_args(args) -> job_mod.Job:
    imported = None
    source = getattr(args, "source", None)
    if source:
        from . import importer
        try:
            imported = importer.load(source)
        except (importer.ImportError_, OSError) as exc:
            fail(str(exc))
        colour = getattr(args, "colour", None)
        if colour:
            try:
                imported = imported.only(colour)
            except importer.ImportError_ as exc:
                fail(str(exc))
        say(f"Imported {pathlib.Path(source).name}: {imported.describe()}",
            style="bold")
        if colour:
            say(f"  only stroke {colour}", style="dim")
        for w in imported.warnings:
            warn(w)
    options = dict(
        imported=imported,
        sheet=args.sheet, card=args.card, portrait=args.portrait,
        engine=args.engine,
        preset_name=getattr(args, "preset", config.DEFAULT_PRESET),
        cutter=getattr(args, "cutter", "Silhouette Cameo 4"),
        mode=args.mode, radius_mm=args.radius,
        mark_inset_mm=args.mark_inset, mark_length_mm=args.mark_length,
        mark_thickness_mm=args.mark_thickness, pattern=args.pattern,
        passes=getattr(args, "passes", None),
        force=getattr(args, "force", None),
        speed=getattr(args, "speed", None),
        bleed_mm=args.bleed,
        registration=not getattr(args, "no_registration", False),
        auto_search=not getattr(args, "manual_registration", False),
        skip=tuple(getattr(args, "skip", []) or ()),
    )
    selected = getattr(args, 'layout_preset', None)
    if selected:
        options.update(layout_presets.BUILTIN[selected].options())
    return job_mod.build(**options)


# --------------------------------------------------------------------------
# template
# --------------------------------------------------------------------------

def cmd_template(args):
    try:
        j = build_from_args(args)
    except geometry.LayoutImpossible as exc:
        fail(str(exc))
    show_summary(j)
    show_checks(j)

    stem = args.name or (
        f"{j.grid.sheet_name.lower()}-{j.grid.card.name}-"
        f"{j.grid.cols}x{j.grid.rows}-"
        f"{'portrait' if j.grid.portrait else 'landscape'}-{j.mode}")
    written = job_mod.write_templates(j, stem, args.out, proof=not args.no_proof)
    say()
    for kind, path in written.items():
        say(f"{kind:6} {path}", style="bold green")
    if "proof" in written:
        say("\nThe proof carries the registration marks for checking against "
            "the printed sheet by eye. Do not send it to the cutter.",
            style="dim")
    if j.blocked:
        say("\nThe template was written, but this job would be refused:",
            style="bold yellow")
        for reason in j.blocked:
            say(f"  - {reason}")
        return 1
    return 0


# --------------------------------------------------------------------------
# layouts
# --------------------------------------------------------------------------

def cmd_layouts(args):
    card_name = args.card
    say(f"\nWhat fits, for {card_name} cards:", style="bold")
    header = f"  {'sheet':9} {'inset':>6} {'orient':10} {'grid':>7} " \
             f"{'cards':>5} {'max arm':>8}  {'near':>5}"
    say(header)
    say("  " + "-" * (len(header) - 2))
    clean = []
    sheets = ([n for n, v in sorted(geometry.SHEETS.items()) if v.stocked]
              if not args.all_sheets else sorted(geometry.SHEETS))
    for sheet_name in sheets:
        for inset, label in ((regmarks.DEFAULT_INSET_MM, "10.0"),
                             (regmarks.BORDERLESS_INSET_MM, "3.5")):
            for portrait in (False, True):
                try:
                    g = geometry.scm_layout(sheet_name, card_name, portrait,
                                            inset)
                except geometry.LayoutImpossible:
                    say(f"  {sheet_name:9} {label:>6} "
                        f"{'portrait' if portrait else 'landscape':10} "
                        f"{'-':>7} {'none':>5} {'-':>8}  {'-':>5}", style="dim")
                    continue
                mk = regmarks.for_grid(g, inset_mm=inset)
                hints = regmarks.mask_hints(g, mk)
                say(f"  {sheet_name:9} {label:>6} "
                    f"{'portrait' if portrait else 'landscape':10} "
                    f"{g.cols}x{g.rows:<5} {g.count:>5} "
                    f"{g.max_mark_length_mm:>7.2f}  "
                    f"{(str(len(hints)) if hints else 'none'):>5}")
                if not hints and geometry.sheet(sheet_name).stocked:
                    # Sort key prefers more cards, then the LARGER inset: at
                    # equal card counts the marks further from the paper edge
                    # tolerate more printer feed drift.
                    clean.append((g.count, inset, sheet_name, label, portrait))
    say("\n'max arm' is the longest registration-mark arm that still clears "
        "the cards.", style="dim")
    say("'near' counts cards within a conservative 5 mm of a mark zone; "
        "it is not a required masking count.", style="dim")
    say(regmarks.MASK_GUIDANCE, style="dim")
    say("\nA 3x3 nine-card sheet on Letter or A4 needs the 3.5 mm borderless "
        "inset; at the\nstandard 10 mm inset the marks take the room and you "
        "get eight.", style="dim")
    if not args.all_sheets:
        others = [n for n, v in sorted(geometry.SHEETS.items())
                  if not v.stocked]
        say(f"\nShowing only the sizes the stock comes in. "
            f"{', '.join(others)} would also cut,\nbut ProxyJak is sold in "
            f"Letter and A4, so a layout on anything else is advice\nabout "
            f"paper you cannot buy. --all-sheets shows them anyway.",
            style="dim")

    say(f"\nThe sheet this workflow uses: "
        f"{config.CARDS_PER_SHEET} cards, four across and two down, on "
        f"Letter or A4\nturned landscape, marks at the standard 10 mm "
        f"inset.", style="bold green")
    say("Nine-up exists but needs a 3.5 mm inset and a portrait sheet, "
        "which puts the\nmarks 3.5 mm from the paper edge with no room "
        "for feed drift. Eight is not a\ncompromise here - it is the "
        "layout.", style="dim")
    say("\nProxy Press settings for it:  silhouette-control-center proxypress", style="dim")
    return 0


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------

def cmd_presets(args):
    all_p = presets.all_presets()
    if args.name:
        p = presets.get(args.name)
        say(f"\n{p.name}", style="bold")
        say(f"  {p.description}")
        say(f"  origin: {p.origin}")
        for k, v in sorted(p.settings.items()):
            say(f"  {k:20} {v}")
        if p.note:
            say(f"\n  {p.note}")
        return 0
    say("\nBlade presets:", style="bold")
    for name in sorted(all_p):
        p = all_p[name]
        s = p.settings
        flag = {"measured": "measured", "silhouette": "published",
                "recovered": "recovered", "guide": "guide"}.get(p.origin, "NOT CUT")
        say(f"  {name:42} force {s.get('force', '?'):>3}  "
            f"speed {s.get('speed', '?'):>2}  "
            f"{s.get('passes', 1)} pass(es)   [{flag}]")
    say("\n[NOT CUT] means the numbers are extrapolated; [guide] means from "
        "the Card Maker guide.\nRun 'cut --dry-run' first, then the plain "
        "paper test preset, then card.", style="dim")
    return 0


# --------------------------------------------------------------------------
# proxypress
# --------------------------------------------------------------------------

def cmd_proxypress(args):
    """The settings to type into Proxy Press so its sheet is the one we cut."""
    from . import importer  # noqa: F401  (keeps import cost off other paths)
    sheet_name = args.sheet
    layout_slug = 'mtg-cameo-' + sheet_name.lower()
    matched = layout_presets.BUILTIN[layout_slug].options()
    s = config.PROXYPRESS_8UP
    pp = geometry.proxypress_layout(sheet_name, portrait=False, settings=s)
    scm = geometry.scm_layout(sheet_name, "standard", False,
                              config.DEFAULT_MARK_INSET_MM)
    worst = max(max(abs(a.x - b.x), abs(a.y - b.y))
                for a, b in zip(scm.cards, pp.cards))

    say(f"\nProxy Press settings for a {sheet_name} sheet this can cut",
        style="bold")
    say(f"  {pp.count} cards, {pp.cols} across and {pp.rows} down, "
        f"landscape\n")
    say("  Layout")
    say(f"    Page size            {sheet_name}")
    say(f"    Orientation          Landscape")
    say(f"    Card size            MTG (63 x 88 mm)")
    say(f"    Card rotation        Upright")
    say(f"    Bleed                {s.bleed_mm:g} mm")
    say(f"    Gap                  {s.gap_mm:g} mm")
    say(f"    Margin               {s.margin_mm:g} mm")
    say(f"    Columns              {s.cols}")
    say(f"    Rows                 {s.rows}")
    say(f"    Corner radius        {s.corner_radius_mm:g} mm")
    say("\n  Cutting -> Silhouette Cameo")
    say("    One-click setup: Click 'Set up for Silhouette Cameo' on the Cutting tab")
    say("    Or set manually under Layout and Cutting:")
    m = config.PROXYPRESS_8UP_MARKS
    say(f"      Registration marks   on")
    say(f"      Mark pattern         {m['regPattern']} marks")
    say(f"      Mark inset           {m['regInset']} mm")
    say(f"      Mark arm length      {m['regLen']} "
        f"(0 = auto: longest that clears the cards)")
    say(f"      Mark line thickness  {m['regThickness']} mm")
    say("\n  Export and print at 100%. PDF checks measure digital geometry; "
        "measure a printed ruler and test alignment with a pen to check "
        "physical output.", style="dim")

    say(f"\nThen cut with:", style="bold")
    say(f"  silhouette-control-center template --layout-preset {layout_slug}")
    say(f"  silhouette-control-center check-sheet yoursheet.pdf --layout-preset {layout_slug}")
    say(f"  silhouette-control-center cut --layout-preset {layout_slug}")
    say(f"  Proxy Press auto arm on {sheet_name}: {matched['mark_length_mm']:g} mm")

    say(f"\nThese place every card within {worst:.3f} mm of where "
        f"Silhouette Card Maker\nwould put it, which is the rounding "
        f"difference between its integer-pixel\nlayout and Proxy Press's "
        f"floats.", style="dim")

    mk = regmarks.for_grid(pp, inset_mm=m["regInset"],
                           length_mm=matched['mark_length_mm'],
                           thickness_mm=m["regThickness"],
                           pattern=regmarks.THREE)
    hints = regmarks.mask_hints(pp, mk)
    if hints:
        say(f"\n{len(hints)} card(s) sit near a mark (proximity advisory):", style="bold yellow")
        for h in hints:
            say(f"  - card {h['index'] + 1} "
                f"(row {h['row']}, column {h['col']}), "
                f"{h['gap_mm']:.2f} mm from the {h['mark']} mark")
        say(regmarks.MASK_GUIDANCE, style="dim")
    return 0


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

def cmd_import(args):
    from . import importer
    try:
        im = importer.load(args.file)
    except (importer.ImportError_, OSError) as exc:
        fail(str(exc))
    say(f"\n{pathlib.Path(args.file).name}", style="bold")
    say(f"  {im.describe()}")
    say(f"  declared size: {im.width_mm:.1f} x {im.height_mm:.1f} mm")
    x0, y0, x1, y1 = im.bounds
    say(f"  geometry:      {x1 - x0:.2f} x {y1 - y0:.2f} mm "
        f"at ({x0:.2f}, {y0:.2f})")
    cols = im.colours()
    if cols:
        say("  colours:       "
            + ", ".join(f"{c} ({n})" for c, n in cols.items()))
        if len(cols) > 1:
            say("                 cut one with --color "
                f"{next(iter(cols))}", style="dim")
    closed = sum(1 for p in im.paths if p.closed)
    say(f"  paths:         {len(im.paths)} ({closed} closed, "
        f"{len(im.paths) - closed} open)")
    total = sum(p.length_mm() for p in im.paths)
    say(f"  blade travel:  {total / 1000:.2f} m")
    for w in im.warnings:
        warn(w)
    if args.verbose:
        say()
        for i, p in enumerate(im.paths):
            xs = [x for x, _ in p.points]
            ys = [y for _, y in p.points]
            say(f"  {i:3}  {p.label:11} {len(p.points):4} pts  "
                f"{max(xs) - min(xs):7.2f} x {max(ys) - min(ys):7.2f} mm  "
                f"at ({min(xs):7.2f}, {min(ys):7.2f})"
                f"{'  closed' if p.closed else ''}")
    say("\nCut it with:  silhouette-control-center cut --from "
        f"{pathlib.Path(args.file).name} --dry-run", style="dim")
    return 0


# --------------------------------------------------------------------------
# check-sheet
# --------------------------------------------------------------------------

def cmd_check_sheet(args):
    from . import sheet as sheet_mod
    path = pathlib.Path(args.file).expanduser()
    if not path.is_file():
        fail(f"No such file: {path}")
    try:
        j = build_from_args(args)
    except geometry.LayoutImpossible as exc:
        fail(str(exc))
    try:
        report = sheet_mod.measure(path, j.marks, page=args.page,
                                   dpi=args.dpi)
    except sheet_mod.SheetError as exc:
        fail(str(exc))

    say("PDF geometry only; measure the physical print separately.", style="dim")
    say(f"\nMeasured {path.name}, page {report.page}, at {report.dpi} dpi",
        style="bold")
    say(f"  page {report.page_w_mm:.1f} x {report.page_h_mm:.1f} mm "
        f"(template expects {j.grid.sheet_w_mm:.1f} x "
        f"{j.grid.sheet_h_mm:.1f})")
    say()
    for m in report.marks:
        ok = m.is_credible and (m.error_mm or 0) <= args.tolerance
        say(f"  [{'ok  ' if ok else 'FAIL'}] {m.describe()}",
            style=None if ok else "bold red")

    problems = report.problems(j.grid.sheet_w_mm, j.grid.sheet_h_mm,
                               args.tolerance)
    say(f"\n  {report.diagnosis()}", style="bold")
    if not problems:
        say("\nThis sheet matches the template. The cutter should find its "
            "marks.", style="bold green")
        return 0
    say()
    for pr in problems:
        say(f"  {pr}", style="bold red")
    say("\nThis measures where the ink is, not whether the sensor can read "
        "it. A mark\nthat is grey, or printed over dark art, can pass here "
        "and still be missed.", style="dim")
    return 1


# --------------------------------------------------------------------------
# testcut
# --------------------------------------------------------------------------

def cmd_testcut(args):
    from . import testcut
    device = graphtec.device_by_name(args.cutter)
    base = presets.get(args.preset).cut_settings()
    sheet = geometry.sheet(args.sheet)
    w, h = sheet.size(portrait=not args.landscape)
    try:
        ts = testcut.build(
            device, base, forces=args.force, speeds=args.speed,
            passes=args.passes, depths=args.depth, size_mm=args.size,
            gap_mm=args.gap, margin_mm=args.margin, cols=args.cols,
            sheet_w_mm=w, sheet_h_mm=h, registration=args.registration,
            offset_mm=config.cut_offset())
    except ValueError as exc:
        fail(str(exc))

    say(f"\nTest sheet: {len(ts.cells)} squares of {args.size:g} mm on "
        f"{args.sheet}", style="bold")
    e = ts.extent()
    say(f"  occupies {e.w:.0f} x {e.h:.0f} mm from "
        f"({e.x:.0f}, {e.y:.0f}); {len(ts.stream):,} bytes")
    reg = ("on" if ts.registration else
           "off - put a blank offcut of the real stock on the mat")
    say(f"  registration: {reg}")
    say()
    for label, desc in ts.map_rows():
        say(f"  {label:7} {desc}")
    say("\nCut it, and WITHOUT ejecting the mat lift a corner of each square "
        "and\nsee which separated cleanly. Tune passes first, then force, "
        "then speed,\nthen depth.", style="dim")

    if args.out:
        folder = pathlib.Path(args.out)
        folder.mkdir(parents=True, exist_ok=True)
        proof = folder / "testcut-map.svg"
        testcut.write_proof(proof, ts, w, h)
        say(f"\nmap    {proof}", style="bold green")
        say("The map is labelled, so it is not a cut file.", style="dim")

    if args.dump:
        dump = pathlib.Path(args.dump)
        dump.write_bytes(ts.stream)
        say(f"stream {dump} ({len(ts.stream):,} bytes)", style="bold green")

    if args.dry_run:
        say("\nDry run: nothing sent.", style="bold")
        return 0

    try:
        tr = transport.open_transport(args.transport)
    except transport.TransportError as exc:
        say(f"\n{exc}", style="bold red")
        fail("No way to reach the cutter. Use --dry-run.")
    with tr:
        if tr.can_read:
            state = tr.status()
            say(f"\nCutter reports: {state}")
            if state == "unloaded":
                fail("The cutter says no media is loaded.")
        if not args.yes and not ask_yes("\nCut the test sheet now?", "n"):
            say("Cancelled.")
            return 0
        try:
            sent = tr.run(ts.steps, log=lambda m: say("  " + m))
        except transport.TransportError as exc:
            fail(str(exc))
        say(f"Sent {sent:,} bytes.", style="bold green")
    return 0


# --------------------------------------------------------------------------
# calibrate
# --------------------------------------------------------------------------

def cmd_calibrate(args):
    dx, dy = config.cut_offset()
    if args.clear:
        config.save_cut_offset(0.0, 0.0)
        say("Calibration cleared.", style="bold green")
        return 0
    if args.x is None and args.y is None:
        say(f"\nCurrent calibration: {dx:+g}, {dy:+g} mm", style="bold")
        say("""
How to measure it, once per machine:

  1. silhouette-control-center template --preset "Pen, no blade" ... and print the sheet.
  2. Put a pen in the holder and cut it. The pen draws where the blade
     would have come down.
  3. Measure how far the drawn outline sits from the printed card edge.
     Right and down are positive.
  4. silhouette-control-center calibrate --x <dx> --y <dy>

This corrects the fixed error between where the sensor says the marks are
and where the tool actually lands. It is a property of the machine, so it is
measured once and applied to every job afterwards. It is NOT a fix for a
sheet that printed at the wrong scale - check that first, because no offset
can correct it.""", style="dim")
        return 0
    nx = dx if args.x is None else args.x
    ny = dy if args.y is None else args.y
    if max(abs(nx), abs(ny)) > 5.0:
        warn(f"{nx:+g}, {ny:+g} mm is a very large correction. That is "
             f"usually a sheet printed at the wrong scale, or marks that do "
             f"not match the template, rather than a machine offset.")
    path = config.save_cut_offset(nx, ny)
    say(f"Calibration set to {nx:+g}, {ny:+g} mm and saved to {path}",
        style="bold green")
    return 0


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------

def cmd_materials(args):
    from . import materials
    try:
        table = materials.by_name(args.file, args.cutter_class)
    except (FileNotFoundError, ValueError) as exc:
        fail(str(exc))
    say("\nSilhouette's own material settings", style="bold")
    say("from Studio's condition.csv. Force and speed are machine indices; "
        "depth is\nmillimetres of blade extension, which is the AutoBlade "
        "index divided by ten.", style="dim")
    say()
    say(f"  {'material':34} {'force':>5} {'speed':>5} {'pass':>4} "
        f"{'depth':>6} {'blade':>6}")
    say("  " + "-" * 64)
    for name in sorted(table):
        m = table[name]
        depth = f"{m.depth_mm:g}" if m.depth_mm is not None else "-"
        say(f"  {name[:34]:34} {m.force:>5} {m.speed:>5} {m.passes:>4} "
            f"{depth:>6} {m.blade_diameter_mm:>5.1f}")
    varies = materials.varies_by_cutter(args.file)
    say(f"\nIdentical across every cutter class except: "
        f"{', '.join(varies) or 'none'}.", style="dim")
    say("Nothing here is for 330 gsm black-core stock; Coverstock is the "
        "heaviest paper\nSilhouette publishes a setting for, and this suite "
        "prints on something heavier.", style="dim")

    if args.save:
        try:
            m = table[args.save]
        except KeyError:
            fail(f"no material named {args.save!r}")
        from . import presets
        path = presets.save(presets.Preset(**materials.as_preset(m)))
        say(f"\nWrote {path}", style="bold green")
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(args):
    say("\nCutter", style="bold")
    found = transport.find_devices()
    if found:
        for dev, how in found:
            say(f"  {dev.name} ({dev.usb_id}) - {how}", style="bold green")
    else:
        say("  no Silhouette cutter on the USB bus", style="yellow")
        say(f"  looked for: "
            f"{', '.join(sorted({d.usb_id for d in graphtec.DEVICES}))}",
            style="dim")

    say("\nTransports", style="bold")
    try:
        import usb.core  # noqa: F401
        say("  pyusb      available")
    except ImportError:
        say("  pyusb      NOT installed - needed for a registration scan, "
            "which has to read the cutter's reply", style="yellow")
    lp = [p for p in (f"/dev/usb/lp{i}" for i in range(4))
          if pathlib.Path(p).exists()]
    say(f"  usblp      {', '.join(lp) if lp else 'no /dev/usb/lp* nodes'}")
    say("  file       always available (--dry-run)")

    if found:
        say("\nLive status", style="bold")
        try:
            with transport.open_transport("auto") as t:
                say(f"  {t.name}: {t.status()}")
        except transport.TransportError as exc:
            say(f"  could not open the cutter: {exc}", style="yellow")

    say("\nWinBoat guest", style="bold")
    g = winboat.status()
    say(f"  container     {'running' if g.container_running else 'not running'}")
    if g.agent_port:
        say(f"  guest agent   port {g.agent_port}"
            + (f", version {g.agent_version}" if g.agent_version else ""))
    if g.rdp_port:
        say(f"  rdp           port {g.rdp_port}"
            + ("" if g.rdp_reachable else "  (nothing listening)"),
            style=None if g.rdp_reachable else "yellow")
    if g.share_host_path:
        say(f"  share         {g.share_host_path} -> {winboat.GUEST_SHARE}")
    say(f"  usb passthru  {'yes' if g.usb_passthrough else 'no'}")
    say(f"  studio        {g.studio_state}")
    say(f"  rdp session   {g.rdp_in_use or 'free'}",
        style="yellow" if g.rdp_in_use else None)
    if not g.ready_for_studio:
        say(f"  not ready     {g.why_not_ready()}", style="yellow")
    for d in g.detail:
        say(f"  - {d}", style="dim")
    return 0


# --------------------------------------------------------------------------
# cut
# --------------------------------------------------------------------------

def cmd_cut(args):
    try:
        j = build_from_args(args)
    except geometry.LayoutImpossible as exc:
        fail(str(exc))
    show_summary(j)
    show_checks(j)

    if j.blocked:
        say()
        for reason in j.blocked:
            say(f"  {reason}", style="bold red")
        fail("The job does not check out. Nothing was sent.")

    if args.dump:
        path = pathlib.Path(args.dump)
        path.write_bytes(j.stream)
        say(f"\nStream written to {path} ({len(j.stream):,} bytes).",
            style="bold green")

    if getattr(args, "via", "cutter") == "studio":
        from . import job as job_mod
        stem = (f"{j.grid.sheet_name.lower()}-{j.grid.card.name}-"
                f"{j.grid.cols}x{j.grid.rows}-"
                f"{'portrait' if j.grid.portrait else 'landscape'}-{j.mode}")
        stage_dir = winboat.stage_dir()
        written = job_mod.write_templates(j, stem, folder=stage_dir, proof=False)
        dxf_path = written["dxf"]
        say(f"\nDXF template generated and staged: {dxf_path.name}", style="bold green")
        try:
            cmd = winboat.open_in_studio(dxf_path, dry_run=args.dry_run)
        except RuntimeError as exc:
            fail(str(exc))
        say("\n" + (" ".join(cmd)), style="dim")
        say("\nSilhouette Studio is opening in the guest with that template."
            if not args.dry_run else "\nDry run: not launched.", style="bold")
        _show_studio_operator_guide(j)
        return 0

    if args.dry_run:
        say("\nDry run: everything checks out, nothing sent.", style="bold")
        return 0

    if j.preset.origin not in ("measured",):
        warn(f"The {j.preset.name} preset has never been cut "
             f"({j.preset.origin}). Cut the plain-paper test first.")

    try:
        t = transport.open_transport(args.transport, device=j.device)
    except transport.TransportError as exc:
        say(f"\n{exc}", style="bold red")
        fail("No way to reach the cutter. Use --dry-run, or go through "
             "Silhouette Studio with 'silhouette-control-center studio'.")

    with t:
        if t.can_read:
            state = t.status()
            say(f"\nCutter reports: {state}")
            if state == "unloaded":
                fail("The cutter says no media is loaded. Load the mat and "
                     "the sheet first.")
            elif state == "moving":
                fail("The cutter is busy.")
        if not args.yes and not ask_yes(
                "\nEverything checks out. Register and cut now?", "n"):
            say("Cancelled. Nothing cut.")
            return 0
        say("\nRunning...", style="bold")
        try:
            sent = t.run(
                _plan(j),
                log=lambda m: say("  " + m))
        except transport.TransportError as exc:
            fail(f"{exc}\n\nIf the marks were not found: check that the "
                 f"printed marks match --mark-inset and --mark-length, that "
                 f"the sheet is square on the mat, and that the mat is "
                 f"loaded to the guide line.")
        say(f"Sent {sent:,} bytes.", style="bold green")
    return 0


def _plan(j):
    """The exact write/read plan built and verified for this job."""
    return j.steps


def _show_studio_operator_guide(j):
    w_mm, h_mm = j.grid.sheet_w_mm, j.grid.sheet_h_mm
    w_in, h_in = w_mm / 25.4, h_mm / 25.4
    bounds = j.grid.bounds
    bw_mm, bh_mm = bounds.w, bounds.h
    bw_in, bh_in = bw_mm / 25.4, bh_mm / 25.4
    cw_mm, ch_mm = j.grid.card.width_mm, j.grid.card.height_mm
    cw_in, ch_in = cw_mm / 25.4, ch_mm / 25.4
    orient = "Landscape" if not j.grid.portrait else "Portrait"

    say("\n=======================================================", style="bold cyan")
    say("  Silhouette Studio Operator Guide (aligned with Card Maker)", style="bold cyan")
    say("=======================================================", style="bold cyan")
    say("1. Page Setup Panel (top-right icon):")
    say(f"   - Page Size:        {j.grid.sheet_name} ({w_mm:.1f} x {h_mm:.1f} mm / {w_in:.2f} x {h_in:.2f} in)")
    say(f"   - Orientation:      {orient}")
    say(f"   - Cutting Mat:      12 x 12 in (or Cameo mat)")
    say("2. Registration Marks (Page Setup -> 3rd tab):")
    say(f"   - Registration:     Type 1 (Cameo, Portrait, Curio)")
    say(f"   - Inset:            {j.marks.inset_mm:g} mm ({j.marks.inset_mm / 25.4:.3f} in)")
    say(f"   - Length:           {j.marks.reg_length_mm:g} mm ({j.marks.reg_length_mm / 25.4:.3f} in)")
    say(f"   - Thickness:        {j.marks.thickness_mm:g} mm")
    say("3. Verify Template Sizing (Transform Panel / Ctrl+A):")
    say(f"   - Group bounds:     {bw_mm:.2f} x {bh_mm:.2f} mm ({bw_in:.3f} x {bh_in:.3f} in)")
    say(f"   - Single card:      {cw_mm:.1f} x {ch_mm:.1f} mm ({cw_in:.3f} x {ch_in:.3f} in)")
    say("   - Note: If DXF imported scaled (e.g. 72/70 factor in Studio Basic Edition):")
    say(f"     Select All (Ctrl+A) -> Transform panel -> Lock Aspect Ratio -> set Width to {bw_mm:.2f} mm ({bw_in:.3f} in).")
    say("4. Blade & Cut Settings (Send tab):")
    p = j.preset
    s = j.settings
    say(f"   - Material Preset:  {p.name}")
    say(f"   - Settings:         Force {s.force}  |  Speed {s.speed}  |  Passes {s.passes}  |  Depth {s.depth or 7}")
    say("   - Guide hint:       Cover the bottom-left card with a Post-It note during the mark scan,")
    say("                       then remove it before cutting to prevent sensor confusion.")
    say("   - Tuning priority:  Passes -> Force -> Speed -> Depth.")
    say("                       If laminated edges ripple, ease off Force. If machine skips, reduce Speed.")
    say("=======================================================\n", style="bold cyan")


# --------------------------------------------------------------------------
# studio / winboat
# --------------------------------------------------------------------------

def cmd_studio(args):
    if args.release:
        ours = winboat.our_stale_sessions()
        if not ours:
            say("No silhouette-control-center RDP sessions are running.", style="bold")
            held = winboat.rdp_busy()
            if held:
                say(f"\n{held}", style="yellow")
                say("That one is not ours and is left alone. If it belongs "
                    "to another tool,\nclose it there rather than here.",
                    style="dim")
            return 0
        for pid, cmd in ours:
            say(f"  pid {pid}: {cmd[:110]}")
        if not args.yes and not ask_yes(
                f"\nClose {len(ours)} silhouette-control-center session(s)?", "n"):
            say("Left alone.")
            return 0
        closed = winboat.release_our_sessions()
        say(f"Closed {len(closed)} session(s).", style="bold green")
        return 0

    if args.run_helper:
        info = winboat.prepare()
        try:
            cmd = winboat.run_script(info["guest_script"],
                                     dry_run=args.dry_run)
        except RuntimeError as exc:
            fail(str(exc))
        say("\n" + " ".join(cmd), style="dim")
        say(f"\nRunning the helper in the guest. It writes "
            f"{info['report']} when it finishes.", style="bold")
        say("Nothing comes back over RDP, so read that file rather than "
            "watching the window.", style="dim")
        return 0

    if args.prepare:
        info = winboat.prepare()
        say("\nWinBoat guest preparation", style="bold")
        for step in info["steps"]:
            say(f"  {step}")
        return 0

    if getattr(args, "app_only", False):
        try:
            cmd = winboat.open_in_studio(None, dry_run=args.dry_run)
        except RuntimeError as exc:
            fail(str(exc))
        say("\n" + (" ".join(cmd)), style="dim")
        say("\nSilhouette Studio is launching in the guest (blank workspace)."
            if not args.dry_run else "\nDry run: not launched.", style="bold")
        return 0

    if getattr(args, "file", None):
        path = pathlib.Path(args.file).expanduser()
        if not path.is_file():
            fail(f"No such file: {path}")
        try:
            cmd = winboat.open_in_studio(path, dry_run=args.dry_run)
        except RuntimeError as exc:
            fail(str(exc))
        say("\n" + (" ".join(cmd)), style="dim")
        say(f"\nSilhouette Studio is opening in the guest with {path.name}."
            if not args.dry_run else "\nDry run: not launched.", style="bold")
        say("\nIn Studio: set the page to the same size and orientation, turn on "
            "registration\nmarks with the same inset, and check the template "
            "imported at 100%.", style="dim")
        return 0

    # No file: generate and stage DXF for the requested layout
    try:
        j = build_from_args(args)
    except geometry.LayoutImpossible as exc:
        fail(str(exc))
    show_summary(j)
    show_checks(j)
    if j.blocked:
        say()
        for reason in j.blocked:
            say(f"  {reason}", style="bold red")
        fail("The job does not check out. Nothing was sent.")

    from . import job as job_mod
    stem = (f"{j.grid.sheet_name.lower()}-{j.grid.card.name}-"
            f"{j.grid.cols}x{j.grid.rows}-"
            f"{'portrait' if j.grid.portrait else 'landscape'}-{j.mode}")
    stage_dir = winboat.stage_dir()
    written = job_mod.write_templates(j, stem, folder=stage_dir, proof=False)
    dxf_path = written["dxf"]
    say(f"\nDXF template generated and staged: {dxf_path.name}", style="bold green")
    try:
        cmd = winboat.open_in_studio(dxf_path, dry_run=args.dry_run)
    except RuntimeError as exc:
        fail(str(exc))
    say("\n" + (" ".join(cmd)), style="dim")
    say("\nSilhouette Studio is opening in the guest with that template."
        if not args.dry_run else "\nDry run: not launched.", style="bold")
    _show_studio_operator_guide(j)
    return 0


# --------------------------------------------------------------------------
# gui
# --------------------------------------------------------------------------

def cmd_gui(args):
    from . import gui
    return gui.main(args.argv)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="silhouette-control-center",
        description="Silhouette Cameo cut templates and cutting for proxy "
                    "card sheets.")
    sub = p.add_subparsers(dest="command")

    t = sub.add_parser("template", help="write the SVG / DXF / proof")
    common_layout_args(t)
    common_cut_args(t)
    t.add_argument("--name", help="file stem (default: describes the layout)")
    t.add_argument("--out", help="output directory")
    t.add_argument("--no-proof", action="store_true")
    t.set_defaults(func=cmd_template)

    la = sub.add_parser("layouts", help="what fits on what")
    la.add_argument("--card", default=config.DEFAULT_CARD)
    la.add_argument("--all-sheets", action="store_true",
                    help="include sizes the card stock is not sold in")
    la.set_defaults(func=cmd_layouts)

    pr = sub.add_parser("presets", help="blade presets and their provenance")
    pr.add_argument("name", nargs="?")
    pr.set_defaults(func=cmd_presets)

    pps = sub.add_parser("proxypress",
                         help="the Proxy Press settings that produce a sheet "
                              "this can cut, eight up")
    pps.add_argument("--sheet", default=config.DEFAULT_SHEET,
                     choices=[n for n, v in sorted(geometry.SHEETS.items())
                              if v.stocked])
    pps.set_defaults(func=cmd_proxypress)

    im = sub.add_parser("import",
                        help="read an SVG or DXF and report what is in it")
    im.add_argument("file")
    im.add_argument("-v", "--verbose", action="store_true",
                    help="list every path")
    im.set_defaults(func=cmd_import)

    cs = sub.add_parser("check-sheet",
                        help="measure a PDF and check its "
                             "registration marks are where the template "
                             "expects")
    cs.add_argument("file", help="PDF to inspect (does not measure physical printer output)")
    cs.add_argument("--page", type=int, default=1)
    cs.add_argument("--dpi", type=int, default=200)
    cs.add_argument("--tolerance", type=float, default=0.5, metavar="MM")
    common_layout_args(cs)
    common_cut_args(cs)
    cs.set_defaults(func=cmd_check_sheet)

    tc = sub.add_parser("testcut",
                        help="a sheet of test squares, one per settings "
                             "combination, to find your cut settings")
    tc.add_argument("--sheet", default=config.DEFAULT_SHEET,
                    choices=sorted(geometry.SHEETS))
    tc.add_argument("--landscape", action="store_true")
    tc.add_argument("--cutter", default="Silhouette Cameo 4")
    tc.add_argument("--preset", default=config.DEFAULT_PRESET,
                    help="the baseline; any axis you do not vary keeps its "
                         "value from here")
    tc.add_argument("--force", help="comma-separated, e.g. 25,29,33")
    tc.add_argument("--speed", help="comma-separated")
    tc.add_argument("--passes", help="comma-separated")
    tc.add_argument("--depth", help="comma-separated, AutoBlade only")
    tc.add_argument("--size", type=float, default=15.0, metavar="MM")
    tc.add_argument("--gap", type=float, default=5.0, metavar="MM")
    tc.add_argument("--margin", type=float, default=15.0, metavar="MM")
    tc.add_argument("--cols", type=int, default=0)
    tc.add_argument("--registration", action="store_true",
                    help="scan for marks first; off by default, because a "
                         "settings test should use a blank offcut")
    tc.add_argument("--transport", default="auto",
                    choices=("auto", "pyusb", "usblp"))
    tc.add_argument("--out", help="also write a labelled map SVG here")
    tc.add_argument("--dump", metavar="PATH")
    tc.add_argument("--dry-run", action="store_true")
    tc.add_argument("-y", "--yes", action="store_true")
    tc.set_defaults(func=cmd_testcut)

    cal = sub.add_parser("calibrate",
                         help="the standing blade-vs-sensor offset")
    cal.add_argument("--x", type=float, help="mm, positive is right")
    cal.add_argument("--y", type=float, help="mm, positive is down")
    cal.add_argument("--clear", action="store_true")
    cal.set_defaults(func=cmd_calibrate)

    ma = sub.add_parser("materials",
                        help="Silhouette's own material settings, read from "
                             "a Studio installation")
    ma.add_argument("--file", help="path to a Studio condition.csv")
    ma.add_argument("--cutter-class", default="",
                    help="prefer rows for this cutter class, e.g. cc300-20")
    ma.add_argument("--save", metavar="MATERIAL",
                    help="save that material as a preset")
    ma.set_defaults(func=cmd_materials)

    st = sub.add_parser("status", help="cutter, transports, WinBoat guest")
    st.set_defaults(func=cmd_status)

    c = sub.add_parser("cut", help="register and cut")
    common_layout_args(c)
    common_cut_args(c)
    c.add_argument("--via", default="cutter", choices=("cutter", "studio"),
                   help="cutter = cut via USB directly; studio = stage DXF "
                        "and open in Silhouette Studio in WinBoat guest")
    c.add_argument("--transport", default="auto",
                   choices=("auto", "pyusb", "usblp"))
    c.add_argument("--dry-run", action="store_true",
                   help="build and check the job, send nothing")
    c.add_argument("--dump", metavar="PATH",
                   help="also write the raw stream to a file")
    c.add_argument("--manual-registration", action="store_true",
                   help="park the head on the first mark yourself (TB23) "
                        "instead of an automatic search")
    c.add_argument("--no-registration", action="store_true",
                   help="cut without registering. Only for a blank sheet.")
    c.add_argument("-y", "--yes", action="store_true")
    c.set_defaults(func=cmd_cut)

    s = sub.add_parser("studio", help="open a template in Silhouette Studio "
                                      "in the WinBoat guest")
    s.add_argument("file", nargs="?",
                   help="specific DXF/SVG file to open (optional)")
    common_layout_args(s)
    common_cut_args(s)
    s.add_argument("--app-only", action="store_true",
                   help="launch Silhouette Studio with a blank workspace")
    s.add_argument("--prepare", action="store_true",
                   help="stage the guest helper script and explain the steps")
    s.add_argument("--release", action="store_true",
                   help="close stale silhouette-control-center RDP sessions. Never "
                        "touches another tool's session.")
    s.add_argument("--run-helper", action="store_true",
                   help="stage the helper and run it in the guest over RDP")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_studio)

    g = sub.add_parser("gui", help="open the window")
    g.add_argument("argv", nargs="*")
    g.set_defaults(func=cmd_gui)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        # No subcommand: the window is the friendlier default, but only if it
        # can actually open.
        try:
            import gi  # noqa: F401
            from . import gui
            return gui.main([])
        except Exception:            # noqa: BLE001
            p.print_help()
            return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
