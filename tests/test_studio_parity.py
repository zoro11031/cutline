"""The parity figure in docs/studio-parity.md must match its own tables."""
from pathlib import Path
import re
import unittest

PAGE = Path(__file__).resolve().parents[1] / "docs/studio-parity.md"


class TestStudioParity(unittest.TestCase):
    def test_headline_matches_the_tables(self):
        text = PAGE.read_text()
        tables = text.split("## Design: drawing", 1)[1].split("## Only in Cutline", 1)[0]
        rows = [line for line in tables.splitlines()
                if line.startswith("|") and not line.startswith(("|---", "| Silhouette Studio"))]
        marks = [line.rstrip(" |").rsplit("|", 1)[1].strip() for line in rows]
        self.assertTrue(rows)
        self.assertLessEqual(set(marks), {"✅", "🟡", "❌"})
        works, partial, missing = (marks.count(mark) for mark in ("✅", "🟡", "❌"))
        percent = 100 * (works + partial / 2) / len(marks)
        headline = re.search(r"\*\*Parity: ([\d.]+)%\*\* \((\d+) ✅, (\d+) 🟡, (\d+) ❌ across (\d+) features", text)
        self.assertIsNotNone(headline)
        self.assertEqual(tuple(map(int, headline.groups()[1:])), (works, partial, missing, len(marks)))
        self.assertAlmostEqual(float(headline.group(1)), percent, delta=0.05)


if __name__ == "__main__":
    unittest.main()
