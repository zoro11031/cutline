#!/bin/sh
# Fast, offline, no cutter needed. Everything that touches real USB or the
# WinBoat guest is skipped unless CUT_PROXIES_SLOW=1.
set -e
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -t . -v "$@"
