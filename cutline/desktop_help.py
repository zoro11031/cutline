"""Help shared by the native desktop frontends."""

BLADE_VALUE_TEXT = {"depth": "Manual", "acceleration": "Auto", "light_passes": "Off",
                    "light_force": "Same as Force", "light_speed": "Same as Speed"}

CONTROL_HELP = {
    "force": "How hard the tool presses.",
    "speed": "How fast the tool moves.",
    "passes": "How many times the tool goes over each cut.",
    "depth": "Sets AutoBlade depth in holder 1 on compatible cutters. "
             "Choose Manual to leave the depth set on the tool.",
    "holder": "The slot holding the tool.",
    "acceleration": "How quickly the cutter speeds up and slows down.",
    "blade_diameter_mm": "Helps a blade turn at corners. Use 0 for a pen.",
    "sharpen_corners": "Slightly overcut straight corners for cleaner edges on thick stock or small sharp shapes. "
                       "Has no effect on rounded edges.",
    "corner_start_mm": "How far to extend the start of the cut.",
    "corner_end_mm": "How far to extend the end of the cut.",
    "track_enhancing": "Feed the sheet back and forth before cutting to grip thick stock and prevent slipping across passes.",
    "path_order": "Choose the order in which shapes are cut.",
    "pass_order": "Choose when the cutter repeats each cut.",
    "interior_first": "Cut holes before the outside edge.",
    "delicate_media": "Change the cut order to help keep thin material in place.",
    "overcut_mm": "Overlap the start and end of a closed cut.",
    "light_passes": "How many of the passes go first at the light force and speed. "
                    "The rest use Force and Speed. Passes is still the total.",
    "light_force": "Force for the light first passes, to ease through a top film before full force.",
    "light_speed": "Speed for the light first passes.",
    "registration": "Use printed marks to line up the cut.",
    "auto_search": "Let the cutter find the first mark on its own.",
    "mark_inset_mm": "Distance from the paper edge to the marks.",
    "mark_length_mm": "Length of the marks. Auto fits them around your cards.",
    "mark_thickness_mm": "Width of the printed mark lines.",
    "bleed_mm": "How far the artwork extends past each card edge.",
    "mat": "Choose the mat you loaded.",
    "end_position": "Choose where the cutter stops after the job.",
    "end_feed_mm": "How far to feed past the last cut.",
}

SETUP_STEPS = (
    "1. Open or draw: Open a template, import a print bundle, or choose Draw in Design for a blank page. Use Curve for smooth paths through clicked points.\n\n"
    "2. Choose material: In Send → Cut Job, connect your cutter and select a material.\n\n"
    "3. Test & cut: For printed sheets, choose Test Registration under Registration in Cut Job. Then choose Send to Cutter."
)

# Adapted from the Silhouette Card Maker cutting guide and troubleshooting pages:
# https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/
# https://alan-cha.github.io/silhouette-card-maker/tutorial/troubleshooting/
# The coated, core and foil stock advice is reported by other users, not measured
# here: see docs/how-it-works.md, "Materials and presets".
# Each entry is (advice, action); the action opens the controls the advice refers to.
OPEN_SHEET = "Open Sheet"
DIAL_IN = "Dial In a Material"
CUT_SETTINGS = "Cut Settings"
TRACK_OFF = "If Track Enhancing is on in Cut Settings, try once with it off."
CUT_PROBLEMS = {
    "Registration marks won’t scan": (
        "1. Print at Actual size (100%) and align paper edges to the preview.\n"
        "2. If nearby dark artwork confuses the sensor, mask it with a light sticky note.\n"
        "3. Reduce glare with diffuse light, or close the cutter lid.\n"
        "4. In Sheet, choose Check PDF Marks. Then choose Test Registration in Cut Job.\n"
        + TRACK_OFF, OPEN_SHEET),
    "Registration fails on foil or glossy stock": (
        "1. Reduce glare: close the lid or use indirect light.\n"
        "2. Place clear matte tape flat over the marks to stop reflections.\n"
        "3. Or print marks on opaque matte tape placed over the stock.\n"
        "4. Run Test Registration with the sheet loaded.", OPEN_SHEET),
    "Cut is too shallow or still attached": (
        "1. Verify the blade is fully seated and a material preset is selected.\n"
        "2. In Dial In a Material, cut one pass and lift a corner while loaded.\n"
        "3. If close, repeat on the same spot; if barely scored, add force or depth.\n"
        "4. If still inconsistent, replace the blade or cutting mat.", DIAL_IN),
    "Cut edges are rough or laminate ripples": (
        "1. Reduce force first on a fresh spot (excess force tears laminate).\n"
        "2. Keep speed moderate; speed matters much less than force.\n"
        "3. If the cleaner cut holds, repeat a pass at that spot.\n"
        "4. Check the blade tip for wear or adhesive buildup.", DIAL_IN),
    "Printed coating chips or tears at the edge": (
        "1. Cut light first: the printed coating is weaker than the core.\n"
        "2. Run gentle first passes to cut the coating, then full-force passes to finish.\n"
        "3. In Dial In a Material, test light Settings A then full Settings B.\n"
        "4. In Cut Settings, enable Lighter first passes under Passes.", DIAL_IN),
    "Fibers left on the back of the card": (
        "1. Coating cut cleanly but core base is holding: add one full pass.\n"
        "2. Or increase force slightly on full passes only (keep light passes low).\n"
        "3. Trim any stray fibers with a hobby knife.\n"
        "4. If dull cuts persist, install a fresh blade.", DIAL_IN),
    "Carriage skips during a cut": (
        "1. Power-cycle the cutter after motion stops so it can rehome.\n"
        "2. Reduce force or speed on a fresh spot (blade is catching).\n"
        "3. Reduce blade depth to the minimum exposure needed for your stock.\n"
        "4. Add a pass if the lighter cut stays attached.", DIAL_IN),
    "Cuts are angled or the mat slips": (
        "1. Stop the job and confirm both pinch rollers grip the mat edges.\n"
        "2. Align the mat against the cutter's loading guide.\n"
        "3. Mask cards near corner marks so the scan does not mistake them.\n"
        "4. If carriage skipped, power-cycle after motion stops to rehome.\n"
        + TRACK_OFF, OPEN_SHEET),
    "Cut is in the wrong place": (
        "1. Verify cut file and PDF match (compare Overlay preview in Sheet).\n"
        "2. Check paper edge and feed arrow, then run Test Registration.\n"
        "3. If offset is uniform, calibrate under Send → Advanced Options → Calibration.\n"
        + TRACK_OFF, OPEN_SHEET),
    "Front and back are offset": (
        "1. Check printer duplex settings: Card Maker layouts use long-edge flip.\n"
        "2. To measure and align back offset, follow Silhouette Card Maker’s alignment guide: "
        "https://alan-cha.github.io/silhouette-card-maker/docs/offset/\n"
        "3. If both sides shift against the cut, compare registration and template placement instead.",
        OPEN_SHEET),
    "Cards print or cut at the wrong size": (
        "1. Print at Actual size (100%) and verify selected sheet size matches.\n"
        "2. Measure mark spacing with a ruler in both directions.\n"
        "3. Fix printer scaling at 100% before adjusting cutter calibration.",
        OPEN_SHEET),
    "Laminate lifts or the sheet curls": (
        "1. Test pouches on offcuts with different heat and speed settings.\n"
        "2. If layers lift, increase heat slightly or run through laminator twice.\n"
        "3. If sheet curls, reduce heat and cool flat under a heavy book.", None),
    "Rollers shift, skew or mark the sheet": (
        "1. Turn off Track Enhancing in Cut Settings and try again.\n"
        "2. Keep it on only if the mat slips without it.\n"
        "3. Check Cut Settings again after selecting a material, as some presets enable it.",
        CUT_SETTINGS),
}
REGISTRATION_PROBLEM = next(iter(CUT_PROBLEMS))
