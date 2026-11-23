import unittest

from cutline import desktop_help


class TestCutProblems(unittest.TestCase):
    def test_every_problem_has_advice_and_a_known_action(self):
        self.assertTrue(desktop_help.CUT_PROBLEMS)
        for symptom, (advice, action) in desktop_help.CUT_PROBLEMS.items():
            self.assertTrue(symptom and advice.strip(), symptom)
            self.assertIn(action, (desktop_help.OPEN_SHEET, desktop_help.DIAL_IN,
                                   desktop_help.CUT_SETTINGS, None), symptom)

    def test_track_enhancing_is_offered_as_a_step(self):
        advice, action = desktop_help.CUT_PROBLEMS["Rollers shift, skew or mark the sheet"]
        self.assertIn("Turn off Track Enhancing", advice)
        self.assertEqual(action, desktop_help.CUT_SETTINGS)
        mentions = [symptom for symptom, (text, _) in desktop_help.CUT_PROBLEMS.items()
                    if desktop_help.TRACK_OFF in text]
        self.assertIn(desktop_help.REGISTRATION_PROBLEM, mentions)

    def test_the_registration_entry_is_about_registration(self):
        self.assertIn("Registration", desktop_help.REGISTRATION_PROBLEM)


if __name__ == "__main__":
    unittest.main()
