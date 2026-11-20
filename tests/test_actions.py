from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cut_proxies import actions, cli, editor_model, graphtec, importer, job, presets, transport, verify
from cut_proxies.holder_settings import HolderSettings


ART = """<svg xmlns="http://www.w3.org/2000/svg"
    xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
    width="279.4mm" height="215.9mm" viewBox="0 0 279.4 215.9">
  <g inkscape:groupmode="layer" inkscape:label="Panels" style="stroke: #f00; fill: #0f0">
    <rect x="88" y="50" width="35" height="40"/>
    <g inkscape:groupmode="layer" inkscape:label="Fold" fill="inherit">
      <path d="M 55,50 L 85,50" style="stroke: blue; fill: none"/>
    </g>
  </g>
  <g inkscape:groupmode="layer" inkscape:label="Print">
    <rect x="35" y="35" width="50" height="30"/>
  </g>
  <circle cx="150" cy="75" r="5"/>
</svg>"""


class TestArtworkGroups(unittest.TestCase):
    def setUp(self):
        self.artwork = importer.load_svg(StringIO(ART))

    def test_svg_preserves_inherited_fills_line_colors_and_nested_layers(self):
        self.assertEqual(list(actions.group_paths(self.artwork.paths, "line")),
                         ["#ff0000", "#0000ff", "(none)"])
        self.assertEqual(list(actions.group_paths(self.artwork.paths, "fill")),
                         ["#00ff00", "(none)", "#000000"])
        self.assertEqual(list(actions.group_paths(self.artwork.paths, "layer")),
                         ["Panels", "Panels / Fold", "(no layer)"])
        self.assertEqual(self.artwork.only("red").paths, self.artwork.paths[:1])

    def test_group_bound_to_holder_settings_cannot_be_redirected_to_another_holder(self):
        preset = presets.get("Print Paper")
        profiles = HolderSettings().profiles(preset, preset.cut_settings())
        plan = job.build(imported=self.artwork, send_mode="line", holder_profiles=profiles,
                         assignments=(actions.Assignment("red", actions.HOLDER_SETTINGS[1]),))
        self.assertEqual(plan.blocked, [])
        self.assertEqual(plan.action_groups[0].settings.holder, 1)
        with self.assertRaisesRegex(ValueError, "Choose the same holder"):
            job.build(imported=self.artwork, send_mode="line", holder_profiles=profiles,
                      assignments=(actions.Assignment("red", actions.HOLDER_SETTINGS[1], 2),))

    def test_duplicate_inkscape_layer_names_remain_separate(self):
        svg = ART.replace('inkscape:label="Print"',
                          'id="second-panels" inkscape:label="Panels"')
        drawing = importer.load_svg(StringIO(svg))
        self.assertEqual(list(actions.group_paths(drawing.paths, "layer")),
                         ["Panels [1]", "Panels [1] / Fold", "Panels [second-panels]", "(no layer)"])

    def test_svg_clones_keep_instance_color_layer_and_transform(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg"
              xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
              xmlns:xlink="http://www.w3.org/1999/xlink"
              width="279.4mm" height="215.9mm" viewBox="0 0 279.4 215.9">
          <defs>
            <g id="master" inkscape:label="Master" transform="translate(2,3)">
              <path d="M1,2 L3,2" stroke="currentColor"/>
            </g>
            <use id="inner" href="#master" x="10" y="15"/>
          </defs>
          <g inkscape:label="Fold" transform="translate(40,50)" color="blue">
            <use xlink:href="#inner" transform="scale(2)" fill="red"/>
          </g>
        </svg>"""
        design = importer.load_svg(StringIO(svg))
        self.assertEqual(len(design.paths), 1)
        path = design.paths[0]
        self.assertEqual(path.points, ((66, 90), (70, 90)))
        self.assertEqual(path.stroke, "#0000ff")
        self.assertEqual(path.fill, "#ff0000")
        self.assertEqual(path.layer, ("Fold",))

    def test_bad_svg_clone_references_are_refused(self):
        for target, definitions, message in (
                ("#missing", "", "missing or ambiguous"),
                ("file.svg#shape", "", "local #id"),
                ("#loop", '<use id="loop" href="#loop"/>', "recursive reference"),
                ("#symbol", '<symbol id="symbol"/>', "viewport or definition")):
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                   f'<defs>{definitions}</defs><use href="{target}"/></svg>')
            with self.subTest(target=target), self.assertRaisesRegex(importer.ImportError_, message):
                importer.load_svg(StringIO(svg))

    def test_dxf_preserves_entity_layer_and_true_color(self):
        pairs = """0 SECTION 2 HEADER 9 $INSUNITS 70 4 9 $EXTMAX 10 279.4 20 215.9
        0 ENDSEC 0 SECTION 2 ENTITIES 0 LINE 8 Fold 420 255 10 40 20 120 11 70 21 120
        0 LINE 8 Edge 62 1 10 80 20 110 11 90 21 110 0 ENDSEC 0 EOF"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "send.dxf")
            path.write_text("\n".join(pairs.split()))
            drawing = importer.load(path)
        self.assertEqual(list(actions.group_paths(drawing.paths, "layer")), ["Fold", "Edge"])
        self.assertEqual(list(actions.group_paths(drawing.paths, "line")), ["#0000ff", "#ff0000"])

    def test_design_all_actions_excludes_hidden_and_none_objects(self):
        from dataclasses import replace
        shape = editor_model.Shape("Rectangle", 40, 50, 30, 40)
        artwork = editor_model.artwork((shape, replace(shape, action="Score"),
                                        replace(shape, action="None"), replace(shape, visible=False)),
                                       279.4, 215.9, editor_model.ALL_ACTIONS)
        self.assertEqual(list(actions.group_paths(artwork.paths, "layer")), ["Cut", "Score"])

    def test_cli_lists_and_builds_group_assignments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "art.svg")
            path.write_text(ART)
            with mock.patch.object(cli, "say") as say:
                self.assertEqual(cli.main(["import", str(path), "--list-groups", "fill"]), 0)
                self.assertTrue(any("Fill group #00ff00" in call.args[0]
                                    for call in say.call_args_list if call.args))
                result = cli.main(["cut", "--from", str(path), "--send-by", "line",
                                   "--group", "blue=2:Pen, no blade", "--group",
                                   "red=Plain paper test", "--dry-run"])
                self.assertEqual(result, 0)


class TestGroupedSend(unittest.TestCase):
    def grouped(self, **kwargs):
        options = dict(imported=importer.load_svg(StringIO(ART)), send_mode="line",
                       assignments=(actions.Assignment("blue", "Pen, no blade", 2),
                                    actions.Assignment("red", "Plain paper test")), offset_mm=(0, 0))
        options.update(kwargs)
        return job.build(**options)

    def test_group_plan_has_one_registration_and_waits_between_actions(self):
        checked = self.grouped()
        self.assertEqual(checked.blocked, [])
        self.assertEqual([group.key for group in checked.action_groups], ["#0000ff", "#ff0000"])
        self.assertEqual(checked.stream.count(b"TB123,"), 1)
        self.assertEqual(checked.stream.count(b"SO0\x03"), 1)
        self.assertEqual(len(checked.paths), 2)
        path_steps = [step for step in checked.steps if step.pace]
        self.assertEqual(len(path_steps), 2)
        self.assertTrue(all(step.wait_ready for step in path_steps))
        self.assertLess(checked.stream.index(b"M800,900\x03"),
                        checked.stream.index(b"M800,1560\x03"))
        states = verify._observed_motion_settings(verify.decode(checked.stream).commands)
        self.assertEqual(states[0][:5], (2, 18, 10, None, 0))
        self.assertEqual(states[1][:5], (1, 15, 10, 2, 18))

    def test_wrong_holder_force_or_path_is_detected(self):
        checked = self.grouped()
        for stream, target in ((checked.stream.replace(b"J2\x03", b"J1\x03"), "group blade settings"),
                               (verify._corrupt_group_force(checked.stream), "group blade settings"),
                               (verify._corrupt_imported_vertex(checked.stream), "imported path geometry")):
            with self.subTest(target=target):
                ok, rows = verify.expectations_met(
                    stream, checked.grid, checked.marks, checked.settings, checked.device,
                    expected_paths=checked.paths, action_groups=checked.action_groups)
                self.assertFalse(ok)
                self.assertIn(target, {row.label for row in rows if not row.ok})

    def test_failed_registration_prevents_all_group_paths(self):
        checked = self.grouped()
        link = transport.Transport()
        link.device = checked.device
        link.write = mock.Mock(side_effect=len)
        link.read = mock.Mock(return_value=graphtec.RESP_REGMARK_FAIL)
        link.status = mock.Mock(return_value="ready")
        link.tool_codes = mock.Mock(return_value=(2, 7))
        with self.assertRaises(transport.RegistrationFailed):
            link.run(checked.steps)
        self.assertEqual([call.args[0] for call in link.write.call_args_list],
                         [step.data for step in checked.registration_steps() if step.kind == "write"])

    def test_two_blade_settings_can_share_a_holder(self):
        checked = self.grouped(assignments=(actions.Assignment("blue", "Plain paper test"),
                                            actions.Assignment("red", "330 gsm black-core")))
        self.assertEqual(checked.blocked, [])
        self.assertEqual([group.settings.holder for group in checked.action_groups], [1, 1])
        self.assertEqual([group.settings.depth for group in checked.action_groups], [2, 7])
        self.assertIn(b"TF", checked.stream)

        manual = self.grouped(manual_depth=True, assignments=(
            actions.Assignment("blue", "Plain paper test"),
            actions.Assignment("red", "330 gsm black-core")))
        self.assertEqual(manual.blocked, [])
        self.assertEqual([group.settings.depth for group in manual.action_groups], [None, None])
        self.assertNotIn(b"TF", manual.stream)

    def test_incompatible_tools_and_duplicate_or_empty_groups_are_refused(self):
        for assignments, message in (
                ((actions.Assignment("blue", "Pen, no blade"),
                  actions.Assignment("red", "Plain paper test")), "different tools"),
                ((actions.Assignment("blue"), actions.Assignment("#00F")), "more than once"),
                ((actions.Assignment("yellow"),), "absent"),
                ((), "at least one")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.grouped(assignments=assignments)


class TestStagedPasses(unittest.TestCase):
    """The first passes at a lighter force and speed, the rest at full, in one job."""

    def staged(self, **kwargs):
        options = dict(passes=8, force=20, speed=5, offset_mm=(0, 0),
                       blade_options=dict(light_passes=2, light_force=10, light_speed=3))
        options.update(kwargs)
        return job.build(**options)

    def test_light_passes_come_first_then_the_rest_at_full_settings(self):
        checked = self.staged()
        self.assertEqual(checked.blocked, [])
        self.assertEqual([(g.settings.passes, g.settings.force, g.settings.speed) for g in checked.action_groups],
                         [(2, 10, 3), (6, 20, 5)])
        decoded = verify.decode(checked.stream)
        self.assertEqual(len(decoded.paths), checked.grid.count * 8, "Passes stays the total")
        states = verify._observed_motion_settings(decoded.commands)
        cards = checked.grid.count
        self.assertEqual({state[1:3] for state in states[:2 * cards]}, {(10, 3)})
        self.assertEqual({state[1:3] for state in states[2 * cards:]}, {(20, 5)})
        self.assertEqual(checked.stream.count(b"TB123,"), 1, "one registration for both stages")
        self.assertIn("group blade settings", {row.label for row in checked.rows})

    def test_unset_light_values_keep_the_full_ones(self):
        checked = self.staged(blade_options=dict(light_passes=1, light_force=12))
        self.assertEqual([(g.settings.force, g.settings.speed) for g in checked.action_groups], [(12, 5), (20, 5)])

    def test_a_corrupted_light_stage_is_refused(self):
        checked = self.staged()
        stream = checked.stream.replace(b"FX10,", b"FX11,")
        ok, rows = verify.expectations_met(
            stream, checked.grid, checked.marks, checked.settings, checked.device,
            cards=checked.grid.cards, offset_mm=(0, 0), action_groups=checked.action_groups,
            source_paths=checked.paths)
        self.assertFalse(ok)
        self.assertIn("group blade settings", {row.label for row in rows if not row.ok})

    def test_light_passes_must_leave_a_full_pass(self):
        for light in (8, 9):
            with self.subTest(light=light), self.assertRaisesRegex(ValueError, "at least one full pass"):
                self.staged(blade_options=dict(light_passes=light))

    def test_light_passes_below_the_total_are_allowed(self):
        checked = self.staged(blade_options=dict(light_passes=7, light_force=10))
        self.assertEqual(checked.blocked, [])
        self.assertEqual([g.settings.passes for g in checked.action_groups], [7, 1])

    def test_no_light_passes_leaves_a_plain_single_settings_job(self):
        checked = self.staged(blade_options={})
        self.assertEqual(checked.action_groups, ())
        self.assertEqual(checked.settings.passes, 8)

    def test_heavier_light_passes_are_allowed_with_a_warning(self):
        checked = self.staged(blade_options=dict(light_passes=2, light_force=30))
        self.assertEqual(checked.blocked, [])
        self.assertTrue(any("not lighter" in warning for warning in checked.warnings))

    def test_grouped_send_stages_each_group(self):
        checked = job.build(imported=importer.load_svg(StringIO(ART)), send_mode="line", offset_mm=(0, 0),
                            passes=3, blade_options=dict(light_passes=1, light_force=5),
                            assignments=(actions.Assignment("red"),))
        self.assertEqual(checked.blocked, [])
        self.assertEqual([group.key for group in checked.action_groups],
                         ["#ff0000 · light passes", "#ff0000 · full passes"])
        self.assertEqual([group.settings.passes for group in checked.action_groups], [1, 2])

    def test_presets_remember_staged_passes(self):
        settings = graphtec.CutSettings(passes=6, light_passes=2, light_force=8, light_speed=2)
        preset = presets.Preset("Staged", "", "measured", __import__("dataclasses").asdict(settings))
        self.assertEqual(preset.cut_settings(), settings)
        self.assertEqual(presets.Preset("Old", "", "measured", dict(force=20)).cut_settings().light_passes, 0)
