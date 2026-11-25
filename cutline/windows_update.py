"""Fetch and verify Cutline's Windows release installer."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

RELEASES = "https://github.com/zoro11031/cutline/releases"
LATEST = "https://api.github.com/repos/zoro11031/cutline/releases/latest"
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com"}


@dataclass(frozen=True)
class Update:
    version: str
    filename: str
    size: int
    digest: str

    @property
    def url(self):
        return f"{RELEASES}/download/v{self.version}/{self.filename}"


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != "https" or target.hostname not in ASSET_HOSTS
                or urlsplit(req.full_url).hostname == "api.github.com"):
            raise ValueError("GitHub returned an unexpected download location.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url):
    return build_opener(ReleaseRedirect()).open(
        Request(url, headers={"User-Agent": "Cutline Windows updater"}), timeout=15)


def version(text):
    if not isinstance(text, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,8})(\.(0|[1-9][0-9]{0,8})){2}", text):
        raise ValueError("GitHub returned an invalid release version.")
    return tuple(map(int, text.split(".")))


def check(current):
    with request(LATEST) as response:
        release = json.loads(response.read(2 * 1024 * 1024))
    if not isinstance(release, dict):
        raise ValueError("GitHub returned invalid release details.")
    tag = release.get("tag_name")
    if release.get("draft") or release.get("prerelease"):
        return None
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("GitHub returned an invalid release version.")
    if version(tag[1:]) <= version(current):
        return None
    name = f"Cutline-{tag[1:]}-Windows-x86_64-Setup.exe"
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("GitHub returned invalid release assets.")
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        size, digest = asset.get("size"), asset.get("digest")
        if (type(size) is int and size > 0 and isinstance(digest, str)
                and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest)):
            return Update(tag[1:], name, size, digest[7:].lower())
    raise ValueError("This release has no verifiable Windows installer.")


def verified(path, update):
    try:
        with path.open("rb") as source:
            return (os.fstat(source.fileno()).st_size == update.size
                    and hashlib.file_digest(source, "sha256").hexdigest() == update.digest)
    except FileNotFoundError:
        return False


def download(update, cache, cancelled, progress):
    from tempfile import NamedTemporaryFile

    cache.mkdir(parents=True, exist_ok=True)
    target = cache / update.filename
    if verified(target, update):
        return None if cancelled.is_set() else target
    with NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as output:
        partial = Path(output.name)
        try:
            with request(update.url) as response:
                count = 0
                while not cancelled.is_set():
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > update.size:
                        raise ValueError("The installer is larger than expected.")
                    output.write(chunk)
                    progress(count)
            output.close()
            if cancelled.is_set():
                return None
            if not verified(partial, update):
                raise ValueError("The installer download could not be verified. Download it again.")
            if os.name == "nt":
                Path(f"{partial}:Zone.Identifier").write_text(
                    f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={update.url}\r\n", newline="")
            if cancelled.is_set():
                return None
            partial.replace(target)
            return target
        finally:
            output.close()
            partial.unlink(missing_ok=True)


def installed():
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Cutline",
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            location, _ = winreg.QueryValueEx(key, "InstallLocation")
        return (Path(location) / "app").resolve() == Path(__file__).resolve().parents[1]
    except (OSError, TypeError, ValueError):
        return False
