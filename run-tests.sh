#!/bin/sh
# Fast and offline. Cutter connections are simulated by the tests.
set -e
cd "$(dirname "$0")"
# Keep the run away from the real settings and templates folders: importing
# the config moves an old-named folder forward, and nothing here should.
if [ -z "${CUTLINE_CONFIG}" ]; then
    scratch=$(mktemp -d)
    trap 'rm -rf "${scratch}"' EXIT
    export CUTLINE_CONFIG="${scratch}/config" CUTLINE_TEMPLATES="${scratch}/templates"
fi
python3 -m unittest discover -s tests -t . -v "$@"

# Then check the suite is not lying about its own coverage: a test that
# asserts nothing passes unconditionally, usually because a loop it was
# built around iterated zero times. Outside the suite because as a test it
# would discover itself and recurse.
echo
./tools/audit_tests.py
