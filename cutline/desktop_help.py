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
    "sharpen_corners": "Lift the blade and extend the cuts at sharp corners.",
    "corner_start_mm": "How far to extend the start of the cut.",
    "corner_end_mm": "How far to extend the end of the cut.",
    "track_enhancing": "Run the rollers before cutting. Use force 19 or higher.",
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
    "1. Draw in Design. Or switch to Send and open a cut template on the Layout tab, "
    "or a print bundle ZIP on the Sheet tab.\n\n"
    "2. On Send's Cut Job tab, connect your cutter and choose a material. "
    "Use Dial In a Material to try a scrap first.\n\n"
    "3. Cutting a print? Check the PDF on the Sheet tab, then choose Test Registration "
    "at the bottom of Cut Job."
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
        "Check that the printed front and cut file are a matched pair. On the Sheet tab, choose "
        "Measure Registration Marks. Print at Actual size (100%). Load the sheet face-up with its "
        "paper edges aligned to the preview.\n\n"
        "Keep the marks and sensor clear. If the sensor is mistaking nearby dark artwork for a mark, "
        "mask the artwork with a light sticky note and leave the mark visible. Try Test Registration "
        "with no cut paths. Remove any mask before sending the cut. Try diffuse light or close the lid. "
        + TRACK_OFF, OPEN_SHEET),
    "Registration fails on foil or glossy stock": (
        "Try diffuse light. If glare persists, place clear matte tape flat over the marks, keeping the "
        "black shapes visible. Or put opaque tape where the marks go and print the marks over it; test "
        "that on scrap before printing a new sheet. Run Test Registration with the sheet loaded.", OPEN_SHEET),
    "Cut is too shallow or still attached": (
        "Check that the blade is seated and the material settings match your stock. In Dial In a Material, "
        "make one pass and lift a corner with the mat still loaded. Repeat on the same spot if the cut is "
        "close. If it barely scores the stock, try slightly more force or another pass at a fresh spot. "
        "Cut templates carry no cut settings, so check a material is chosen. If it stays inconsistent, "
        "try a new blade, a new mat, or both.", DIAL_IN),
    "Cut edges are rough or laminate ripples": (
        "Use less force first and test each change separately on a fresh spot. Heavy pressure can drag "
        "and tear the laminate; other cutters report that speed matters much less than force, and "
        "advise against maxing it. If the cleaner cut still holds, repeat a pass at that spot. "
        "Check the blade for wear if the edge stays rough.", DIAL_IN),
    "Printed coating chips or tears at the edge": (
        "The printed coating on core and foil stock is weaker than the core, so a full-force cut chips it. "
        "Cut light first: several light passes get through the coating gently, then fewer full passes "
        "finish the core, where the edge does not show. For a job, turn on Lighter first passes under "
        "Passes. In Dial In a Material, cut with a light Cut Settings A, then switch to a full Cut "
        "Settings B on the same spot. Other cutters report "
        "4 light passes at force 10 then 3 at force 25 on core stock.\n\n"
        "Foil is thicker and more delicate, so use more light passes at lower force. One user runs 8 at "
        "force 8 then 4 at force 22; Cutline allows 10 passes in all, so scale it, for example 7 then 3. "
        "Try it in Dial In a Material before a full sheet.", DIAL_IN),
    "Fibers left on the back of the card": (
        "The coating is cut but the bottom of the core is still holding. Add one more full pass, or a "
        "little more force on the full passes only; leave the light passes alone. Fibers that remain trim "
        "off with a hobby knife. If raised edges persist whatever the settings, try a fresh or different "
        "blade: replacement blades vary and dull quickly.", DIAL_IN),
    "Carriage skips during a cut": (
        "A blade that takes too much in one pass can make the carriage skip. Once motion stops, "
        "power-cycle the cutter so it can rehome. On a fresh spot, reduce force first or try a lower speed. "
        "If the blade catches in the mat, reduce blade exposure. Use the minimum depth that works. "
        "If the sample tracks cleanly and stays attached, add a pass.", DIAL_IN),
    "Cuts are angled or the mat slips": (
        "Stop the job and check that both pinch rollers grip the mat edges. Align the mat to the cutter’s "
        "loading guide and make sure the stock cannot shift. Cover cards next to the corner marks with a "
        "sticky note so the scan cannot mistake them for marks. " + TRACK_OFF + " If the carriage skipped, "
        "power-cycle after motion stops so it can rehome.", OPEN_SHEET),
    "Cut is in the wrong place": (
        "Open the cut file paired with this PDF and compare the Overlay preview. Silhouette Card Maker "
        "sheets are labelled with their cut template, such as letter-bridge-v1; use that one. Check the "
        "sheet edge and feed arrow, then run Test Registration. " + TRACK_OFF + " If the overlay aligns "
        "and a pen test shows the same offset "
        "across the sheet, use Send → Advanced Options → Calibration to shift in the opposite direction.",
        OPEN_SHEET),
    "Front and back are offset": (
        "If the backs drift against the fronts, check the printer’s flip setting first: stock Silhouette "
        "Card Maker layouts use long-edge flip. To measure and correct the back’s offset, follow Silhouette "
        "Card Maker’s alignment guide: https://alan-cha.github.io/silhouette-card-maker/docs/offset/\n\n"
        "If both sides shift against the cut, compare registration and template placement instead.",
        OPEN_SHEET),
    "Cards print or cut at the wrong size": (
        "Print at Actual size (100%) and match the selected paper size. Measure a printed edge or the "
        "spacing between marks in both directions. If the printer changes the scale at 100%, correct its "
        "scale before adjusting cutter offsets.", OPEN_SHEET),
    "Laminate lifts or the sheet curls": (
        "Check the pouch, stock and laminator settings on an offcut. If the layers lift, run the sheet "
        "through again or at slightly more heat within the pouch’s instructions, and handle it gently while "
        "cutting. Laser toner can make bonding less consistent. If the sheet curls, try less heat and cool "
        "it flat under a heavy book.", None),
    "Rollers shift, skew or mark the sheet": (
        "Track Enhancing feeds the mat in and out under the rollers before the scan or cut, so they grip "
        "better. Some stock does better without it: the sheet can shift against its marks, the mat can "
        "skew, or the rollers can mark a printed or laminated face. Turn off Track Enhancing in Cut "
        "Settings and try again. Keep it on if the mat slips without it. Some Studio presets turn it on, "
        "so check it again after choosing a material.", CUT_SETTINGS),
}
REGISTRATION_PROBLEM = next(iter(CUT_PROBLEMS))
