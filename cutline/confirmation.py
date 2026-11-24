"""Shared confirmation model for cutting and scanning in Qt and GTK frontends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .job import Job


@dataclass(frozen=True)
class SendConfirmation:
    title: str
    lead: str
    verification: str
    warnings: tuple[str, ...]
    settings: tuple[str, ...]
    connection: str | None
    confirm_label: str
    destructive: bool

    @property
    def summary(self) -> str:
        blocks = [self.lead]
        if self.warnings:
            blocks.append("\n".join(f"⚠ {w}" for w in self.warnings))
        return "\n\n".join(blocks)

    @property
    def details(self) -> str:
        blocks = [self.verification]
        if self.settings:
            blocks.append("\n".join(self.settings))
        if self.connection:
            blocks.append(f"Connection: {self.connection}")
        return "\n\n".join(blocks)

    @property
    def body(self) -> str:
        blocks = [f"{self.lead}\n\n{self.verification}"]
        if self.warnings:
            blocks.append("\n".join(self.warnings))
        if self.settings:
            blocks.append("\n".join(self.settings))
        if self.connection:
            blocks.append(f"Connection: {self.connection}")
        return "\n\n".join(blocks)


from .bluetooth import BLUETOOTH_MODES


def endpoint_text(mode: str, endpoint) -> str:
    """A connection as a person reads it: never a raw tuple."""
    if isinstance(endpoint, (tuple, list)) and len(endpoint) == 2:
        return f"USB bus {endpoint[0]}, device {endpoint[1]}"
    if mode in BLUETOOTH_MODES:
        return f"{BLUETOOTH_MODES[mode]} {endpoint}"
    return f"USB printer {endpoint}"


def verification_statement(job: Job) -> str:
    count = len(job.paths)
    unit = "path" if count == 1 else "paths"
    return f"{count} {unit} decoded and measured inside the sheet."


def build_send_confirmation(
    plan: Job,
    connection: tuple[str, str | None, ...],
    *,
    scan_only: bool = False,
    bundle=None,
    artwork=None,
) -> SendConfirmation:
    title = "Test registration?" if scan_only else "Send this cut?"
    lead = (
        "This test ends after the cutter reports the scan result."
        if scan_only
        else "This moves the blade."
    )
    verification = verification_statement(plan)

    warnings: list[str] = []
    settings: list[str] = []

    # 1. Warnings first
    used = [group.preset for group in plan.action_groups] or [plan.preset]
    if not scan_only and any(preset.origin != "measured" for preset in used):
        warnings.append("The selected settings need a hardware test on this stock.")

    if plan.registration:
        if not plan.auto_search:
            warnings.append("Position the cutter head over the first mark before starting the scan.")
    else:
        warnings.append("Registration is off; this uses sheet coordinates.")

    if bundle and hasattr(bundle, "mat_placement"):
        placement = bundle.mat_placement()
        if placement:
            warnings.append(placement)

    if artwork is not None and getattr(artwork, "studio3_page", False):
        warnings.append("Check the imported outlines and match the registration settings to the printed Studio3 sheet.")

    if scan_only:
        warnings.append("This scans the printed marks without sending cut paths.")

    # 2. Settings next
    settings.append(plan.grid.describe())
    if plan.action_groups:
        settings.append("\n".join(f"{i + 1}. {group.key} · {group.describe()}"
                                  for i, group in enumerate(plan.action_groups)))
    else:
        passes = "pass" if plan.settings.passes == 1 else "passes"
        settings.append(f"{plan.preset.name}: force {plan.settings.force}, speed {plan.settings.speed}, {plan.settings.passes} {passes}")

    dx, dy = plan.offset_mm
    if dx or dy:
        settings.append(f"Cutter offset: X {dx:+.2f} mm, Y {dy:+.2f} mm.")

    # 3. Connection
    conn_text = None
    if len(connection) > 1 and connection[1]:
        conn_text = f"{plan.device.name} · {endpoint_text(connection[0], connection[1])}"

    confirm_label = "Scan" if scan_only else "Cut"
    destructive = not scan_only

    return SendConfirmation(
        title=title,
        lead=lead,
        verification=verification,
        warnings=tuple(warnings),
        settings=tuple(settings),
        connection=conn_text,
        confirm_label=confirm_label,
        destructive=destructive,
    )
