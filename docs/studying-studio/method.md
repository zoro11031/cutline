# Method

Silhouette publishes none of its cutters' protocol, file formats or material
data. Everything Cutline knows about them was recovered, and this page sets
out the rules that recovery followed.

## Rules

**Nothing is a lookalike.** A command byte, a file field or a constant enters
Cutline only with a source: a capture of Studio's own traffic, Studio's own
files, or a published project that recovered it, cited where it is used. A
value whose meaning is unknown is sent exactly as Studio sends it and
labelled as unknown (`TB99`, `TB55,1`, the doubled force command) rather than
given a guessed meaning. If the bytes cannot be shown, the finding says so.

**Geometry claims and hardware claims are different claims.** "The stream
decodes to the right coordinates" is checked by decoding the stream. "The
cutter cuts in the right place" is checked only by a cutter cutting. The docs
never let the first stand in for the second, and each finding says which kind
it is. See [Status](status.md) for the ledger.

**Differential capture.** To learn what a setting does, change only that
setting, capture what Studio sends before and after, and compare the whole
stream, including the cutter's replies. Repeat the unchanged run as a
control, so variation between identical runs is not mistaken for an effect.
Record the Studio version and executable hash, the cutter model and firmware,
and every setting with each capture. Read USB data as a byte stream; packet
boundaries are not command boundaries.

**A null result is a result.** What was tried and did not work is recorded
alongside what did: the import that came in 2.84% oversized, the LE link that
never carried a reply, the speed-scale change that had to be reverted. A
finding that turned out wrong is corrected in place, with what replaced it.

**Measurements carry their date and their method.** A finding says when it
was made, on which version, and how, so it can be repeated or overturned.

**Studio's own files stay Studio's.** Captures and surveys record sizes,
hashes, headers and decoded values, not copies of proprietary documents. The
one vendor file Cutline bundles, `condition.csv`, is replaced only as a whole
from a named Studio version.

## Testing the tester

The checks that decide whether a job is sent were themselves the subject of
several findings, and the lessons generalise to any verification work:

- **Check that the thing ran, not that the outcome looks right.** A verifier
  that always passes prints the same table as one that works. In every
  failure found here, the output looked identical whether the mechanism was
  working or absent.
- **The tells are in the margins.** Two tests taking 2.3 s in a suite that
  runs in 1 s was the only sign they were reaching a container that the
  offline tier must never touch. "8/8 probes caught" meant nothing until it
  said which check caught each probe.
- **A guard must be tested in both directions.** A guard tested only where it
  should refuse is passed by one that always refuses.
- **Two paths that share an assumption are one path.** Exact agreement
  between two computations is a reason to look for the shared step, not proof
  of accuracy.
- **A limit set at launch is the protection that does not depend on
  cleanup.** Every guest session and long-running process is started inside a
  `timeout`, so it ends on its own even if the cleanup never runs.

## Sources

| Source | What it supplied |
|---|---|
| [inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette) | The Graphtec command set, device IDs, tool codes, the BLE transport, registration mark conventions. |
| Silhouette Card Maker ([guide](https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/)) | The layout algorithm and its published `layouts.json`, DXF corner encoding, `.studio3` templates, the cutting guide's tuning advice. |
| [studio3svg](https://github.com/zaydmulani09/studio3svg) | An independent `.studio3` format description. |
| [Sillinux](https://github.com/racxusdev/Sillinux) | A Linux RFCOMM transport on channel 1. |
| [Cuthulhu](https://github.com/vcolombo/cuthulhu) | Hardware notes for the Cameo 5 Alpha. |
| Silhouette Studio 5.0.556.1 | Its files, its symbols, and captures of its traffic. See [What Studio does](what-studio-does.md). |
