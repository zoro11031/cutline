"""Does each guard's test class actually discriminate, or only refuse?

A guard tested solely on the failure case is still unproven: a function
hardwired to "always refuse" passes every such test. The mirror is just as
bad — a class full of negative cases is passed by a function that can only
say "fine". The mutation cases below check both kinds of failure.

So for each guard, hardwire it **both** ways and require its test class to
notice each time. A check that can only answer one way then fails at least
one direction, and a check wired to a constant fails both.

This runs the other test classes in-process, which is unusual, but the
alternative is a one-off script run once by hand — and the whole argument
of `docs/FINDINGS.md` section 18 is that a proving run which does not
happen alongside the thing it certifies is not evidence about it. It is
cheap: nine sub-suites of a few tests each.
"""

import io
import unittest

from cut_proxies import corner_test, cutters, graphtec, job, regmarks, sheet, starting_guide, sticker_print, testcut, transport, verify


def _run(class_paths):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        [loader.loadTestsFromName(p) for p in class_paths])
    return unittest.TextTestRunner(stream=io.StringIO()).run(suite)


# (module, attribute, stub, description, the classes that must object)
def _force_verification_row(label, allowed):
    original = verify.expectations_met

    def force(*args, **kwargs):
        _, rows = original(*args, **kwargs)
        for row in rows:
            if row.label == label:
                row.ok = allowed
        return all(row.ok for row in rows), rows

    return force


def refuse_depth(codes):
    raise transport.TransportError("refused")


def refuse_sticker_ink(*args):
    raise ValueError("registration marks")


CASES = (
    (sticker_print, "check_print_area", lambda *a: None, "sticker print guard always allows",
     ["tests.test_sticker_print.TestStickerPrint.test_export_refuses_to_overwrite_archive_if_print_covers_a_mark"]),
    (sticker_print, "check_print_area", refuse_sticker_ink, "sticker print guard always refuses",
     ["tests.test_sticker_print.TestStickerPrint.test_export_refuses_to_overwrite_archive_if_print_covers_a_mark"]),
    (cutters, "usb_refusal", lambda *a: "", "every endpoint accepted",
     ["tests.test_cutter_detection.TestExactEndpoint.test_refusal_changes_with_endpoint_and_model"]),
    (cutters, "usb_refusal", lambda *a: "refused", "every endpoint refused",
     ["tests.test_cutter_detection.TestExactEndpoint.test_refusal_changes_with_endpoint_and_model"]),

    (graphtec.Device, "supports_holder", lambda *a: True, "every holder accepted",
     ["tests.test_guards.TestInstalledTools"]),
    (graphtec.Device, "supports_holder", lambda *a: False, "every holder refused",
     ["tests.test_guards.TestInstalledTools"]),
    (transport, "check_autoblade", lambda codes: None, "depth always allowed",
     ["tests.test_guards.TestInstalledTools"]),
    (transport, "check_autoblade", refuse_depth, "depth always refused",
     ["tests.test_guards.TestInstalledTools"]),
    (job, "inkscape_registration_errors", lambda *a: [],
     "Inkscape registration always allows",
     ["tests.test_inkscape_import.TestInkscapeImport"]),
    (job, "inkscape_registration_errors", lambda *a: ["blocked"],
     "Inkscape registration always refuses",
     ["tests.test_inkscape_import.TestInkscapeImport"]),
    (job, "imported_page_errors", lambda *a: [],
     "SVG page guard always allows", ["tests.test_imported_page.ImportedPage"]),
    (job, "imported_page_errors", lambda *a: ["SVG page mismatch"],
     "SVG page guard always refuses", ["tests.test_imported_page.ImportedPage"]),
    (job, "imported_mode_errors", lambda *a: [],
     "Imported mode guard always allows", ["tests.test_imported_page.ImportedPage"]),
    (job, "imported_mode_errors", lambda *a: ["cross mode unsupported"],
     "Imported mode guard always refuses", ["tests.test_imported_page.ImportedPage"]),
    (regmarks, "off_sheet", lambda *a, **k: [],
     "off_sheet never reports anything off the sheet",
     ["tests.test_regmarks.TestClearance"]),
    (regmarks, "off_sheet", lambda *a, **k: ["x"],
     "off_sheet reports everything off the sheet",
     ["tests.test_regmarks.TestClearance"]),
    (regmarks, "collisions", lambda *a, **k: [],
     "collisions never reports a collision",
     ["tests.test_regmarks.TestClearance"]),
    (regmarks, "collisions", lambda *a, **k: ["x"],
     "collisions reports a collision always",
     ["tests.test_regmarks.TestClearance"]),
    (regmarks, "mask_hints", lambda *a, **k: [],
     "mask_hints never warns about a crowded card",
     ["tests.test_guards.TestMaskHintsFire"]),
)

CASES += tuple(
    (verify, "expectations_met", _force_verification_row(label, allowed),
     f"{label} always {'allows' if allowed else 'refuses'}",
     ["tests.test_imported_paths.TestImportedPaths"])
    for label in ("imported path geometry", "cuts inside the sheet")
    for allowed in (True, False)
)

CASES += tuple(
    (verify, "expectations_met", _force_verification_row(label, allowed),
     f"{label} always {'allows' if allowed else 'refuses'}",
     ["tests.test_templates.TestVerificationRefuses.test_cameo_4_sets_the_standard_mat_before_searching_for_marks"])
    for label in ("registration mat setup", "registration mark dimensions")
    for allowed in (True, False)
)

CASES += tuple(
    (verify, "expectations_met", _force_verification_row(label, allowed),
     f"{label} always {'allows' if allowed else 'refuses'}",
     [f"tests.test_send.TestRegistrationOnly.{test}"])
    for label, test in (
        ("scan command", "test_manual_registration_is_checked_and_waits_for_the_scan_result"),
        ("scan search start", "test_search_start_is_verified_against_the_captured_letter_window"),
    )
    for allowed in (True, False)
)

CASES += tuple(
    (verify, "expectations_met", _force_verification_row(label, allowed),
     f"{label} always {'allows' if allowed else 'refuses'}", [test])
    for label, test in (
        ("scheduled path geometry", "tests.test_toolpath.TestToolpathSchedule.test_schedule_is_validated_and_geometric_changes_are_detected"),
        ("cutting mat setup", "tests.test_send.TestMediaHandling.test_matless_blank_sheet_explicitly_sets_the_device_boundary"),
        ("return to start", "tests.test_send.TestMediaHandling.test_return_to_start_verifies_the_correct_trailer_for_each_dialect"),
    )
    for allowed in (True, False)
)

CASES += tuple(
    (verify, "expectations_met", _force_verification_row(label, allowed),
     f"{label} always {'allows' if allowed else 'refuses'}",
     [f"tests.test_send.TestStudioMediaControls.{test}"])
    for label, test in (
        ("active cutter settings", "test_active_cutter_settings_detect_corrupted_wire_values"),
        ("line segment overcut", "test_line_segment_overcut_uses_holder_and_disables_on_pen"),
        ("track enhancing", "test_track_enhancing_has_one_waited_roller_step_before_registration"),
        ("feed position", "test_feed_distance_is_added_to_the_farthest_cut_and_verified"),
    )
    for allowed in (True, False)
)


CASES += tuple(
    (verify, "expectations_met", _force_verification_row("group blade settings", allowed),
     f"group blade settings always {'allow' if allowed else 'refuse'}",
     ["tests.test_actions.TestGroupedSend"])
    for allowed in (True, False)
)


def _refuse_testcut(*args):
    raise ValueError("refused")


def _ignore_corner_obstacles(source, occupied, find=corner_test._find_spot):
    return find(source, ())


def _ignore_bundle_page_count(*args, expected_pages=None, measure=sheet.measure, **kwargs):
    return measure(*args, **kwargs)


def _reject_bundle_page_count(*args, **kwargs):
    raise sheet.SheetError("The PDF page count does not match the bundle.")


CASES += (
    (sheet, "measure", _ignore_bundle_page_count, "every bundle PDF page count accepted",
     ["tests.test_proxypress_bundle.BundleRules.test_pdf_page_count_must_match_the_full_bundle_mapping"]),
    (sheet, "measure", _reject_bundle_page_count, "every bundle PDF page count refused",
     ["tests.test_proxypress_bundle.BundleRules.test_pdf_page_count_must_match_the_full_bundle_mapping"]),
    (job, "cutting_mat_errors", lambda *a: [], "every explicit cutting mat accepted",
     ["tests.test_send.TestMediaHandling.test_compatible_portrait_mat_allows_matching_sheet_and_refuses_wrong_size"]),
    (job, "cutting_mat_errors", lambda *a: ["wrong mat"], "every explicit cutting mat refused",
     ["tests.test_send.TestMediaHandling.test_compatible_portrait_mat_allows_matching_sheet_and_refuses_wrong_size"]),
    (testcut, "_check_geometry", lambda *a: None, "every test cut path accepted",
     ["tests.test_testcut.TestStudioPattern"]),
    (testcut, "_check_geometry", _refuse_testcut, "every test cut path refused",
     ["tests.test_testcut.TestStudioPattern"]),
    (corner_test, "_find_spot", _ignore_corner_obstacles, "corner placement ignores obstacles",
     ["tests.test_corner_cut.TestCornerTest"]),
    (corner_test, "_find_spot", _refuse_testcut, "every corner placement is refused",
     ["tests.test_corner_cut.TestCornerTest"]),
    (starting_guide.Guide, "refusal", lambda *a: "", "every guide repeat accepted",
     ["tests.test_starting_guide.TestStartingGuide"]),
    (starting_guide.Guide, "refusal", lambda *a: "blocked", "every guide repeat refused",
     ["tests.test_starting_guide.TestStartingGuide"]),
    (starting_guide.Guide, "can_cut", property(lambda *_: True), "guided repeats always allowed",
     ["tests.test_starting_guide.TestStartingGuide"]),
    (starting_guide.Guide, "can_cut", property(lambda *_: False), "guided repeats always refused",
     ["tests.test_starting_guide.TestStartingGuide"]),
    (starting_guide.Guide, "archived_spot", property(lambda *_: None), "legacy test spots always repeatable",
     ["tests.test_starting_guide.TestStartingGuide.test_legacy_archived_spot_keeps_count_and_refuses_repeat"]),
    (starting_guide.Guide, "archived_spot", property(lambda guide: guide.history[0] if guide.history else None),
     "unused test spots treated as legacy archives",
     ["tests.test_starting_guide.TestStartingGuide.test_legacy_archived_spot_keeps_count_and_refuses_repeat"]),
    (starting_guide, "has_cutter_endpoint", lambda *_: True, "every material test cutter accepted",
     ["tests.test_starting_guide.TestStartingGuide.test_sending_requires_a_selected_cutter_endpoint"]),
    (starting_guide, "has_cutter_endpoint", lambda *_: False, "every material test cutter refused",
     ["tests.test_starting_guide.TestStartingGuide.test_sending_requires_a_selected_cutter_endpoint"]),
)

try:
    from cut_proxies import gui
except ImportError:
    gui = None
if gui is not None:
    CASES += (
        (gui, 'cut_refusal', lambda *a: '', 'GUI confirmation always allows',
         ['tests.test_gui.TestCutConfirmationGuard']),
        (gui, 'cut_refusal', lambda *a: 'blocked', 'GUI confirmation always refuses',
         ['tests.test_gui.TestCutConfirmationGuard']),
        (gui, 'sheet_request_current', lambda *a: True, 'PDF result guard always accepts',
         ['tests.test_gui.TestSheetRequestGuard']),
        (gui, 'sheet_request_current', lambda *a: False, 'PDF result guard always rejects',
         ['tests.test_gui.TestSheetRequestGuard']),
    )


class TestGuardsDiscriminate(unittest.TestCase):

    def test_each_hardwired_guard_is_noticed(self):
        for module, attr, stub, description, classes in CASES:
            with self.subTest(guard=description):
                real = getattr(module, attr)
                setattr(module, attr, stub)
                try:
                    result = _run(classes)
                finally:
                    setattr(module, attr, real)
                broken = len(result.failures) + len(result.errors)
                self.assertGreater(
                    result.testsRun, 0,
                    f"no tests ran for {description}; the class path is "
                    f"probably wrong, which would make this vacuous")
                self.assertGreater(
                    broken, 0,
                    f"{description} - and every test still passed, so that "
                    f"guard's tests do not discriminate")

    def test_the_guards_are_restored_afterwards(self):
        # A discrimination run that leaked a stub would quietly disable a
        # guard for every test after it.
        self.assertFalse(
            getattr(regmarks.collisions, "__name__", "") == "<lambda>")
        self.assertEqual(regmarks.collisions.__name__, "collisions")

    def test_an_unguarded_case_would_be_reported(self):
        # Prove this file can fail: a guard nothing asserts on.
        real = regmarks.clamp
        regmarks.clamp = lambda i, l, t: (i, l, t)
        try:
            result = _run(["tests.test_guards.TestOpenTransportFires"])
        finally:
            regmarks.clamp = real
        # Port tests know nothing about clamp, so they stay green - which is
        # exactly the shape of "tested only one way" this file exists to
        # catch, and why each case above must name the right class.
        self.assertEqual(len(result.failures) + len(result.errors), 0)


if __name__ == "__main__":
    unittest.main()
