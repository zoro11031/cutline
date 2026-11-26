# Cutline documentation

How Cutline works, and how what it knows about Silhouette's cutters and
Silhouette Studio was worked out. To install and use Cutline, start with the
[README](../README.md).

| Page | What it covers |
|---|---|
| [How Cutline works](how-it-works.md) | Layouts and marks, the cut stream, verification, files, materials, connecting to a cutter. |
| [How Silhouette Studio was studied](studying-studio.md) | The rules, the sources, the Windows guest, and what Studio itself does. |
| [Status and open questions](status.md) | What is verified, what is untested, and the experiments still to run. |
| [Silhouette Studio parity](studio-parity.md) | Studio's features beside Cutline's, each marked working, partial or missing. |
| [Windows packaging](windows-packaging.md) | Offline installer, locked dependencies, and upgrade compatibility. |

Every finding is dated (all in 2026) and says how it was established. A claim
about bytes is verified by decoding the bytes; a claim about what a cutter
does is verified only by a cutter doing it. The one raw capture kept here,
[`cameo4-studio-registration-2026-11-17.tsv`](cameo4-studio-registration-2026-11-17.tsv),
is Studio's registration traffic to a Cameo 4, and a test replays it.
