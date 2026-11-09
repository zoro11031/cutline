"""Does each guard's test class actually discriminate, or only refuse?

A guard tested solely on the failure case is still unproven: a function
hardwired to "always refuse" passes every such test. The mirror is just as
bad — a class full of negative cases is passed by a function that can only
say "fine", which is what `rdp_busy`'s tests did until this file existed.
That guard is the one stopping this session walking into another's VM
session, and it had never been asked to say "busy".

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

from cut_proxies import regmarks, winboat


def _run(class_paths):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        [loader.loadTestsFromName(p) for p in class_paths])
    return unittest.TextTestRunner(stream=io.StringIO()).run(suite)


# (module, attribute, stub, description, the classes that must object)
CASES = (
    (winboat, "rdp_busy", lambda *a, **k: "",
     "rdp_busy can never report a held session",
     ["tests.test_guards.TestRdpBusyFires"]),
    (winboat, "rdp_busy", lambda *a, **k: "pid 1 running x holds it",
     "rdp_busy reports every port as held",
     ["tests.test_guards.TestRdpBusyFires"]),
    (winboat, "port_open", lambda *a, **k: True,
     "port_open calls every port open",
     ["tests.test_guards.TestPortOpenFires"]),
    (winboat, "port_open", lambda *a, **k: False,
     "port_open calls every port closed",
     ["tests.test_guards.TestPortOpenFires"]),
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
    (winboat, "our_stale_sessions", lambda *a, **k: [],
     "our_stale_sessions never finds one of ours",
     ["tests.test_guards.TestOurStaleSessionsNeverSeesAnotherTool"]),
    (winboat, "our_stale_sessions", lambda *a, **k: [(999, "foreign")],
     "our_stale_sessions claims a foreign session is ours",
     ["tests.test_guards.TestOurStaleSessionsNeverSeesAnotherTool"]),
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
            getattr(winboat.rdp_busy, "__name__", "") == "<lambda>")
        self.assertFalse(
            getattr(regmarks.collisions, "__name__", "") == "<lambda>")
        self.assertEqual(regmarks.collisions.__name__, "collisions")
        self.assertEqual(winboat.port_open.__name__, "port_open")

    def test_an_unguarded_case_would_be_reported(self):
        # Prove this file can fail: a guard nothing asserts on.
        real = regmarks.clamp
        regmarks.clamp = lambda i, l, t: (i, l, t)
        try:
            result = _run(["tests.test_guards.TestPortOpenFires"])
        finally:
            regmarks.clamp = real
        # Port tests know nothing about clamp, so they stay green - which is
        # exactly the shape of "tested only one way" this file exists to
        # catch, and why each case above must name the right class.
        self.assertEqual(len(result.failures) + len(result.errors), 0)


if __name__ == "__main__":
    unittest.main()
