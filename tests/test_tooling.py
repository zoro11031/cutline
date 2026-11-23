"""The shared reading of a tool-holder reply."""

import unittest

from cutline import graphtec, tooling


CAMEO4 = graphtec.device_by_name("Silhouette Cameo 4")
CAMEO4_PRO = graphtec.device_by_name("Silhouette Cameo 4 Pro")
PORTRAIT2 = graphtec.device_by_name("Silhouette Portrait 2")


class TestHolderLabels(unittest.TestCase):
    def test_a_reply_names_the_tool_in_each_holder(self):
        self.assertEqual(tooling.holder_labels((2, 0), CAMEO4),
                         ("Tool Holder 1 · AutoBlade", "Tool Holder 2 · Empty"))
        self.assertEqual(tooling.holder_labels((1, 7), CAMEO4),
                         ("Tool Holder 1 · Ratchet blade", "Tool Holder 2 · Pen"))

    def test_an_unknown_code_is_reported_rather_than_hidden(self):
        label, _ = tooling.holder_labels((99, 0), CAMEO4)
        self.assertEqual(label, "Tool Holder 1 · Unknown tool (ID 99)")
        self.assertEqual(tooling.holder_labels((255, 0), CAMEO4)[0],
                         "Tool Holder 1 · No tool detected")

    def test_no_reply_reads_as_unknown_not_as_empty(self):
        self.assertEqual(tooling.holder_labels((), CAMEO4),
                         ("Tool Holder 1", "Tool Holder 2"))
        self.assertEqual(tooling.holder_labels(None, CAMEO4),
                         ("Tool Holder 1", "Tool Holder 2"))

    def test_a_single_holder_device_gets_one_label(self):
        self.assertEqual(tooling.holder_labels((2,), PORTRAIT2), ("Tool Holder 1 · AutoBlade",))
        self.assertEqual(tooling.holder_labels((), PORTRAIT2), ("Tool Holder 1",))
        self.assertEqual(len(tooling.holder_labels((2, 0), CAMEO4_PRO)), 2)

    def test_installed_reads_each_holder_and_refuses_to_invent_one(self):
        self.assertEqual(tooling.installed((2, 7), 1), 2)
        self.assertEqual(tooling.installed((2, 7), 2), 7)
        self.assertIsNone(tooling.installed((2,), 2))
        self.assertIsNone(tooling.installed((), 1))
        self.assertIsNone(tooling.installed((2, 7), 0))
        self.assertTrue(tooling.is_autoblade((2, 0)))
        self.assertFalse(tooling.is_autoblade((1, 0)))


class TestDepthCorrection(unittest.TestCase):
    """Both answers: the correction fires on a manual blade and only then."""

    def test_a_manual_blade_sends_depth_back_to_manual(self):
        depth, reason = tooling.depth_correction((1, 0), 7)
        self.assertEqual(depth, tooling.MANUAL_DEPTH)
        self.assertIn("Ratchet blade", reason)
        self.assertIn("manual", reason)
        self.assertEqual(tooling.depth_correction((4, 0), 1)[0], tooling.MANUAL_DEPTH)
        self.assertEqual(tooling.depth_correction((0, 0), 10)[0], tooling.MANUAL_DEPTH)

    def test_an_autoblade_keeps_automatic_depth(self):
        self.assertEqual(tooling.depth_correction((2, 0), 7), (None, ""))

    def test_an_already_manual_depth_is_left_alone(self):
        self.assertEqual(tooling.depth_correction((1, 0), tooling.MANUAL_DEPTH), (None, ""))
        self.assertEqual(tooling.depth_correction((1, 0), None), (None, ""))

    def test_nothing_is_changed_without_a_usable_reply(self):
        self.assertEqual(tooling.depth_correction((), 7), (None, ""))
        self.assertEqual(tooling.depth_correction(None, 7), (None, ""))
        self.assertEqual(tooling.depth_correction((255, 0), 7), (None, ""))

    def test_the_second_holder_is_read_when_it_is_the_one_asked_about(self):
        self.assertEqual(tooling.depth_correction((2, 1), 7, holder=2)[0], tooling.MANUAL_DEPTH)
        self.assertEqual(tooling.depth_correction((1, 2), 7, holder=2), (None, ""))


if __name__ == "__main__":
    unittest.main()
