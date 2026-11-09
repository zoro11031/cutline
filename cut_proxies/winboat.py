"""The WinBoat fallback: drive Silhouette Studio in the Windows guest.

Why this exists at all.  The direct USB path in `graphtec.py` is built from a
protocol that was recovered, not published, and Silhouette has shipped
firmware that changed what the machine accepts.  When the direct path will not
register or will not cut, the question becomes "is the template wrong, or is
the protocol wrong?", and the only way to answer it is to put the same
template through Silhouette's own software and see what happens.  That also
makes this the reference implementation to capture traffic against, the same
way the Canon suite captured the Generic Plus UFR II driver in a VM.

What this module does **not** do is install Silhouette Studio.  That is a
signed, interactive, several-hundred-megabyte installer from
silhouetteamerica.com, and running an unattended install of third-party
software into someone's VM is not a thing to do on their behalf without
asking.  `prepare()` stages everything and prints the two commands to run;
`status()` reports whether Studio has appeared yet.

How the pieces connect:

* The host's home directory is bind-mounted into the container at `/shared`,
  and the container re-exports it to the guest over SMB.  So a file written
  under `$HOME` on the host is visible to Windows without any copying - the
  "shared directory" is the home directory, and `stage()` only has to put the
  template somewhere predictable inside it.
* `/dev/bus/usb` is passed into the container, so the cutter is reachable from
  the guest once Windows has a driver for it.
* WinBoat's guest agent answers on a host port and can list installed
  applications, which is how Studio is detected.
* Applications are launched as RDP RemoteApps with `xfreerdp3 /app:...`, which
  is how WinBoat itself starts them.

Credentials are read out of the user's own `~/.winboat/docker-compose.yml` at
the moment they are needed and are never written to a file, a log line or an
error message by this module.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shutil
import signal
import subprocess
import urllib.error
import urllib.request

CONTAINER = "WinBoat"
WINBOAT_HOME = pathlib.Path.home() / ".winboat"
COMPOSE = WINBOAT_HOME / "docker-compose.yml"

# Where files are staged for the guest to read.
#
# The constraint is that WinBoat bind-mounts the whole of $HOME into the
# container and re-exports it, so the guest can only see paths under $HOME.
# This originally put a `cut-proxies/` directory at the top of the home
# directory, which works but litters it.
#
# The project itself usually lives under $HOME too - `~/Projects/Printing/
# cut-proxies` - and anything inside it is therefore equally visible to the
# guest. So stage inside the project by default and keep the home directory
# clean. The fallback only applies when the checkout is somewhere the guest
# cannot see, where there is no choice.
STAGE_DIRNAME = "guest-staging"
STAGE_FALLBACK_DIRNAME = "cut-proxies"

# What the guest calls the share.  dockur/windows exports the bind mount over
# SMB under this name; it is overridable because it is the one value here that
# depends on the container image rather than on this project.
GUEST_SHARE = os.environ.get("CUT_PROXIES_GUEST_SHARE", r"\\host.lan\Data")

# Where Silhouette Studio installs itself.  Both are checked because the
# installer has used each.
STUDIO_PATHS = (
    r"C:\Program Files\Silhouette America\Silhouette Studio\Silhouette Studio.exe",
    r"C:\Program Files (x86)\Silhouette America\Silhouette Studio\Silhouette Studio.exe",
)

STUDIO_DOWNLOAD = "https://www.silhouetteamerica.com/software"


@dataclasses.dataclass
class Guest:
    """What is known about the Windows guest right now."""

    container_running: bool = False
    agent_port: int | None = None
    agent_version: str | None = None
    rdp_port: int | None = None
    share_host_path: pathlib.Path | None = None
    studio_path: str | None = None
    # Whether we were able to ASK the guest what it has installed. A guest
    # agent that is not answering is not the same thing as Studio being
    # absent, and reporting the second when it is the first sends people off
    # reinstalling software they already have.
    studio_known: bool = False
    usb_passthrough: bool = False
    rdp_in_use: str = ""
    rdp_reachable: bool = False
    detail: list[str] = dataclasses.field(default_factory=list)

    @property
    def ready_for_studio(self) -> bool:
        return bool(self.container_running and self.studio_path
                    and self.rdp_port and self.rdp_reachable)

    @property
    def studio_state(self) -> str:
        if self.studio_path:
            return self.studio_path
        if not self.studio_known:
            return "unknown - could not ask the guest"
        return "not installed"

    def why_not_ready(self) -> str:
        """One sentence on what is missing, specific enough to act on."""
        if not self.container_running:
            return "the WinBoat container is not running"
        if not self.rdp_port:
            return "the container publishes no RDP port"
        if not self.rdp_reachable:
            return (f"nothing is listening on the guest's RDP port "
                    f"{self.rdp_port}; the container may have just been "
                    f"recreated, which moves the published port")
        if not self.studio_path:
            if not self.studio_known:
                return ("the guest agent is not answering, so whether "
                        "Silhouette Studio is installed could not be "
                        "determined; Windows may still be booting")
            return "Silhouette Studio is not installed in the guest"
        return ""


def _docker() -> str | None:
    for name in ("docker", "podman"):
        if shutil.which(name):
            return name
    return None


def _inspect() -> dict | None:
    exe = _docker()
    if not exe:
        return None
    out = subprocess.run([exe, "inspect", CONTAINER],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return None
    return data[0] if data else None


def _port(info: dict, container_port: str) -> int | None:
    """The host port a container port is published on."""
    ports = (info.get("NetworkSettings") or {}).get("Ports") or {}
    for key, bindings in ports.items():
        if key.startswith(container_port + "/") and bindings:
            try:
                return int(bindings[0]["HostPort"])
            except (KeyError, ValueError):
                continue
    return None


def port_open(port: int, host: str = "127.0.0.1",
              timeout: float = 2.0) -> bool:
    """Whether anything is listening. A published port is not a live one.

    WinBoat picks the host port when the container is created, so recreating
    it moves RDP and the agent to new numbers. Everything here rediscovers
    them each time rather than remembering, but a port that was correct a
    moment ago can still be dead, and connecting to a closed port fails in a
    way that looks like the guest refusing the job.
    """
    import socket
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def _agent(port: int, path: str, timeout: float = 5.0):
    url = f"http://127.0.0.1:{port}/{path.lstrip('/')}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def status() -> Guest:
    """Look at the guest and report, without changing anything."""
    g = Guest()
    info = _inspect()
    if info is None:
        g.detail.append(
            f"No {CONTAINER} container found (is WinBoat installed and is the "
            f"container runtime reachable?)")
        return g

    g.container_running = bool((info.get("State") or {}).get("Running"))
    if not g.container_running:
        g.detail.append(f"The {CONTAINER} container exists but is not "
                        f"running; start WinBoat first.")

    g.agent_port = _port(info, "7148")
    g.rdp_port = _port(info, "3389")

    for mount in info.get("Mounts") or []:
        if mount.get("Destination") == "/shared":
            g.share_host_path = pathlib.Path(mount["Source"])
        if mount.get("Destination") == "/dev/bus/usb":
            g.usb_passthrough = True
    if not g.usb_passthrough:
        g.detail.append(
            "/dev/bus/usb is not passed into the container, so the guest "
            "cannot see the cutter even once Studio is installed.")

    if g.container_running and g.rdp_port:
        g.rdp_reachable = port_open(g.rdp_port)
        if not g.rdp_reachable:
            g.detail.append(
                f"nothing is listening on RDP port {g.rdp_port}; if the "
                f"container was just recreated the published port moves, and "
                f"this is rediscovered rather than remembered, so try again "
                f"once Windows is up.")
        g.rdp_in_use = rdp_busy(g.rdp_port)
        if g.rdp_in_use:
            g.detail.append(
                f"{g.rdp_in_use}; anything launched into the guest will do "
                f"nothing until it is closed.")

    if g.container_running and g.agent_port:
        health = _agent(g.agent_port, "health")
        if health is None:
            g.detail.append(
                f"The guest agent on port {g.agent_port} is not answering; "
                f"Windows may still be booting.")
        else:
            version = _agent(g.agent_port, "version") or {}
            g.agent_version = version.get("version")
            apps = _agent(g.agent_port, "apps", timeout=15.0)
            g.studio_known = apps is not None
            for app in (apps or []):
                name = (app.get("Name") or "").lower()
                path = app.get("Path") or ""
                if "silhouette" in name or "silhouette" in path.lower():
                    g.studio_path = path
                    break
            if g.studio_path is None and g.studio_known:
                g.detail.append(
                    "Silhouette Studio is not installed in the guest. "
                    "cut-proxies will not install it for you; see "
                    "docs/SETUP.md.")
            elif g.studio_path is None:
                g.detail.append(
                    "Could not list the guest's applications, so whether "
                    "Studio is installed is unknown.")
    return g


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------

def stage_dir() -> pathlib.Path:
    """The host directory the guest can see, preferring one inside the repo.

    `CUT_PROXIES_STAGE` overrides. Otherwise this is `guest-staging/` in the
    project, as long as the project is somewhere the guest can actually
    reach; if the checkout sits outside the shared tree there is no choice
    but a directory at the top of the share.
    """
    g = status()
    base = (g.share_host_path or pathlib.Path.home()).resolve()

    override = os.environ.get("CUT_PROXIES_STAGE")
    if override:
        folder = pathlib.Path(override).expanduser().resolve()
    else:
        in_project = (pathlib.Path(__file__).resolve().parent.parent
                      / STAGE_DIRNAME)
        try:
            in_project.relative_to(base)
            folder = in_project
        except ValueError:
            folder = base / STAGE_FALLBACK_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def guest_path(host_path) -> str:
    """Translate a staged host path to what Windows will call it."""
    host_path = pathlib.Path(host_path).resolve()
    g = status()
    base = (g.share_host_path or pathlib.Path.home()).resolve()
    try:
        rel = host_path.relative_to(base)
    except ValueError:
        raise ValueError(
            f"{host_path} is not inside {base}, which is the only directory "
            f"the guest can see. Stage the file with stage() first.") from None
    return GUEST_SHARE + "\\" + str(rel).replace("/", "\\")


def stage(*paths) -> list[tuple[pathlib.Path, str]]:
    """Copy templates into the share and return (host, guest) path pairs."""
    folder = stage_dir()
    out = []
    for p in paths:
        src = pathlib.Path(p).resolve()
        dest = folder / src.name
        if src != dest:
            shutil.copy2(src, dest)
        out.append((dest, guest_path(dest)))
    return out


# --------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------

def _credentials() -> tuple[str, str]:
    """Read the guest login out of the user's WinBoat compose file.

    Returned, never logged.  Callers pass these straight to xfreerdp and must
    not put them in a message.
    """
    user = os.environ.get("CUT_PROXIES_GUEST_USER")
    password = os.environ.get("CUT_PROXIES_GUEST_PASSWORD")
    if user and password:
        return user, password
    if not COMPOSE.is_file():
        raise RuntimeError(
            f"No guest credentials. Set CUT_PROXIES_GUEST_USER and "
            f"CUT_PROXIES_GUEST_PASSWORD, or let {COMPOSE} provide them.")
    found: dict[str, str] = {}
    for line in COMPOSE.read_text().splitlines():
        line = line.strip()
        for key in ("USERNAME:", "PASSWORD:"):
            if line.startswith(key):
                found[key.rstrip(":")] = line[len(key):].strip().strip('"\'')
    if "USERNAME" not in found or "PASSWORD" not in found:
        raise RuntimeError(
            f"Could not read USERNAME and PASSWORD from {COMPOSE}; set "
            f"CUT_PROXIES_GUEST_USER and CUT_PROXIES_GUEST_PASSWORD instead.")
    return found["USERNAME"], found["PASSWORD"]


def freerdp() -> str:
    for name in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "xfreerdp is not installed; it is how an application in the guest is "
        "opened on this desktop. Install freerdp.")


def _is_rdp_client(cmd: str) -> bool:
    """Whether a pgrep line is an xfreerdp *process*, not a mention of one.

    Matching anywhere in the command line is how a reaper ends up targeting
    a shell that is about to launch the client, a `timeout` wrapper, or -
    worst - itself. The prefix heuristic this replaced ("does it start with
    /bin/") passed a `python3 -c "...xfreerdp3..."` wrapper happily.

    So the test is the executable actually being run: the first token whose
    basename begins with `xfreerdp`, allowing one `timeout N` wrapper in
    front of it because that is how we launch.
    """
    parts = cmd.split()
    if not parts:
        return False
    if os.path.basename(parts[0]) == "timeout":
        # timeout [opts] <seconds> <cmd> ...
        parts = [p for p in parts[1:] if not p.startswith("-")]
        parts = parts[1:] if parts and parts[0].replace(".", "").isdigit() \
            else parts
    return bool(parts) and os.path.basename(parts[0]).startswith("xfreerdp")


def rdp_busy(port: int | None = None) -> str:
    """Whether something already holds the guest's RDP session.

    Windows serves one interactive session at a time, so a second connection
    does not queue - it silently does nothing, or it bumps the first one off
    mid-job.  This project found that out the hard way: a RemoteApp launched
    while another tool held the session produced no output and no error, and
    the time went into debugging the script instead of the session.

    Returns a description of the holder, or "" if the session is free. The
    description says whether it is one of ours, because the two cases want
    opposite responses: another tool's session must be left strictly alone,
    while a stale one of ours is ours to clean up.
    """
    port = port or (status().rdp_port or 3389)
    try:
        out = subprocess.run(["pgrep", "-af", "xfreerdp"], capture_output=True,
                             text=True, check=False).stdout
    except OSError:
        return ""
    mine = os.getpid()
    for line in out.splitlines():
        pid, _, cmd = line.partition(" ")
        if not pid.isdigit() or int(pid) == mine:
            continue
        # Only the actual client process, not a shell that mentions it.
        if f":{port}" in cmd and "/v:" in cmd:
            if _is_rdp_client(cmd):
                app = ""
                for part in cmd.split():
                    if part.startswith("/app:program:"):
                        app = part[len("/app:program:"):].split(",")[0]
                ours = RDP_MARKER in cmd
                return (f"pid {pid}"
                        + (f" running {app}" if app else "")
                        + (" (a cut-proxies session) " if ours
                           else " (not ours) ")
                        + "already holds the guest's RDP session")
    return ""


def our_stale_sessions(port: int | None = None) -> list[tuple[int, str]]:
    """Our own xfreerdp clients against the guest, as (pid, command).

    Matched strictly on our own `/wm-class` marker, so this can see a stray
    of ours and can never see - let alone touch - another tool's session.
    That boundary matters: a sibling session's long VM job was nearly lost
    to a second connection, and the fix there was not ours to make.
    """
    port = port or (status().rdp_port or 3389)
    out: list[tuple[int, str]] = []
    try:
        listing = subprocess.run(["pgrep", "-af", "xfreerdp"],
                                 capture_output=True, text=True,
                                 check=False).stdout
    except OSError:
        return out
    for line in listing.splitlines():
        pid, _, cmd = line.partition(" ")
        if not pid.isdigit():
            continue
        if RDP_MARKER in cmd and f":{port}" in cmd and "/v:" in cmd:
            if _is_rdp_client(cmd):
                out.append((int(pid), cmd))
    return out


def release_our_sessions(port: int | None = None,
                         dry_run: bool = False) -> list[int]:
    """Close our own stale RDP clients. Never touches anyone else's.

    A cleanup at the *end* of a run does not survive the run being cut
    short - the tooling can block the command and the output still reads as
    a normal completion, which is how two browsers leaked here for hours.
    So this is meant to be run at the start, where a leak from last time
    self-heals instead of persisting.
    """
    killed: list[int] = []
    for pid, _cmd in our_stale_sessions(port):
        if dry_run:
            killed.append(pid)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (OSError, ProcessLookupError):
            pass
    return killed


# Nothing is launched unbounded. A `timeout` in front of the client is the
# one protection in this file that does not depend on any cleanup running:
# the bound is set by the thing being launched, at launch, and holds even
# if the reap, the close and the whole rest of the session never happen.
# Two leaked browsers and a leaked server in this session all came from
# cleanups that silently did not execute.
SCRIPT_TIMEOUT_S = 600        # a helper script; minutes, not hours
INTERACTIVE_TIMEOUT_S = 4 * 60 * 60   # a Studio session someone is using


def _run_freerdp(args: list[str], user: str, password: str,
                 dry_run: bool = False,
                 timeout_s: int = SCRIPT_TIMEOUT_S) -> list[str]:
    """Start xfreerdp with the credentials kept out of the process list.

    `/args-from:fd:N` makes FreeRDP read its arguments from a pipe, one per
    line, so the password never appears in `ps`, in a file, or in this
    process's environment.  Passing it as `/p:...` on the command line - the
    obvious way, and what this did first - publishes it to every user on the
    machine for as long as the session lasts.
    """
    exe = freerdp()
    full = [f"/u:{user}", f"/p:{password}"] + args
    bound = ["timeout", str(timeout_s)] if shutil.which("timeout") else []
    shown = bound + [exe, "/args-from:fd:<pipe>", f"/u:{user}",
                     "/p:<from WinBoat config>"] + args
    if dry_run:
        return shown
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, ("\n".join(full) + "\n").encode())
    finally:
        os.close(write_fd)
    subprocess.Popen(
        bound + [exe, f"/args-from:fd:{read_fd}"],
        pass_fds=(read_fd,), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    os.close(read_fd)
    return shown


# The connection flags that actually work against this container.
#
# `/d:.` is the one that matters, and it is easy to miss. Without a domain,
# authentication against the guest's LOCAL account goes down the Kerberos
# path, fails with "Client's credentials have been revoked", and the
# connection is cancelled before any application starts. `/d:.` names the
# local machine and NLA then succeeds.
#
# Do not "fix" that by disabling NLA: `-sec-nla` is not a flag xfreerdp3
# accepts. It is rejected as an unexpected keyword, the client exits 23
# without ever connecting, and because nothing was attempted there is no
# connection error in the log - which reads exactly like success if you only
# check the return code. Two attempts were lost to that.
# `/wm-class` tags our own launches so a stale one of ours is
# distinguishable from another tool's live session. Without it `rdp_busy`
# can only say "something holds the guest", and the two cases want opposite
# responses: leave a foreign session strictly alone, clean up our own.
RDP_MARKER = "cut-proxies-guest"

RDP_FLAGS = ["/d:.", "/sec:nla", "/cert:ignore", "/log-level:ERROR",
             f"/wm-class:{RDP_MARKER}"]


def open_in_studio(template, dry_run: bool = False) -> list[str]:
    """Open a staged template in Silhouette Studio in the guest.

    Returns the command that was run (with the password replaced), so a caller
    can show it without leaking the credential.
    """
    g = status()
    if not g.ready_for_studio:
        raise RuntimeError("Cannot open Studio: " + g.why_not_ready())
    reaped = [] if dry_run else release_our_sessions(g.rdp_port)
    busy = rdp_busy(g.rdp_port)
    if busy and not dry_run:
        raise RuntimeError(
            f"{busy}. Windows serves one session at a time, so this would "
            f"either do nothing or bump that one off. Close it and try "
            f"again."
            + (f" (Cleared {len(reaped)} stale session(s) of our own first.)"
               if reaped else ""))
    host_path, win_path = stage(template)[0]
    user, password = _credentials()
    return _run_freerdp(
        [f"/v:127.0.0.1:{g.rdp_port}"] + RDP_FLAGS
        + [f"/app:program:{g.studio_path},cmd:\"{win_path}\""],
        user, password, dry_run, timeout_s=INTERACTIVE_TIMEOUT_S)


def run_script(guest_path: str, dry_run: bool = False) -> list[str]:
    """Run a PowerShell script already staged in the share, in the guest.

    Launched through `cmd.exe /c powershell -File ...` rather than
    powershell.exe directly: the RemoteApp argument string is parsed by
    Windows before PowerShell sees it, and a bare `-File \\\\host.lan\\...`
    UNC path does not survive that reliably, while cmd's `/c` does.

    Fire-and-forget. There is no way to read the output back over RDP, which
    is why every script this project stages writes a report into the share
    instead of printing one.
    """
    g = status()
    if not (g.container_running and g.rdp_port and g.rdp_reachable):
        raise RuntimeError("Cannot reach the guest: " + g.why_not_ready())
    # Reap our own strays FIRST. A cleanup at the end of a run does not
    # survive the run being cut short; doing it here means last time's leak
    # self-heals rather than blocking this run forever.
    reaped = [] if dry_run else release_our_sessions(g.rdp_port)
    busy = rdp_busy(g.rdp_port)
    if busy and not dry_run:
        raise RuntimeError(
            f"{busy}. Windows serves one session at a time, so this would "
            f"either do nothing or bump that one off. Close it and try "
            f"again."
            + (f" (Cleared {len(reaped)} stale session(s) of our own first.)"
               if reaped else ""))
    user, password = _credentials()
    return _run_freerdp(
        [f"/v:127.0.0.1:{g.rdp_port}"] + RDP_FLAGS
        + [f'/app:program:cmd.exe,cmd:/c powershell -ExecutionPolicy Bypass '
           f'-NoProfile -File {guest_path}'],
        user, password, dry_run)


def run_powershell(script: str, dry_run: bool = False) -> list[str]:
    """Run a short PowerShell snippet in the guest. See `run_script`."""
    g = status()
    if not (g.container_running and g.rdp_port):
        raise RuntimeError("The guest is not running.")
    busy = rdp_busy(g.rdp_port)
    if busy and not dry_run:
        raise RuntimeError(f"{busy}. Close it and try again.")
    user, password = _credentials()
    return _run_freerdp(
        [f"/v:127.0.0.1:{g.rdp_port}"] + RDP_FLAGS
        + [f'/app:program:cmd.exe,cmd:/c powershell -ExecutionPolicy Bypass '
           f'-NoProfile -Command {script}'],
        user, password, dry_run)


# --------------------------------------------------------------------------
# Preparing the guest
# --------------------------------------------------------------------------

# Placeholders are @NAME@, not str.format braces: this is a PowerShell
# script and it is full of { } that format() would try to interpret.
PREPARE_PS1 = r"""# cut-proxies: prepare the guest for Silhouette Studio.
# Read-only. It reports what the guest can see; it installs nothing.
$ErrorActionPreference = 'Continue'
$share  = '@SHARE@'
$work   = Join-Path $share 'cut-proxies'
$out    = Join-Path $work 'studio-extract'
$report = Join-Path $out 'guest-report.txt'

New-Item -ItemType Directory -Force -Path $out | Out-Null
function Say($m) { Add-Content -Path $report -Value $m }
Set-Content -Path $report -Value "cut-proxies guest report $(Get-Date -Format s)"
Say "share: $share"

# 1. Silhouette Studio
$studio = @(
  'C:\Program Files\Silhouette America\Silhouette Studio\Silhouette Studio.exe',
  'C:\Program Files (x86)\Silhouette America\Silhouette Studio\Silhouette Studio.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($studio) {
  $vi = (Get-Item $studio).VersionInfo
  Say "studio: $studio"
  Say "studio version: $($vi.FileVersion) / product $($vi.ProductVersion)"
} else {
  Say "studio: NOT installed - get it from @DOWNLOAD@"
}

# 2. Can Windows see the cutter?
$cutters = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
  Where-Object { $_.InstanceId -match 'VID_0B4D' -or $_.InstanceId -match 'VID_3844' }
if ($cutters) {
  foreach ($c in $cutters) { Say "cutter: $($c.FriendlyName) [$($c.Status)] $($c.InstanceId)" }
} else {
  Say "cutter: no Graphtec (VID_0B4D) or Cameo 5 Alpha (VID_3844) device present"
  Say "cutter: check it is powered on and that WinBoat passes the USB device through"
}

# 3. Studio's own data files, copied out so Linux can read them. This is
#    where its material cut settings live, which is the one thing that could
#    move this project's blade presets off being extrapolations.
Say ""
Say "=== data files ==="
$budget = 40MB; $used = 0
foreach ($root in @('C:\Program Files\Silhouette America', $env:APPDATA, $env:LOCALAPPDATA,
                    (Join-Path $env:USERPROFILE 'Documents'))) {
  if (-not $root -or -not (Test-Path $root)) { continue }
  Get-ChildItem $root -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'Silhouette' -and $_.Length -lt 8MB -and
                   $_.Extension -match '^\.(xml|json|db|sqlite|slst|dat|txt|ini|cfg|config|csv)$' } |
    ForEach-Object {
      Say ("  {0,10}  {1}" -f $_.Length, $_.FullName)
      if ($used + $_.Length -lt $budget) {
        $safe = ($_.FullName -replace '[:\\/]', '_')
        Copy-Item $_.FullName (Join-Path $out $safe) -ErrorAction SilentlyContinue
        $used += $_.Length
      }
    }
}
Say "copied $used bytes"
Say "done"
"""



def prepare(dry_run: bool = False) -> dict:
    """Stage the guest-side helper script and say what to do next."""
    folder = stage_dir()
    script = folder / "prepare-guest.ps1"
    script.write_text(PREPARE_PS1.replace("@SHARE@", GUEST_SHARE)
                                 .replace("@DOWNLOAD@", STUDIO_DOWNLOAD))
    g = status()
    steps = [
        f"The helper script is at {script}",
        f"Inside Windows it is {guest_path(script)}",
        "Run it from here with: cut-proxies studio --run-helper",
        "or open PowerShell in the guest yourself and run:",
        f"  powershell -ExecutionPolicy Bypass -File \"{guest_path(script)}\"",
        f"Either way it writes a report to {folder / 'guest-report.txt'}",
    ]
    busy = rdp_busy(g.rdp_port) if g.rdp_port else ""
    if busy:
        steps.append(
            f"Note: {busy}, so --run-helper will refuse until it is closed.")
    if g.studio_path:
        steps.append(f"Silhouette Studio is already installed at "
                     f"{g.studio_path}; `cut-proxies cut --via studio` can "
                     f"open a template in it.")
    else:
        steps.append(f"Silhouette Studio still has to be installed by hand, "
                     f"from {STUDIO_DOWNLOAD}. cut-proxies will not install "
                     f"it for you.")
    return {"script": script, "guest_script": guest_path(script),
            "report": folder / "studio-extract" / "guest-report.txt",
            "extract": folder / "studio-extract", "steps": steps,
            "guest": g}
