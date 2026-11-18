"""silhouette-control-center on the command line.

Subcommands, in the order a sheet goes through them:

  template   lay out a sheet and write the SVG / DXF / proof
  layouts    what fits on what, including the ones that do not fit
  presets    the blade presets, and where each number came from
  status     the cutter and the transports
  cut        register and cut (or --dry-run to build and check only)
  gui        the window

`cut` refuses rather than guesses.  Anything in `Job.blocked` stops it.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import pathlib
import sys

from . import (actions, bluetooth, config, geometry, graphtec, job as job_mod, layout_presets, presets, regmarks,
               template, testcut, toolpath, transport, verify)

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
    probe_count = len(verify.applicable_probes(j.rows))
    say(f"  [{'ok  ' if live else 'FAIL'}] {'checks proven live':26} "
        f"{max(0, probe_count - len(j.liveness_failures))}/{probe_count}"
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
        say("Use Control Center's Test scan to check the printed marks without cutting.", style="dim")


def common_layout_args(p, bundles=False):
    p.add_argument('--layout-preset', choices=tuple(layout_presets.BUILTIN),
                   help='apply a complete printed-sheet layout; overrides layout '
                        'options and leaves blade settings unchanged')
    p.add_argument("--sheet", choices=sorted(geometry.SHEETS))
    p.add_argument("--card", default=config.DEFAULT_CARD,
                   help="card size name (default: standard = 63x88 mm)")
    orient = p.add_mutually_exclusive_group()
    orient.add_argument("--portrait", dest="portrait", action="store_true", default=None)
    orient.add_argument("--landscape", dest="portrait", action="store_false")
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
                   help="use paths from " + ("an SVG, DXF, Studio3 or matched ProxyPress ZIP"
                                             if bundles else "an SVG, DXF or Studio3 template"))
    p.add_argument("--svg-page", type=int, metavar="N",
                   help="select a page in an Inkscape SVG (starting at 1)")
    p.add_argument("--color", "--colour", dest="colour", metavar="COLOUR",
                   help="cut only the paths with this stroke colour, the "
                        "way Studio selects by line colour (e.g. red, "
                        "#ff0000)")
    p.add_argument("--weed-border-mm", type=float, default=0.0, metavar="MM",
                   help="padding for a Weed Border around imported paths")
    p.add_argument("--weed-lines", choices=toolpath.WEED_LINES, default="none",
                   help="add divider lines in clear gaps between imported contours")
    p.add_argument("--skip", type=int, action="append", default=[],
                   metavar="N",
                   help="leave card N uncut (0-based). Use it for the slot "
                        "left empty so a card stops crowding a mark; repeat "
                        "for more than one")


def common_cut_args(p):
    p.add_argument("--cutter", help="cutter model (Bluetooth detects it for live jobs; otherwise defaults to Cameo 4)")
    p.add_argument("--preset", default=config.DEFAULT_PRESET)
    p.add_argument("--force", type=int, default=None)
    p.add_argument("--speed", type=int, default=None)
    p.add_argument("--passes", type=int, default=None)

    p.add_argument("--depth", type=int, choices=range(-1, 11), help="AutoBlade depth 0–10; -1 leaves it unset")
    advanced_cut_args(p)
    media_handling_args(p)


def media_handling_args(p):
    p.add_argument("--mat", choices=graphtec.MAT_OPTIONS, default="auto",
                   help="select the physical cutting mat or use --mat none")
    p.add_argument("--end-position", choices=graphtec.END_POSITIONS, default="below",
                   help="feed past the cut or return to origin")
    p.add_argument("--feed-mm", type=float, default=0.0, metavar="MM",
                   help="additional distance past the cut in Feed mode")


def advanced_cut_args(p):
    p.add_argument("--holder", type=int, choices=(1, 2))
    p.add_argument("--acceleration", type=int, choices=range(-1, 4), help="-1 uses the automatic TJ sequence")
    p.add_argument("--blade-diameter-mm", type=float, choices=[i / 20 for i in range(47)], metavar="MM")
    p.add_argument("--lift-head", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--line-segment-overcut", "--sharpen-corners", dest="sharpen_corners",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="lift and overcut sharp corners")
    p.add_argument("--track-enhancing", action=argparse.BooleanOptionalAction, default=None,
                   help="run the rollers before registration or cutting")
    for flag in ("--corner-start-mm", "--corner-end-mm"):
        p.add_argument(flag, type=float, choices=[i / 10 for i in range(10)], metavar="MM",
                       help="line segment extension from 0 to 0.9 mm")

    p.add_argument("--path-order", choices=toolpath.PATH_ORDERS,
                   help="no sort, maximize speed, or minimize roller movement")
    p.add_argument("--interior-first", action=argparse.BooleanOptionalAction, default=None,
                   help="cut nested paths before enclosing contours")
    p.add_argument("--delicate-media", action=argparse.BooleanOptionalAction, default=None,
                   help="sort interiors first and alternate the winding of closed contours")
    p.add_argument("--pass-order", choices=toolpath.PASS_ORDERS,
                   help="repeat the whole sheet or finish each path first")
    p.add_argument("--alternate-direction", action=argparse.BooleanOptionalAction, default=None,
                   help="reverse the direction on every second pass")
    p.add_argument("--overcut-mm", type=float, metavar="MM",
                   help="seam overcut on closed contours")


def advanced_settings(args):
    options = {key: getattr(args, key) for key in
               ("holder", "acceleration", "blade_diameter_mm", "lift_head",
                "sharpen_corners", "corner_start_mm", "corner_end_mm", "path_order",
                "pass_order", "alternate_direction", "overcut_mm", "interior_first",
                "delicate_media", "track_enhancing")
               if getattr(args, key, None) is not None}
    if options.get("acceleration") == -1:
        options["acceleration"] = None
    return options


def build_from_args(args, matched=None, bundle_row=None) -> job_mod.Job:
    imported = matched.artwork(bundle_row) if matched else None
    send_mode = getattr(args, "send_by", "simple")
    if send_mode != "simple" and getattr(args, "colour", None):
        fail("--color is available in Simple mode. Assign groups with --group in Line mode.")
    assignments = tuple(actions.parse_assignment(value) for value in getattr(args, "group", ()))
    source = getattr(args, "source", None)
    svg_page = getattr(args, "svg_page", None)
    if svg_page is not None and (not source or matched):
        fail("--svg-page requires --from an Inkscape SVG.")
    if source and matched is None:
        from . import importer
        try:
            imported = importer.load(source, page=svg_page)
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
        send_mode=send_mode,
        assignments=assignments,
        mat=getattr(args, "mat", "auto"),
        end_position=getattr(args, "end_position", "below"),
        end_feed_mm=getattr(args, "feed_mm", 0.0),
        weed_border_mm=getattr(args, "weed_border_mm", 0.0),
        weed_lines=getattr(args, "weed_lines", "none"),
        sheet=args.sheet or config.DEFAULT_SHEET, card=args.card,
        portrait=config.DEFAULT_PORTRAIT if args.portrait is None else args.portrait,
        engine=args.engine,
        preset_name=getattr(args, "preset", config.DEFAULT_PRESET),
        cutter=getattr(args, "cutter", None) or "Silhouette Cameo 4",
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
    if imported and imported.studio3_page:
        if args.sheet is None and args.portrait is None:
            options["use_document_page"] = True
        elif (paper := geometry.match_sheet(imported.page_size_mm)) is not None:
            options["sheet"] = args.sheet or paper[0]
            if args.portrait is None:
                options["portrait"] = paper[1]
    options["blade_options"] = advanced_settings(args)
    if getattr(args, "depth", None) is not None:
        options["depth"] = None if args.depth == -1 else args.depth
    selected = getattr(args, 'layout_preset', None)
    if selected:
        options.update(layout_presets.BUILTIN[selected].options(), use_document_page=False)
    if matched:
        options.update(matched.layout, engine='scm', mode=template.ROUNDED, bleed_mm=0)
    return job_mod.build(**options)


# --------------------------------------------------------------------------
# template
# --------------------------------------------------------------------------

def cmd_template(args):
    try:
        j = build_from_args(args)
    except ValueError as exc:
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
        f"(0 = auto: Card Maker arm shortened for clearance when needed)")
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
        im = importer.load(args.file, page=getattr(args, "svg_page", None))
    except (importer.ImportError_, OSError) as exc:
        fail(str(exc))
    say(f"\n{pathlib.Path(args.file).name}", style="bold")
    say(f"  {im.describe()}")
    say(f"  declared size: {im.width_mm:.1f} x {im.height_mm:.1f} mm")
    x0, y0, x1, y1 = im.bounds
    say(f"  geometry:      {x1 - x0:.2f} x {y1 - y0:.2f} mm "
        f"at ({x0:.2f}, {y0:.2f})")
    if group_mode := getattr(args, "list_groups", None):
        for key, paths in actions.group_paths(im.paths, group_mode).items():
            say(f"  {actions.SEND_MODES[group_mode]} group {key} ({len(paths)} paths)")
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
    from . import bundle, sheet as sheet_mod
    path = pathlib.Path(args.file).expanduser()
    if not path.is_file():
        fail(f"No such file: {path}")
    matched = None
    try:
        if path.suffix.lower() == '.zip':
            matched = bundle.load(path)
            row = matched.row(args.bundle_sheet)
            marks = matched.marks()
            report, _ = matched.measure(row, marks, args.dpi)
        else:
            j = build_from_args(args)
            marks = j.marks
            report = sheet_mod.measure(path, marks, page=args.page, dpi=args.dpi)
    except (ValueError, sheet_mod.SheetError, bundle.BundleError) as exc:
        fail(str(exc))

    say("PDF geometry only; measure the physical print separately.", style="dim")
    say(f"\nMeasured {pathlib.Path(report.pdf).name}, page {report.page}, at {report.dpi} dpi",
        style="bold")
    say(f"  page {report.page_w_mm:.1f} x {report.page_h_mm:.1f} mm "
        f"(template expects {marks.sheet_w_mm:.1f} x "
        f"{marks.sheet_h_mm:.1f})")
    say()
    for m in report.marks:
        ok = m.is_credible and (m.error_mm or 0) <= args.tolerance
        say(f"  [{'ok  ' if ok else 'FAIL'}] {m.describe()}",
            style=None if ok else "bold red")

    problems = report.problems(marks.sheet_w_mm, marks.sheet_h_mm,
                               args.tolerance)
    say(f"\n  {report.diagnosis()}", style="bold")
    if not problems:
        say("\nThe PDF registration marks match the template.", style="bold green")
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

def connection_args(parser):
    parser.add_argument("--transport", choices=("auto", "pyusb", "usblp", *bluetooth.BLUETOOTH_MODES),
                        default="auto")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--bluetooth-id", "--bluetooth-address", metavar="ID",
                        help="MAC address or operating-system BLE identifier")
    target.add_argument("--bluetooth-name", metavar="NAME", help="exact or unique partial name")
    parser.add_argument("--bluetooth-channel", type=int, metavar="N", help="RFCOMM channel (default 1)")
    parser.add_argument("--allow-unrecognized-firmware", action="store_true",
                        help="use an explicit cutter model for an unrecognized firmware reply")


def connection_options(args):
    options = {
        "bluetooth_id": args.bluetooth_id, "bluetooth_name": args.bluetooth_name,
        "bluetooth_channel": args.bluetooth_channel,
        "allow_unknown_firmware": args.allow_unrecognized_firmware,
    }
    if any(value is not None and value is not False for value in options.values()) and (
            args.transport not in bluetooth.BLUETOOTH_MODES):
        fail("Bluetooth options require --transport ble or --transport bluetooth.")
    if args.bluetooth_channel is not None and (args.transport != "bluetooth" or
                                                not 1 <= args.bluetooth_channel <= 30):
        fail("--bluetooth-channel requires Bluetooth Classic and a value from 1 to 30.")
    if args.allow_unrecognized_firmware and not (args.bluetooth_id and getattr(args, "cutter", None)):
        fail("--allow-unrecognized-firmware requires --bluetooth-id and --cutter.")
    return {key: value for key, value in options.items() if value is not None and value is not False}


@contextlib.contextmanager
def cut_connection(args):
    options = connection_options(args)
    with contextlib.ExitStack() as stack:
        link = None
        if (args.transport in bluetooth.BLUETOOTH_MODES and not args.cutter and not args.dry_run):
            try:
                link = stack.enter_context(transport.open_transport(args.transport, **options))
            except transport.TransportError as exc:
                fail(str(exc))
            args.cutter = link.device.name
        yield link, options


def cmd_testcut(args):
    with cut_connection(args) as (link, options):
        return _testcut(args, link, options)


def _testcut(args, link, options):
    device = graphtec.device_by_name(args.cutter or "Silhouette Cameo 4")
    base = dataclasses.replace(presets.get(args.preset).cut_settings(), **advanced_settings(args))
    sheet = geometry.sheet(args.sheet)
    w, h = sheet.size(portrait=not args.landscape)
    try:
        ts = testcut.build(
            device, base, forces=args.force, speeds=args.speed,
            passes=args.passes, depths=args.depth, size_mm=args.size,
            gap_mm=args.gap, margin_mm=args.margin, cols=args.cols,
            sheet_w_mm=w, sheet_h_mm=h, registration=args.registration,
            offset_mm=config.cut_offset(), shape=args.shape, mat=args.mat,
            end_position=args.end_position, end_feed_mm=args.feed_mm)
    except ValueError as exc:
        fail(str(exc))

    label = "square-and-triangle samples" if ts.shape == "studio" else "squares"
    say(f"\nTest sheet: {len(ts.cells)} {label} of {ts.size_mm:g} mm on "
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
        tr = contextlib.nullcontext(link) if link else transport.open_transport(
            args.transport, device=ts.device, **options)
    except transport.TransportError as exc:
        say(f"\n{exc}", style="bold red")
        fail("No way to reach the cutter. Use --dry-run.")
    with tr as cutter:
        if cutter.can_read:
            state = cutter.status()
            say(f"\nCutter reports: {state}")
            if state != "ready":
                fail(f"The cutter is not ready ({state}).")
        if not args.yes and not ask_yes("\nCut the test sheet now?", "n"):
            say("Cancelled.")
            return 0
        try:
            sent = cutter.run(ts.steps, log=lambda m: say("  " + m))
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

def show_device_diagnostics(cutter, firmware=True):
    for label, value in cutter.diagnostics():
        if firmware or label != "Firmware":
            say(f"  {label}: {value}")


def cmd_status(args):
    options = connection_options(args)
    if args.transport in bluetooth.BLUETOOTH_MODES:
        from . import ble, rfcomm
        label = bluetooth.BLUETOOTH_MODES[args.transport]
        scanner = ble if args.transport == "ble" else rfcomm
        try:
            if args.bluetooth_id or args.bluetooth_name or args.diagnostics:
                with transport.open_transport(args.transport, device=(
                        graphtec.device_by_name(args.cutter) if args.cutter else None), **options) as cutter:
                    say(f"{cutter.device.name} ({cutter.identifier}): {cutter.status_with_tools()}")
                    say(f"  Firmware: {cutter.firmware_version()}")
                    if args.diagnostics:
                        show_device_diagnostics(cutter, firmware=False)
            else:
                devices = scanner.discover(include_all=args.all_bluetooth)
                for address, name in devices:
                    say(f"{address}  {name or 'Unnamed device'}")
                if not devices:
                    say(f"No Silhouette cutter found over {label}.")
        except (transport.TransportError, ValueError, KeyError) as exc:
            fail(str(exc))
        return 0
    if args.all_bluetooth:
        fail("--all-bluetooth requires --transport ble or --transport bluetooth.")

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
            with transport.open_transport(args.transport) as t:
                say(f"  {t.name}: {t.status_with_tools()}")
                if args.diagnostics:
                    show_device_diagnostics(t)
        except transport.TransportError as exc:
            say(f"  could not open the cutter: {exc}", style="yellow")

    return 0


# --------------------------------------------------------------------------
# cut
# --------------------------------------------------------------------------

def cmd_cut(args):
    with cut_connection(args) as (link, options):
        return _cut(args, link, options)


def _cut(args, link, options):
    from . import bundle, sheet as sheet_mod
    matched = row = report = None
    try:
        if args.source and pathlib.Path(args.source).suffix.lower() == '.zip':
            if args.no_registration:
                fail('A matched ProxyPress ZIP requires registration.')
            matched = bundle.load(args.source)
            row = matched.row(args.bundle_sheet)
            j = build_from_args(args, matched, row)
            report, _ = matched.measure(row, j.marks, sheet_mod.DEFAULT_DPI)
            problems = report.problems(j.grid.sheet_w_mm, j.grid.sheet_h_mm)
            if problems:
                fail('PDF registration check failed. Nothing was sent.\n' + '\n'.join(problems))
            say(f"Matched sheet {row['sheet']} with PDF front page {row['frontPage']}.")
            say(matched.mat_placement())
        else:
            j = build_from_args(args)
    except (ValueError, bundle.BundleError) as exc:
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

    if args.dry_run:
        say("\nDry run: everything checks out, nothing sent.", style="bold")
        return 0

    used_presets = (dict.fromkeys(group.preset.name for group in j.action_groups
                                  if group.preset.origin != "measured")
                    if j.action_groups else {j.preset.name: None} if j.preset.origin != "measured" else {})
    for name in used_presets:
        warn(f"The {name} preset needs a hardware test on this stock.")

    try:
        connection = contextlib.nullcontext(link) if link else transport.open_transport(
            args.transport, device=j.device, **options)
    except transport.TransportError as exc:
        say(f"\n{exc}", style="bold red")
        fail("No way to reach the cutter. Check the connection or use --dry-run.")

    with connection as t:
        if args.transport in bluetooth.BLUETOOTH_MODES:
            say(f"\nBluetooth target: {j.device.name} ({t.identifier})")
        if t.can_read:
            try:
                state = t.status()
            except transport.TransportError as exc:
                fail(str(exc))
            say(f"\nCutter reports: {state}")
            if state == "unloaded":
                fail("The cutter says no media is loaded. Load the mat and "
                     "the sheet first.")
            elif state != "ready" and args.transport in bluetooth.BLUETOOTH_MODES:
                fail(f"The Bluetooth cutter is not ready ({state}).")
            elif state == "moving":
                fail("The cutter is busy.")
        if j.registration and not j.auto_search:
            say("\nPosition the cutter head over the first mark before starting the scan.")
        prompt = "Register and cut now?" if j.registration else "Cut now?"
        if not args.yes and not ask_yes(f"\n{prompt}", "n"):
            say("Cancelled. Nothing cut.")
            return 0
        say("\nRunning...", style="bold")
        try:
            sent = t.run(
                _plan(j),
                log=lambda m: say("  " + m), wait_done=args.wait_done)
        except transport.RegistrationFailed as exc:
            notes = j.registration_notes(report, matched.near_marks(row) if matched else None)
            notes.append("Check that the printed front is at 100% scale and all marks are visible.")
            notes.append("Commands sent before the reply (one write per line)")
            notes.extend(j.registration_writes())
            fail("\n".join([str(exc), *notes]))
        except transport.TransportError as exc:
            fail(str(exc))
        say(f"Sent {sent:,} bytes.", style="bold green")
    return 0


def _plan(j):
    """The exact write/read plan built and verified for this job."""
    return j.steps


# --------------------------------------------------------------------------
# gui
# --------------------------------------------------------------------------

def cmd_gui(args):
    return launch_gui(args.frontend, args.argv)


def launch_gui(frontend="auto", argv=None):
    from importlib.util import find_spec

    if frontend == "qt" or (frontend == "auto" and find_spec("PySide6") is not None):
        try:
            from . import qt_gui
        except ImportError as exc:
            if frontend == "qt":
                fail(f"The Qt frontend needs PySide6 with Qt Widgets. {exc}")
        else:
            return qt_gui.main(argv)

    try:
        from . import gui
    except ImportError as exc:
        fail(f"Install PySide6 for the Qt frontend or GTK4 and libadwaita for the GTK frontend. {exc}")
    return gui.main(argv)


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
                        help="read an SVG, DXF or Studio3 template and report what is in it")
    im.add_argument("file")
    im.add_argument("--svg-page", type=int, metavar="N",
                    help="select a page in an Inkscape SVG (starting at 1)")
    im.add_argument("--list-groups", choices=("line", "fill", "layer"),
                    help="list keys for use with cut --send-by and --group")
    im.add_argument("-v", "--verbose", action="store_true",
                    help="list every path")
    im.set_defaults(func=cmd_import)

    cs = sub.add_parser("check-sheet",
                        help="measure a PDF and check its "
                             "registration marks are where the template "
                             "expects")
    cs.add_argument("file", help="PDF or matched ProxyPress ZIP to inspect")
    cs.add_argument("--bundle-sheet", type=int, help="sheet number in a ProxyPress ZIP")
    cs.add_argument("--page", type=int, default=1, help="page number for standalone PDFs")
    cs.add_argument("--dpi", type=int, default=200)
    cs.add_argument("--tolerance", type=float, default=0.5, metavar="MM")
    common_layout_args(cs)
    common_cut_args(cs)
    cs.set_defaults(func=cmd_check_sheet)

    tc = sub.add_parser("testcut",
                        help="cut one test shape per settings combination")
    tc.add_argument("--sheet", default=config.DEFAULT_SHEET,
                    choices=sorted(geometry.SHEETS))
    tc.add_argument("--landscape", action="store_true")
    tc.add_argument("--cutter")
    tc.add_argument("--preset", default=config.DEFAULT_PRESET,
                    help="the baseline; any axis you do not vary keeps its "
                         "value from here")
    tc.add_argument("--force", help="comma-separated, e.g. 25,29,33")
    tc.add_argument("--speed", help="comma-separated")
    tc.add_argument("--passes", help="comma-separated")
    advanced_cut_args(tc)
    media_handling_args(tc)
    tc.add_argument("--depth", help="comma-separated, AutoBlade only")
    tc.add_argument("--shape", choices=testcut.TEST_SHAPES, default="square",
                    help="a plain square or Studio's square-and-triangle cut")
    tc.add_argument("--size", type=float, metavar="MM",
                    help="sample width (default 15 mm for square or 10 mm for Studio)")
    tc.add_argument("--gap", type=float, default=5.0, metavar="MM")
    tc.add_argument("--margin", type=float, default=15.0, metavar="MM")
    tc.add_argument("--cols", type=int, default=0)
    tc.add_argument("--registration", action="store_true",
                    help="scan for marks first; off by default, because a "
                         "settings test should use a blank offcut")
    connection_args(tc)
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

    st = sub.add_parser("status", help="cutter and transports")
    connection_args(st)
    st.add_argument("--cutter", help="firmware model override")
    st.add_argument("--all-bluetooth", action="store_true", help="include unnamed and unrecognized devices")
    st.add_argument("--diagnostics", action="store_true", help="query firmware and calibration data")
    st.set_defaults(func=cmd_status)

    c = sub.add_parser("cut", help="register and cut")
    common_layout_args(c, bundles=True)
    common_cut_args(c)
    c.add_argument("--send-by", choices=actions.SEND_MODES, default="simple",
                   help="send everything together or group by line color, fill color, or layer")
    c.add_argument("--group", action="append", default=[], metavar="GROUP=PRESET",
                   help="enable a group in send order. Use GROUP=2:PRESET for holder 2")
    connection_args(c)
    c.add_argument("--dry-run", action="store_true",
                   help="build and check the job, send nothing")
    c.add_argument("--bundle-sheet", type=int, help="sheet number in a ProxyPress ZIP")
    c.add_argument("--dump", metavar="PATH",
                   help="also write the raw stream to a file")
    c.add_argument("--manual-registration", action="store_true",
                   help="park the head on the first mark yourself (TB23) "
                        "instead of an automatic search")
    c.add_argument("--no-registration", action="store_true",
                   help="cut without registering. Only for a blank sheet.")
    c.add_argument("--wait-done", action="store_true", help="wait for the cutter to report ready after sending")
    c.add_argument("-y", "--yes", action="store_true")
    c.set_defaults(func=cmd_cut)

    g = sub.add_parser("gui", help="open the window")
    g.add_argument("--frontend", choices=("auto", "qt", "gtk"), default="auto",
                   help="auto prefers Qt when PySide6 is installed")
    g.add_argument("argv", nargs="*")
    g.set_defaults(func=cmd_gui)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        # The launcher can also be called from an SSH session or a TTY.
        import os
        if not any(os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM")):
            p.print_help()
            return 0
        return launch_gui()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
