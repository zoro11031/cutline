# MISSION BRIEFING: Reverse Engineering Silhouette Studio & Bringing `cut-proxies` to Full Feature Parity

References to the desktop app's WinBoat bridge describe an earlier workflow.
For local file export, see the current setup guide.

## 1. Project Context & Location
You are working on **`cut-proxies`** (located at `/home/justin/Projects/Printing/cut-proxies`), a Linux-native driver, cut-stream builder, verification engine, and GUI/CLI for the **Silhouette Cameo 4/5** cutting plotter, specifically optimized for cutting custom TCG proxy cards on heavy stock (ProxyJak 330gsm black core).

The repository lives alongside:
- `proxypress/` (`/home/justin/Projects/Printing/proxypress`): Web proxy generator.
- `canon-suite/` (`/home/justin/Projects/Printing/canon-suite`): The Canon CUPS/CPCA laser driver layer.

### The Virtual Machine (WinBoat)
There is a WinBoat Windows VM environment configured with **Silhouette Studio 4.5.815** installed (`C:\Program Files\Silhouette America\Silhouette Studio`).
- Linux host staging path: `/home/justin/Projects/Printing/cut-proxies/guest-staging/`
- WinBoat guest share path: `\\host.lan\guest-staging\`
- Helper scripts: `tools/probe-studio-dxf.ps1` and `cut_proxies/winboat.py`

---

## 2. Strict Engineering Ground Rules (from `AGENTS.md`)
You MUST adhere strictly to the repository's guidelines:
1. **Nothing here is a lookalike.** Every Graphtec/Silhouette GP-GL command byte in `cut_proxies/graphtec.py` must be recovered by differential capture against real Silhouette Studio traffic or cited directly to its proven source. Do not add commands or tweak constants based on plausible guesses.
2. **Hardware claims vs. Geometry claims:** If you have not cut a physical sheet on this exact hardware, a verified coordinate decode is a *geometry claim*, NOT a *hardware claim*. Keep them strictly separated in `docs/FINDINGS.md`.
3. **Preset Provenance:** Every preset in `presets.py` must declare its `origin`: `silhouette` (verbatim from Studio), `guide` (community documentation), `recovered` (inkscape-silhouette), `derived` (extrapolated here, note must say "never cut"), or `measured` (cut on *this* machine).
4. **Verification Proves Itself Every Run:** `verify.liveness_failures` intentionally corrupts each built stream 8 distinct ways to ensure the checks fail when breached. Do not bypass or disable verification.
5. **Session Safety:** All WinBoat / RDP / PowerShell launches must carry bounded timeouts (`timeout 600` for scripts, `timeout 14400` for interactive) and clean up at the *start* of runs using `_is_rdp_client`. Never log or leak credentials from `~/.winboat/docker-compose.yml`.

---

## 3. High-Priority Parity Targets to Reverse-Engineer

### Target A: AutoBlade Adjustment Protocol
Currently, `cut-proxies` sends blade depth in comments/stream, but manual depth is often assumed. Reverse-engineer the exact motor tapping sequence Silhouette Studio sends to mechanically dial the Cameo 4/5 AutoBlade to depth:
- Identify the carriage movement to the adjustment holes on the far left of the rail.
- Capture the exact solenoid/motor pulse commands (`TB...`, `Z...`, or step commands) used to tap the blade against the adjustment stop to zero it, then increment it to target depth (1–10).

### Target B: Optical Registration Scan & Sensor Readback Transformation
In `cut_proxies/regmarks.py` and `graphtec.py`:
- Studio sends mark search preambles (`TB51,400`, `TB53,10`, `TB99`).
- Capture the raw serial/USB response returned by the Cameo's optical sensor after scanning marks 1, 2, and 3.
- Reverse-engineer Studio's math for calculating the affine transformation matrix (rotation skew, X/Y drift, scale expansion) from the readback values, and verify how it adjusts the toolpaths.
- Document the exact difference between Type 1 (3 marks) and Cameo 5 Alpha (4 marks).

### Target C: Corner Overcut & Tangent Compensation
Studio has a "Line Segment Overcut" feature for thick materials (extending cuts slightly past corners and lifting the blade to prevent corner tears):
- Inspect how Studio encodes overcut into the GP-GL stream (e.g. `FE`, `FC`, or explicit `D` arc tangent loops).
- Implement overcut path generation in `cut_proxies/template.py` and `cut_proxies/job.py`.

### Target D: Dual-Carriage & Tool 2 Support
Cameo 4/5 features dual tool holders (Tool 1 for AutoBlade/fine cut, Tool 2 for Rotary blade, Kraft blade, or Punch tool):
- Capture tool selection commands and carriage switching codes in the Graphtec stream.
- Map out Tool 2 toolpath execution for heavy 3mm Kraft blades (used for heavy 330gsm cardstock and chipboard).

### Target E: Studio File Format (.studio / .studio3) Native Parser & Generator
Currently, `cut-proxies` bridges via SVG and DXF (using polyline bulge `tan(22.5°)`).
- Investigate the `.studio3` container (an obfuscated zip/sqlite archive containing vector paths and cut settings).
- Build a native `.studio3` generator or direct converter so files staged into WinBoat open with zero import scaling warnings, zero path compounding, and pre-selected material settings.

---

## 4. Execution Workflow

1. **Audit & Test Suite:**
   Run `./run-tests.sh` to confirm the fast offline test baseline passes (317 tests).
2. **Differential Capture via WinBoat:**
   - Use USB packet captures (USBPcap/Wireshark on the guest or usbmon on the host) while Silhouette Studio sends jobs to virtual/real plotters.
   - Dump and compare byte streams against `cut-proxies --dump-stream`.
3. **Implement & Test:**
   - Update `cut_proxies/graphtec.py`, `regmarks.py`, `template.py`, or `winboat.py`.
   - Add matching verification checks and liveness probes in `verify.py`.
   - Add unit tests in `tests/`.
4. **Document Findings:**
   - Record newly established facts, differential captures, and command bytes in `docs/FINDINGS.md` with numbered sections, dates, and methods.
