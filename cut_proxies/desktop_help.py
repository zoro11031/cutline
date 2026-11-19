"""Help shared by the native desktop frontends."""

BLADE_VALUE_TEXT = {"depth": "Manual", "acceleration": "Auto"}

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
    "1. Draw in Design, open cut artwork in Layout, or open a ProxyPress ZIP in Sheet.\n\n"
    "2. In Send, connect your cutter and choose a material. Use Dial In a Material to try a scrap.\n\n"
    "3. Cutting a print? Check the PDF in Sheet and test the printed marks in Send."
)

# Adapted for Control Center from the Silhouette Card Maker cutting guide
# and troubleshooting pages. Actions open the relevant controls.
CUT_PROBLEMS = {
    "Registration marks won’t scan": (
        "Check that the printed front and cut file are a matched pair. In Sheet, measure the PDF. "
        "Print at Actual size (100%). Load the sheet face-up with its paper edges aligned to the preview.\n\n"
        "Keep the marks and sensor clear. If the sensor is mistaking nearby dark artwork for a mark, "
        "mask the artwork with a light sticky note and leave the mark visible. Try Test Registration "
        "with no cut paths. Remove any mask before sending the cut. Try diffuse light or close the lid.",
        "Open Sheet"),
    "Registration fails on foil or glossy stock": (
        "Try diffuse light. If glare persists, place clear matte tape flat over the marks, keeping the "
        "black shapes visible. You can also test matte patches on scrap before printing a new sheet. "
        "Run Test Registration with the sheet loaded.", "Open Sheet"),
    "Cut is too shallow or still attached": (
        "Check that the blade is seated and the material settings match your stock. In Dial In a Material, "
        "make one pass and lift a corner with the mat still loaded. Repeat on the same spot if the cut is "
        "close. If it barely scores the stock, try slightly more force at a fresh spot. Check the blade "
        "and mat for wear if the result stays inconsistent.", "Dial In a Material"),
    "Cut edges are rough or laminate ripples": (
        "Use less force and try a faster speed. Test each change separately on a fresh spot. "
        "Heavy pressure at low speed can drag and tear the laminate. If the cleaner cut still holds, "
        "repeat a pass at that spot. Check the blade for wear if the edge stays rough.", "Dial In a Material"),
    "Carriage skips during a cut": (
        "A blade that takes too much in one pass can make the carriage skip. Once motion stops, "
        "power-cycle the cutter so it can rehome. On a fresh spot, reduce force first or try a lower speed. "
        "If the blade catches in the mat, reduce blade exposure. Use the minimum depth that works. "
        "If the sample tracks cleanly and stays attached, add a pass.", "Dial In a Material"),
    "Cuts are angled or the mat slips": (
        "Stop the job and check that both pinch rollers grip the mat edges. Align the mat to the cutter’s "
        "loading guide and make sure the stock cannot shift. Check the printed marks for competing artwork. "
        "If the carriage skipped, power-cycle after motion stops so it can rehome.", "Open Sheet"),
    "Cut is in the wrong place": (
        "Open the cut file paired with this PDF and compare the Overlay preview. Check the sheet edge and "
        "feed arrow, then run Test Registration. If the overlay aligns and a pen test shows the same offset "
        "across the sheet, use cutter calibration to shift in the opposite direction.", "Open Sheet"),
    "Front and back are offset": (
        "If only one side drifts, check the printer’s duplex alignment and flip setting first. "
        "Stock Silhouette Card Maker layouts use long-edge flip. If both sides shift against the cut, "
        "compare registration and template placement before adjusting the printer.", "Open Sheet"),
    "Cards print or cut at the wrong size": (
        "Print at Actual size (100%) and match the selected paper size. Measure a printed edge or the "
        "spacing between marks in both directions. If the printer changes the scale at 100%, correct its "
        "scale before adjusting cutter offsets.", "Open Sheet"),
    "Laminate lifts or the sheet curls": (
        "Check the pouch, stock and laminator settings on an offcut. If the layers lift, try slightly more "
        "heat within the pouch’s instructions. If the sheet curls, try less heat and cool it flat. "
        "Laser toner can make bonding less consistent.", None),
}
