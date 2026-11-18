"""The actual transport plan must be the stream that was verified."""
from pathlib import Path
import types
import unittest
from unittest import mock

from cut_proxies import cli, graphtec, job, verify

try:
    from cut_proxies import gui
except ImportError:
    gui = None


class TestRegistrationOnly(unittest.TestCase):
    def test_four_card_scan_is_the_two_captured_studio_writes(self):
        capture = Path(__file__).resolve().parents[1] / "docs/cameo4-studio-registration-2026-11-17.tsv"
        packets = [line.split() for line in capture.read_text().splitlines()[1:]]
        writes = [bytes.fromhex(data) for _, endpoint, data in packets
                  if endpoint == "0x01" and bytes.fromhex(data).startswith((b"TG1", b"TB52"))]
        self.assertEqual(len(writes), 2)
        built = graphtec.CutJob(graphtec.device_by_name("Silhouette Cameo 4"),
                                graphtec.CutSettings(), origin_mm=(10, 10),
                                reg_width_mm=259.4, reg_length_mm=195.9,
                                mark_shape_mm=(8.0264, 1))
        for steps in (built.plan([]), built.plan_matrix([(built.settings, [])])):
            self.assertEqual([s.data for s in steps[:2]], writes)
            self.assertEqual(steps[2].expect, graphtec.RESP_REGMARK_OK)
            self.assertEqual(steps[3].data, graphtec.frame((r"\0,0", "Z3918,5188")))

    def test_registration_only_has_the_same_scan_and_no_cut_paths(self):
        checked = job.build(offset_mm=(0, 0))
        steps = checked.registration_steps()
        self.assertFalse(checked.blocked)
        self.assertEqual(steps, checked.steps[:len(steps)])
        self.assertEqual(steps[-1].expect, graphtec.RESP_REGMARK_OK)
        self.assertFalse(any(step.note == "cut paths" for step in steps))
        self.assertEqual(job.build(registration=False).registration_steps(), ())

    def test_manual_registration_is_checked_and_waits_for_the_scan_result(self):
        checked = job.build(auto_search=False, offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertIn(b"TB23,3918,5188\x03", checked.stream)
        self.assertEqual(checked.registration_steps()[-1].expect, graphtec.RESP_REGMARK_OK)
        self.assertNotIn(b"TB123,", writes(checked.registration_steps()))
        self.assertNotIn(b"TB124,", writes(checked.registration_steps()))
        broken = checked.stream.replace(b"TB23,", b"TB123,")
        ok, rows = verify.expectations_met(
            broken, checked.grid, checked.marks, checked.settings, checked.device,
            auto_search=False)
        self.assertFalse(ok)
        self.assertIn("scan command", {row.label for row in rows if not row.ok})

    def test_search_start_is_verified_against_the_captured_letter_window(self):
        checked = job.build(offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertIn(b"TB123,3918,5188,118,118\x03", checked.stream)
        broken = checked.stream.replace(b"TB123,3918,5188,118,118", b"TB123,3918,5188,118,218")
        ok, rows = verify.expectations_met(
            broken, checked.grid, checked.marks, checked.settings, checked.device)
        self.assertFalse(ok)
        self.assertIn("scan search start", {row.label for row in rows if not row.ok})

    def test_legacy_force_and_full_sheet_window_work_without_registration(self):
        checked = job.build(cutter="Silhouette Cameo 2", registration=False,
                            offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertIn(graphtec.frame("FX33"), checked.stream)
        self.assertNotIn(graphtec.frame("FX33,1"), checked.stream)
        self.assertIn(graphtec.frame(("FN0", "TB50,0")), checked.stream)
        self.assertIn(graphtec.frame("Z4318,5588"), checked.stream)
        self.assertIsNone(checked.settings.depth)

    def test_report_prints_the_exact_captured_registration_writes(self):
        checked = job.build(sheet="Letter", portrait=False, mark_inset_mm=10,
                            mark_length_mm=8.0264, mark_thickness_mm=1)
        self.assertEqual(checked.registration_writes(), (
            "TG1 FN0 TB50,0 TB99",
            "TB52,2 TB51,161 TB53,20 TB55,1 TB123,3918,5188,118,118 TB99",
        ))
        self.assertEqual(job.build(registration=False).registration_writes(), ())



class TestMediaHandling(unittest.TestCase):
    def test_requested_long_mat_replaces_the_default_letter_sequence(self):
        chosen = job.build(mat="cameo-12x24", offset_mm=(0, 0))
        self.assertEqual(chosen.blocked, [])
        self.assertIn(graphtec.frame(("TG2", "FN0", "TB50,0", r"\0,0", "Z12192,6096")), chosen.stream)
        self.assertEqual(verify.decode(chosen.stream).scan_args_mm[-2:], (0, 0))
        short = job.build(mat="cameo-12x12", offset_mm=(0, 0))
        self.assertEqual(short.blocked, [])
        self.assertEqual(short.registration_writes(), job.build(offset_mm=(0, 0)).registration_writes())

    def test_matless_blank_sheet_explicitly_sets_the_device_boundary(self):
        checked = job.build(mat="none", registration=False, offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertIn(graphtec.frame(("TG0", "FN0", "TB50,0", r"\0,0", "Z60000,6096")), checked.stream)
        broken = checked.stream.replace(b"TG0", b"TG1")
        ok, rows = verify.expectations_met(broken, checked.grid, checked.marks,
                                           checked.settings, checked.device,
                                           registration=False, mat="none")
        self.assertFalse(ok)
        self.assertIn("cutting mat setup", {row.label for row in rows if not row.ok})

    def test_compatible_portrait_mat_allows_matching_sheet_and_refuses_wrong_size(self):
        checked = job.build(cutter="Silhouette Portrait 4", mat="portrait-8.5x12",
                            portrait=True, registration=False, offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertIn(graphtec.frame("TG11"), checked.stream)
        refused = job.build(mat="portrait-8.5x12", portrait=False, offset_mm=(0, 0))
        self.assertTrue(any("does not fit" in message for message in refused.blocked))
        legacy = job.build(cutter="Silhouette Cameo 2", mat="cameo-12x12", offset_mm=(0, 0))
        self.assertTrue(any("does not support software mat" in message for message in legacy.blocked))

    def test_return_to_start_verifies_the_correct_trailer_for_each_dialect(self):
        for cutter, trailer in (("Silhouette Cameo 4", ("L0", r"\0,0", "M0,0", "J0", "FN0", "TB50,0")),
                                ("Silhouette Cameo 2", ("H",))):
            with self.subTest(cutter=cutter):
                checked = job.build(cutter=cutter, end_position="start", offset_mm=(0, 0))
                self.assertEqual(checked.blocked, [])
                self.assertTrue(checked.stream.endswith(graphtec.frame(trailer)))
                broken = verify._corrupt_return_to_start(checked.stream)
                ok, rows = verify.expectations_met(
                    broken, checked.grid, checked.marks, checked.settings, checked.device,
                    end_position="start")
                self.assertFalse(ok)
                self.assertIn("return to start", {row.label for row in rows if not row.ok})


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def writes(steps):
    return b''.join(s.data for s in steps if s.kind == 'write')


class TestCliCheckedPlan(unittest.TestCase):
    def test_transport_plan_preserves_calibration(self):
        j = job.build(offset_mm=(0.35, -0.2))
        self.assertFalse(j.blocked)
        self.assertEqual(writes(cli._plan(j)), j.stream)

    def test_transport_plan_preserves_registration_off(self):
        j = job.build(registration=False, offset_mm=(0, 0))
        self.assertFalse(j.blocked)
        self.assertEqual(writes(cli._plan(j)), j.stream)


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestGuiCheckedPlan(unittest.TestCase):
    def send(self, checked, scan_only=False):
        window = types.SimpleNamespace(job=checked, _sending=False, _checking_status=False, pdf_bundle=None,
            cut_button=mock.Mock(), scan_button=mock.Mock(), scan_result=mock.Mock(),
            r_auto=mock.Mock(), toast=mock.Mock(),
            rebuild=mock.Mock(), _send_done=mock.Mock())
        window.r_auto.get_active.return_value = True
        transport = mock.MagicMock()
        transport.__enter__.return_value = transport
        transport.status.return_value = 'ready'
        with mock.patch.object(gui.threading, 'Thread', ImmediateThread), \
             mock.patch.object(gui.transport, 'open_transport', return_value=transport), \
             mock.patch.object(gui.GLib, 'idle_add'):
            gui.Window._cut_response(window, None, 'cut', checked, scan_only=scan_only)
        return transport

    def test_actual_send_preserves_calibration(self):
        j = job.build(offset_mm=(0.35, -0.2))
        transport = self.send(j)
        self.assertEqual(writes(transport.run.call_args.args[0]), j.stream)

    def test_actual_send_preserves_registration_off(self):
        j = job.build(registration=False, offset_mm=(0, 0))
        transport = self.send(j)
        self.assertEqual(writes(transport.run.call_args.args[0]), j.stream)

    def test_test_scan_runs_only_the_verified_registration_prefix(self):
        j = job.build(offset_mm=(0, 0))
        transport = self.send(j, scan_only=True)
        self.assertEqual(transport.run.call_args.args[0], j.registration_steps())
        self.assertFalse(any(step.note == "cut paths" for step in transport.run.call_args.args[0]))

    def test_response_rechecks_a_blocked_job(self):
        j = job.build(cutter='Silhouette Portrait', offset_mm=(0, 0))
        self.assertTrue(j.blocked)
        transport = self.send(j)
        transport.run.assert_not_called()
