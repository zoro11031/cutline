"""Fire every guard that exists to catch a failure.

A detector that has never fired is not a detector, it is an assumption. A
clean result from a broken check is indistinguishable from a clean run, and
it reads as a pass — which is the worst possible way for a safety check to
be wrong.

Everything here deliberately *causes* the condition and asserts the guard
notices. These guards in particular had no coverage at all when this file
was written: they were added in response to real failures, verified once by
hand, and then never exercised again.

These tests use fake transports to exercise failure paths without USB.
"""

import unittest
from unittest import mock

from cutline import (geometry, graphtec, job as job_mod, regmarks,
                         template, transport)
from cutline.graphtec import CutSettings, Step


# --------------------------------------------------------------------------
# mask hints
# --------------------------------------------------------------------------

class TestMaskHintsFire(unittest.TestCase):
    """The crowding detector, which drives a real operator instruction."""

    def test_it_fires_on_the_layouts_that_are_known_to_crowd(self):
        # Letter and A4 at a full sheet: three cards each, ~1.2 mm clear.
        for sheet, portrait, inset in (("Letter", False, 10.0),
                                       ("Letter", True, 3.5),
                                       ("A4", False, 10.0)):
            with self.subTest(sheet=sheet, portrait=portrait):
                grid = geometry.scm_layout(sheet, "standard", portrait, inset)
                mk = regmarks.for_grid(grid, inset_mm=inset)
                hints = regmarks.mask_hints(grid, mk)
                self.assertEqual(len(hints), 3)
                self.assertLess(hints[0]["gap_mm"], 2.0)

    def test_it_stays_silent_on_the_layout_that_does_not_crowd(self):
        grid = geometry.scm_layout("Legal", "standard", True, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.mask_hints(grid, mk), [])

    def test_a_hint_names_the_card_the_mark_and_the_gap(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        h = regmarks.mask_hints(grid, mk)[0]
        self.assertEqual(set(h), {"index", "row", "col", "mark", "gap_mm",
                                  "text"})
        self.assertIn(h["mark"], ("top-left", "top-right", "bottom-left",
                                  "bottom-right"))
        self.assertIn("mm", h["text"])
        self.assertNotIn("cover", h["text"])

    def test_hints_come_back_closest_first(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        gaps = [h["gap_mm"] for h in regmarks.mask_hints(grid, mk)]
        self.assertEqual(gaps, sorted(gaps))

    def test_skipping_a_card_silences_its_hint(self):
        job = job_mod.build(sheet="Letter", portrait=False,
                            preset_name="Plain paper test")
        self.assertTrue(job.mask_hints)
        crowded = tuple(h["index"] for h in job.mask_hints)
        quiet = job_mod.build(sheet="Letter", portrait=False,
                              preset_name="Plain paper test", skip=crowded)
        self.assertEqual(quiet.mask_hints, [])

    def test_a_wider_clearance_catches_more(self):
        grid = geometry.scm_layout("Legal", "standard", True, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.mask_hints(grid, mk), [])
        self.assertTrue(regmarks.mask_hints(grid, mk, clearance_mm=60.0))


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------

class FakeTransport(transport.Transport):
    """A transport that answers however the test needs it to."""

    name = "fake"
    can_read = True

    def __init__(self, replies=()):
        self.written = []
        self.replies = list(replies)

    def write(self, data):
        self.written.append(data)
        return len(data)

    def read(self, size=64, timeout_ms=10000):
        if not self.replies:
            raise transport.TransportError("nothing left to say")
        return self.replies.pop(0)


class TestInstalledTools(unittest.TestCase):
    def cutter(self, *replies):
        cutter = FakeTransport(replies)
        cutter.device = graphtec.device_by_name("Silhouette Cameo 4")
        return cutter

    def test_requires_detected_autoblade_before_any_depth_job_motion(self):
        plan = [Step("write", b"M0,0\x03"), Step("write", b"FX1,1\x03TF0,1\x03")]
        for reply in (b" 0, 0\x03", b" 4, 2\x03", b"255,0\x03", b"29,0\x03",
                      b"2,0", b"2,x\x03", b"2\x03", b"2,256\x03"):
            with self.subTest(reply=reply):
                cutter = self.cutter(reply)
                with self.assertRaises(transport.TransportError):
                    cutter.run(plan)
                self.assertEqual(cutter.written, [b"\x1b\x15"])
        cutter = self.cutter(b" 2, 0\x03")
        cutter.run(plan)
        self.assertEqual(cutter.written, [b"\x1b\x15"] + [step.data for step in plan])

    def test_manual_depth_and_scan_only_use_original_writes(self):
        cutter = self.cutter()
        for data in (b"FX20,1\x03M0,0\x03", b"TB123,3918,5188,118,118\x03"):
            cutter.run([Step("write", data)])
        self.assertEqual(len(cutter.written), 2)
        self.assertNotIn(b"\x1b\x15", cutter.written)

    def test_status_preserves_unknown_ids_and_labels_each_holder(self):
        for raw, labels in ((b" 2, 0\x03", ("AutoBlade", "Empty")),
                            (b" 3, 4\x03", ("Deep-cut blade", "Kraft blade")),
                            (b"77,255\x03", ("Unknown tool (ID 77)", "Detection error"))):
            cutter = self.cutter(b"2\x03", raw)
            self.assertEqual(cutter.status_with_tools(),
                             f"unloaded · Holder 1: {labels[0]} · Holder 2: {labels[1]}")
            self.assertEqual(cutter.written, [b"\x1b\x05", b"\x1b\x15"])
        old = self.cutter(b"0\x03")
        old.device = graphtec.device_by_name("Silhouette Cameo 3")
        self.assertIn("unavailable", old.status_with_tools())
        self.assertEqual(old.written, [b"\x1b\x05"])

    def test_status_and_tools_return_the_same_reply_without_polling_a_moving_cutter(self):
        cutter = self.cutter(b"0\x03", b"2,0\x03")
        self.assertEqual(cutter.status_and_tools(), ("ready · Holder 1: AutoBlade · Holder 2: Empty", (2, 0)))
        self.assertEqual(cutter.written, [b"\x1b\x05", b"\x1b\x15"])
        moving = self.cutter(b"1\x03")
        self.assertEqual(moving.status_and_tools(), ("moving", None))
        self.assertEqual(moving.written, [b"\x1b\x05"])

    def test_only_dual_carriage_models_accept_holder_two(self):
        settings = CutSettings(holder=2)
        self.assertEqual(settings.clamped(self.cutter().device).holder, 2)
        with self.assertRaisesRegex(ValueError, "holder 2"):
            settings.clamped(graphtec.device_by_name("Silhouette Portrait 3"))


class TestTransportRunFires(unittest.TestCase):
    """The expectation check between writing and cutting."""

    def _steps(self):
        return [
            Step("write", b"setup\x03", note="settings"),
            Step("expect", expect=graphtec.RESP_REGMARK_OK, note="marks found"),
            Step("write", b"cut\x03", note="paths"),
        ]

    def test_a_good_reply_lets_the_job_through(self):
        t = FakeTransport([graphtec.RESP_REGMARK_OK])
        sent = t.run(self._steps())
        self.assertEqual(sent, len(b"setup\x03") + len(b"cut\x03"))
        self.assertEqual(len(t.written), 2)

    def test_a_bad_reply_stops_before_the_cut(self):
        # The whole point: marks not found must not be followed by cutting.
        t = FakeTransport([b"    1\x03"])
        with self.assertRaises(transport.RegistrationFailed) as ctx:
            t.run(self._steps())
        self.assertIn("did not report which mark", str(ctx.exception))
        self.assertIn("No cut paths were sent", str(ctx.exception))
        self.assertEqual(len(t.written), 1)

    def test_silence_stops_before_the_cut_too(self):
        t = FakeTransport([])
        with self.assertRaises(transport.TransportError):
            t.run(self._steps())
        self.assertEqual(len(t.written), 1)

    def test_a_write_only_transport_refuses_to_pretend_it_read(self):
        class WriteOnly(transport.Transport):
            name = "writeonly"
            can_read = False

            def write(self, data):
                return len(data)

        with self.assertRaises(transport.TransportError):
            WriteOnly().read()

    def test_status_of_a_write_only_transport_is_unknown_not_ready(self):
        class WriteOnly(transport.Transport):
            name = "writeonly"
            can_read = False

            def write(self, data):
                return len(data)

        self.assertEqual(WriteOnly().status(), "unknown")

    def test_status_decodes_the_machine_states(self):
        for raw, word in ((b"0\x03", "ready"), (b"1\x03", "moving"),
                          (b"2\x03", "unloaded")):
            with self.subTest(state=word):
                self.assertEqual(FakeTransport([raw]).status(), word)

    def test_an_unterminated_status_is_reported_not_guessed(self):
        self.assertIn("unparsed", FakeTransport([b"xyz"]).status())


class TestFileTransportDoesNotFakeSuccess(unittest.TestCase):

    def test_it_records_the_unchecked_expectation_rather_than_passing_it(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "spool.bin"
            logged = []
            with transport.FileTransport(path) as t:
                t.run([Step("write", b"a\x03"),
                       Step("expect", expect=graphtec.RESP_REGMARK_OK,
                            note="marks found")],
                      log=logged.append)
            self.assertTrue(any("not checked" in m for m in logged))
            self.assertEqual(path.read_bytes(), b"a\x03")

    def test_it_never_claims_it_can_read(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            with transport.FileTransport(pathlib.Path(tmp) / "s.bin") as t:
                self.assertFalse(t.can_read)
                with self.assertRaises(transport.TransportError):
                    t.read()


class TestOpenTransportFires(unittest.TestCase):

    def test_an_unknown_preference_is_rejected(self):
        with self.assertRaises(transport.TransportError):
            transport.open_transport("carrier-pigeon")

    def test_the_file_transport_needs_a_path(self):
        with self.assertRaises(transport.TransportError):
            transport.open_transport("file")

    def test_no_cutter_produces_a_message_naming_every_route_tried(self):
        with mock.patch.object(transport, "PyUsbTransport",
                               side_effect=transport.NotFound("no cutter")), \
             mock.patch.object(transport, "find_devices", return_value=[]), \
             self.assertRaises(transport.NotFound) as ctx:
            transport.open_transport("auto")
        said = str(ctx.exception)
        self.assertIn("pyusb", said)
        self.assertIn("usblp", said)


class TestJobRefusalFires(unittest.TestCase):
    """job.blocked has to be non-empty for each thing it guards against."""

    def test_an_over_long_mark_blocks(self):
        j = job_mod.build(preset_name="Plain paper test",
                          mark_length_mm=regmarks.MAX_LENGTH_MM)
        self.assertTrue(j.blocked)

    def test_a_sheet_too_wide_for_the_cutter_blocks(self):
        j = job_mod.build(sheet="Letter", portrait=False,
                          cutter="Silhouette Portrait 2",
                          preset_name="Plain paper test")
        self.assertTrue(j.blocked)

    def test_a_clean_job_is_not_blocked(self):
        j = job_mod.build(sheet="Legal", portrait=True,
                          preset_name="Plain paper test")
        self.assertEqual(j.blocked, [])

    def test_imported_cross_mode_blocks(self):
        from cutline import importer
        art = importer.Imported(
            paths=(template.CutPath(((20, 20), (83, 20), (83, 108), (20, 108)), True),),
            width_mm=63.0, height_mm=88.0, source='test', scale_note='1:1')
        j = job_mod.build(preset_name="Plain paper test", imported=art, mode="cross")
        self.assertTrue(any("mode='cross'" in e for e in j.blocked), j.blocked)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# the verifier proving itself
# --------------------------------------------------------------------------

class TestVerifierLiveness(unittest.TestCase):
    """The check that the checks are running at all.

    `expectations_met` printing an all-clear table is the last thing between
    a wrong stream and the blade. A verifier that always returned ok would
    print exactly the same table, so each job corrupts its own stream four
    ways and confirms the checks reject it, in the same run.
    """

    def setUp(self):
        self.job = job_mod.build(preset_name="Plain paper test")
        from cutline import verify
        self.verify = verify
        self.args = (self.job.grid, self.job.marks, self.job.settings,
                     self.job.device, self.job.mode)
        self.kwargs = dict(cards=self.job.grid.cards,
                           offset_mm=self.job.offset_mm)

    def test_a_healthy_verifier_catches_every_probe(self):
        missed = self.verify.liveness_failures(
            self.job.stream, *self.args, **self.kwargs)
        self.assertEqual(missed, [])

    def test_every_probe_actually_changes_the_stream(self):
        # A probe that silently no-ops would report "caught" forever.
        for label, corrupt in self.verify.PROBES:
            with self.subTest(probe=label):
                broken = corrupt(self.job.stream)
                self.assertIsNotNone(broken, f"{label} produced nothing")
                self.assertNotEqual(broken, self.job.stream)

    def _real_rows(self):
        """The row set a healthy run produces, so a mock can keep its shape.

        A mock returning no rows would look like a run where none of the
        checks exist, and every probe would be skipped as inapplicable
        rather than reported - which is the correct behaviour for an
        unregistered job and the wrong answer here.
        """
        _, rows = self.verify.expectations_met(
            self.job.stream, *self.args, **self.kwargs)
        return rows

    def test_a_blind_verifier_is_detected(self):
        # The case the whole mechanism exists for: every check says yes, no
        # matter what it is given.
        import unittest.mock as mock
        rows = self._real_rows()
        with mock.patch.object(self.verify, "expectations_met",
                               return_value=(True, rows)):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertEqual(len(missed), len(self.verify.PROBES))
        self.assertTrue(all("still passed" in m for m in missed))

    def test_a_verifier_that_raises_is_detected_not_swallowed(self):
        # The leading control meets it first, so the whole run is declared
        # unattributable rather than reporting each probe separately.
        import unittest.mock as mock
        with mock.patch.object(self.verify, "expectations_met",
                               side_effect=RuntimeError("boom")):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertTrue(missed)
        self.assertIn("raised", missed[0])

    def test_each_probe_is_caught_by_the_check_it_targets(self):
        """Not merely rejected - rejected by the right check.

        A probe caught by some other check looks live while the one it was
        written for stays unproven. The position probe used to do exactly
        that: shifting only the path's start point broke the loop, so
        `closed loops` objected and `card position error` was never
        consulted.
        """
        for label, corrupt in self.verify.PROBES:
            with self.subTest(probe=label):
                broken = corrupt(self.job.stream)
                self.assertIsNotNone(broken)
                ok, rows = self.verify.expectations_met(
                    broken, *self.args, **self.kwargs)
                self.assertFalse(ok)
                failed = {r.label for r in rows if not r.ok}
                self.assertIn(label, failed,
                              f"{label!r} was rejected by {failed} instead")

    def test_a_probe_caught_by_the_wrong_check_is_reported(self):
        # Synthesise the shape: rejected, but never by the named check.
        import unittest.mock as mock
        Row = self.verify.Row
        # Every real check present and passing, plus one unrelated failure:
        # rejected, but never by the check any probe names.
        rows = self._real_rows() + [Row("something else", "x", "y", False)]
        with mock.patch.object(self.verify, "expectations_met",
                               return_value=(False, rows)):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertEqual(len(missed), len(self.verify.PROBES))
        self.assertTrue(all("still unproven" in m for m in missed))

    def test_the_position_probe_keeps_the_card_closed_and_its_size(self):
        # If it did not, it would be caught by closure or size instead.
        broken = self.verify._corrupt_position(self.job.stream)
        d = self.verify.decode(broken)
        self.assertEqual(len(d.closed_paths), len(d.paths))
        for box in d.path_bboxes():
            self.assertAlmostEqual(box.w, 63.0, delta=0.1)
            self.assertAlmostEqual(box.h, 88.0, delta=0.1)

    def test_the_size_probe_keeps_the_card_closed_and_its_origin(self):
        broken = self.verify._corrupt_size(self.job.stream)
        clean = self.verify.decode(self.job.stream).path_bboxes()[0]
        d = self.verify.decode(broken)
        self.assertEqual(len(d.closed_paths), len(d.paths))
        grown = d.path_bboxes()[0]
        self.assertAlmostEqual(grown.x, clean.x, delta=0.01)
        self.assertAlmostEqual(grown.y, clean.y, delta=0.01)
        self.assertAlmostEqual(grown.h, clean.h + 1.0, delta=0.05)

    def test_every_load_bearing_check_has_a_probe(self):
        # The checks whose failure would ruin a sheet or the mat.
        targeted = {label for label, _ in self.verify.PROBES}
        for needed in ("blade force", "speed", "card position error",
                       "card size error", "cuts inside the marks",
                       "cut paths", "registration preamble",
                       "scan window length"):
            self.assertIn(needed, targeted)

    def test_a_check_already_failing_gives_its_probe_no_free_pass(self):
        """The leading control.

        A check stuck failing on a healthy stream would be "hit" by its own
        probe without the probe doing anything, and the liveness report
        would read clean while that check was broken open.
        """
        import unittest.mock as mock
        real = self.verify.expectations_met
        Row = self.verify.Row

        def stuck(stream, *a, **k):
            ok, rows = real(stream, *a, **k)
            rows = [Row(r.label, r.want, r.got, False)
                    if r.label == "card size error" else r for r in rows]
            return all(r.ok for r in rows), rows

        with mock.patch.object(self.verify, "expectations_met", stuck):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertTrue(any("already failing" in m and "card size" in m
                            for m in missed))

    def test_a_raising_baseline_makes_the_whole_run_inconclusive(self):
        import unittest.mock as mock
        with mock.patch.object(self.verify, "expectations_met",
                               side_effect=RuntimeError("boom")):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertEqual(len(missed), 1)
        self.assertIn("nothing in this run is attributable", missed[0])

    def test_state_left_behind_by_a_probe_is_caught(self):
        """The trailing control: the same stream must verify the same way
        after the probes as before them."""
        import unittest.mock as mock
        real = self.verify.expectations_met
        calls = {"n": 0}

        def drifting(stream, *a, **k):
            calls["n"] += 1
            ok, rows = real(stream, *a, **k)
            # Healthy on the way in, broken on the way out.
            if stream == self.job.stream and calls["n"] > 1:
                rows = [self.verify.Row(r.label, r.want, r.got, False)
                        for r in rows]
                return False, rows
            return ok, rows

        with mock.patch.object(self.verify, "expectations_met", drifting):
            missed = self.verify.liveness_failures(
                self.job.stream, *self.args, **self.kwargs)
        self.assertTrue(any("left state behind" in m for m in missed))

    def test_the_controls_are_clean_on_a_healthy_run(self):
        missed = self.verify.liveness_failures(
            self.job.stream, *self.args, **self.kwargs)
        self.assertEqual(missed, [])

    def test_liveness_failures_block_the_job(self):
        j = job_mod.build(preset_name="Plain paper test")
        self.assertEqual(j.blocked, [])
        j.liveness_failures = ["blade force: still passed"]
        self.assertTrue(j.blocked)
        self.assertIn("not trustworthy", j.blocked[0])

    def test_an_unregistered_job_verifies_and_probes_cleanly(self):
        """`--no-registration` used to be unsendable.

        The verifier asserted the registration preamble, the mark type, the
        scan command and the scan window unconditionally, and measured
        positions against the mark origin. None of that exists on a job that
        cuts a blank sheet in sheet coordinates, so six checks failed and
        the job could never be sent. The leading control surfaced it: a
        probe cannot prove a check that was already red.
        """
        j = job_mod.build(preset_name="Plain paper test", registration=False)
        self.assertEqual(j.liveness_failures, [])
        self.assertEqual([r.label for r in j.rows if not r.ok], [])

    def test_an_unregistered_job_sends_no_scan_commands(self):
        j = job_mod.build(preset_name="Plain paper test", registration=False)
        from cutline import verify
        d = verify.decode(j.stream)
        self.assertIsNone(d.scan_command)
        self.assertFalse(d.preamble_present)

    def test_probes_for_checks_that_do_not_run_are_not_counted_missed(self):
        # The scan-window probe has nothing to aim at without registration.
        j = job_mod.build(preset_name="Plain paper test", registration=False)
        self.assertEqual(j.liveness_failures, [])


class TestEveryForceIsChecked(unittest.TestCase):
    """The hole a liveness probe found on its first run."""

    def test_a_wrong_force_anywhere_in_the_stream_is_caught(self):
        from cutline import verify
        job = job_mod.build(preset_name="Plain paper test")
        # The Cameo 4 dialect sends the pressure twice. Corrupting only the
        # first used to pass, because the check read only the last.
        broken = verify._corrupt_force(job.stream)
        ok, rows = verify.expectations_met(
            broken, job.grid, job.marks, job.settings, job.device, job.mode,
            cards=job.grid.cards, offset_mm=job.offset_mm)
        self.assertFalse(ok)
        self.assertTrue(any(r.label == "blade force" and not r.ok
                            for r in rows))

    def test_the_decoder_records_every_force_not_just_the_last(self):
        from cutline import verify
        job = job_mod.build(preset_name="Plain paper test")
        d = verify.decode(job.stream)
        self.assertGreater(len(d.forces), 1)
        self.assertEqual(set(d.forces), {job.settings.force})
