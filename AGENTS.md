# Cutline contributor guide

Cutline is an independent hobby project for design and Print & Cut on
Silhouette cutters, mainly for cutting Magic proxies. See [README.md](README.md)
for setup and [docs/](docs/README.md) for implementation and hardware findings.

Commit Markdown only in `README.md`, `AGENTS.md`, and `docs/`. Keep local
plans, raw captures, and research out of Git. Public files must be
free of credentials, pairing keys, device identifiers, personal paths, and
the names of external card-printing tools that produce sheets for Cutline.
Silhouette Card Maker is credited for its layouts and guides.

## Cutter jobs

- Both desktop frontends and the CLI send jobs through `cutline/job.py`.
  Build, decode, and measure the job before sending. Refuse on `Job.blocked`.
  Imported paths use a synthetic grid and go through the same checks.
- Keep `verify.liveness_failures` in every build. The original stream must
  pass before the probes and verify identically afterward. Each probe must
  trip the check it names. Add a probe for each new blocking check.
- Use sourced command bytes in `graphtec.py`. Label unexplained constants as
  such. Coordinates on the wire are (y, x). Apply the registration origin
  shift and calibration translation once in `CutJob._path_commands`, then
  subtract the calibration offset during verification.
- Decoded geometry and physical cuts are separate results. Confirm hardware
  outcomes with the operator before recording them. Put findings in the
  appropriate topic in `docs/` with the date, method, and Studio version
  where applicable. Update `docs/status.md` and record failed attempts too.
- Preserve preset provenance. A derived preset says "never cut." A measured
  preset needs a dated cut and edge inspection on this machine. Replace the
  bundled `condition.csv` only with a complete snapshot from a named Studio
  release. Imported user copies take priority. Vendor presets must still
  match the snapshot.

## Geometry and desktop

- Keep the default card layout eight-up on Letter and A4 in landscape.
  Preserve Card Maker's 300 ppi rounding and the centered engine's legacy
  settings and bundle compatibility. The two engines agree within 0.1 mm.
- Search registration arm length for a clear fit. The corner exclusion is
  the exact arm-length square. Cards close to the marks get scan-cover hints.
  Both engines use the standard three- and four-mark patterns.
- Report the scale used by imports. Keep the template writers standard
  library only and use bulge polylines for DXF corners. Proofs and material
  test maps are reference files. Keep registration marks and map labels out
  of cutter paths.
- Keep shared help in `desktop_help.py` and `starting_guide.py` in sync with
  control names in Qt and GTK. Attribute community advice, date it under
  Materials in `docs/how-it-works.md`, and keep it out of presets.
- When a feature changes, update its row and score in `docs/studio-parity.md`.
  A cutting option stays partial until it has been used for a physical cut.
  State facts where needed in code, tests, and UI. Keep research citations
  and explanations in the docs.

## Checks and commits

- Run `./run-tests.sh`, `cutline cut --dry-run`, and `cutline status` before
  committing code changes. The suite and assertion audit must run offline
  without a cutter or guest. Toolkit tests must skip cleanly if Qt or GTK
  is missing.
- For a new guard, test both answers in `tests/test_discrimination.py`. For
  a new refusal or warning, add a firing case in `tests/test_guards.py` and
  verify it fails when the guard is broken.
- For SVG or DXF writer changes, render the Letter portrait proof at 150 dpi
  with `rsvg-convert` and check that it is 1275 × 1650 pixels. Keep the
  independent render check in `test_sheet`.
- When windows change, regenerate and inspect screenshots with
  `python3 tools/capture_screenshots.py`.
- Work on a branch, squash work in progress, fast-forward `main`, and delete
  the branch. Commit as Justin Caveda. Use one commit per piece of work.

## Releases

Build release packages in CI. Publish the AppImage, Flatpak, and Windows
installer, plus `install-cutline.sh` for Linux USB permissions and
`SHA256SUMS`. Keep the RPM and Arch recipes in the repo. Pin downloaded
runtimes and dependencies by version and SHA-256.

The AppImage bundles PySide6 and GTK. Auto mode prefers GTK on GNOME and Qt
elsewhere. Use `gui --frontend qt` or `gui --frontend gtk` to override it.
The Windows installer uses embedded Python and PySide6. In user docs, show
`sh install-cutline.sh --udev` if Cutline needs Linux USB access, then tell
users to reconnect the cutter.

1. Bump the version in `cutline/__init__.py`, `packaging/cutline.spec`,
   `packaging/aur/PKGBUILD`, and `packaging/install-cutline.sh`.
2. Push `release/vX.Y.Z` and wait for every release-branch check to pass.
3. Squash release work, fast-forward `main`, and push an annotated
   `vX.Y.Z` tag. Put a short, user-facing changelog in the tag body.
4. Wait for the tagged workflow to publish, verify the notes and downloads
   on GitHub, and remove the release branch.
