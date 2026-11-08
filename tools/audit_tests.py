#!/usr/bin/env python3
"""Find tests that pass without asserting anything.

A sibling session's audit of print options reported four as broken and two
of them were not: its test input was a single-page PDF, so the page-range
and page-set checks could not have had an effect either way. A green that
covers no path — the same failure as a probe that never fires, arriving
from the fixture rather than from the code.

The general case is hard to detect automatically. The degenerate case is
not: a test that reaches its end without evaluating a single assertion
passes unconditionally, and the usual cause is a loop it was built around
iterating zero times.

This runs the suite with every `assert*` on `TestCase` counted per test and
reports any that made none. It lives outside the suite on purpose: as a
test it would discover itself, run the suite inside the suite, and recurse
— which it did, and which is why this is a script.

    tools/audit_tests.py            report
    tools/audit_tests.py --self-test  prove the auditor can fail

Exit codes: 0 clean, 1 silent tests found, 2 the audit did not run.
"""

from __future__ import annotations

import io
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Enough tests that an empty or broken discovery is obvious rather than
# reported as a clean run.
MINIMUM_EXPECTED = 50


def _instrument():
    """Replace every assert* with a counting wrapper. Returns (counts, undo).

    Only names beginning with `assert`. An earlier version also took
    anything starting with `fail`, which swallowed `failureException` —
    a class, not a method — and turned every assertion failure into a
    TypeError deep inside unittest.
    """
    counts: dict[str, int] = {}
    current: list[str | None] = [None]
    saved = []
    for name in dir(unittest.TestCase):
        if not name.startswith("assert"):
            continue
        fn = getattr(unittest.TestCase, name)
        if not callable(fn) or isinstance(fn, type):
            continue
        saved.append((name, fn))

        def make(f):
            def wrapper(self, *args, **kwargs):
                if current[0]:
                    counts[current[0]] = counts.get(current[0], 0) + 1
                return f(self, *args, **kwargs)
            return wrapper
        setattr(unittest.TestCase, name, make(fn))

    def undo():
        for name, fn in saved:
            setattr(unittest.TestCase, name, fn)

    return counts, current, undo


def _result_class(counts, current):
    class Watch(unittest.TextTestResult):
        def startTest(self, test):
            current[0] = test.id()
            counts.setdefault(current[0], 0)
            super().startTest(test)
    return Watch


def audit(suite):
    counts, current, undo = _instrument()
    try:
        unittest.TextTestRunner(
            stream=io.StringIO(),
            resultclass=_result_class(counts, current)).run(suite)
    finally:
        undo()
    return counts


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if "--self-test" in argv:
        # Prove the auditor notices a silent test, since it is itself a
        # check that has only ever agreed.
        class Silent(unittest.TestCase):
            def test_asserts_nothing(self):
                for _ in []:                      # never runs
                    self.fail("unreachable")

            def test_asserts_something(self):
                self.assertTrue(True)

        counts = audit(unittest.TestLoader().loadTestsFromTestCase(Silent))
        silent = [t for t, n in counts.items() if n == 0]
        ok = len(silent) == 1 and silent[0].endswith("test_asserts_nothing")
        print("self-test:", "the auditor sees a silent test" if ok
              else f"BROKEN - counted {counts}")
        return 0 if ok else 2

    suite = unittest.TestLoader().discover(str(ROOT / "tests"),
                                           top_level_dir=str(ROOT))
    counts = audit(suite)
    if len(counts) < MINIMUM_EXPECTED:
        print(f"only {len(counts)} tests discovered; the audit did not run",
              file=sys.stderr)
        return 2

    silent = sorted(t for t, n in counts.items() if n == 0)
    one = sum(1 for n in counts.values() if n == 1)
    print(f"{len(counts)} tests, {len(silent)} asserting nothing, "
          f"{one} asserting once")
    if silent:
        print("\nThese pass unconditionally:", file=sys.stderr)
        for t in silent:
            print(f"  {t}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
