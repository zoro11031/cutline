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
    "1. Draw in Design, open a cut template in Layout, or open a ProxyPress ZIP in Sheet.\n\n"
    "2. In Send, connect your cutter and choose a material. Use Dial In a Material to try a scrap.\n\n"
    "3. Cutting a print? Check the PDF in Sheet and test the printed marks in Send."
)
