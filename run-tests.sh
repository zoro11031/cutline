#!/bin/sh
# Fast, offline, no cutter needed. Everything that touches real USB or the
# WinBoat guest is skipped unless CUT_PROXIES_SLOW=1.
set -e
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -t . -v "$@"

# Then check the suite is not lying about its own coverage: a test that
# asserts nothing passes unconditionally, usually because a loop it was
# built around iterated zero times. Outside the suite because as a test it
# would discover itself and recurse.
echo
./tools/audit_tests.py
