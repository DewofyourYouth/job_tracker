"""Track jobs you have actually applied to."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

APPLICATIONS_CSV = Path("data/applications.csv")

FIELDNAMES = ["Company", "Job Title", "URL", "Applied Date", "Status", "Notes", "Source"]

VALID_STATUSES = {
    "applied",
    "phone_screen",
    "interview",
    "offer",
    "rejected",
    "withdrew",
}

STATUS_STYLE = {
    "applied": "cyan",
    "phone_screen": "blue",
    "interview": "yellow",
    "offer": "green",
    "rejected": "red",
    "withdrew": "dim",
}


def _load() -> list[dict]:
    if not APPLICATIONS_CSV.exists():
        return []
    with APPLICATIONS_CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict]) -> None:
    APPLICATIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with APPLICATIONS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _find_listing(url: str) -> dict:
    listings_csv = Path("data/listings.csv")
    if not listings_csv.exists():
        return {}
    with listings_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("Url") == url:
                return row
    return {}


def record_application(url: str, company: str = "", title: str = "", notes: str = "", source: str = "") -> bool:
    """Add an application record. Returns True if newly added, False if already exists."""
    rows = _load()
    for row in rows:
        if row["URL"] == url:
            return False

    listing = _find_listing(url)
    rows.append({
        "Company": company or listing.get("Company", ""),
        "Job Title": title or listing.get("Job Title", ""),
        "URL": url,
        "Applied Date": date.today().isoformat(),
        "Status": "applied",
        "Notes": notes or "",
        "Source": source,
    })
    _save(rows)
    return True


@click.group("track")
def track_command():
    """Track and manage job applications."""


@track_command.command("add")
@click.argument("url", required=False)
@click.option("--company", default="", help="Company name (auto-filled from listings.csv if found).")
@click.option("--title", default="", help="Job title (auto-filled from listings.csv if found).")
@click.option("--notes", default="", help="Any notes about this application.")
@click.option("--date", "applied_date", default=None, help="Date applied (YYYY-MM-DD). Defaults to today.")
@click.option("--source", default="", help="Where you found the job (e.g. LinkedIn, WeWorkRemotely).")
@click.option("--manual", "-m", is_flag=True, help="Manually enter all fields (for jobs applied outside the tracker).")
def add_command(url, company, title, notes, applied_date, source, manual):
    """Record a job you applied to.

    URL is optional — omit to pick interactively from listings.csv.
    Use -m / --manual to enter any job by hand (e.g. one you found on LinkedIn).
    """
    if manual or (not url and not company and not title):
        # prompt for all fields
        url = url or click.prompt("Job URL (or leave blank)", default="")
        if url:
            listing = _find_listing(url)
            company = company or listing.get("Company", "")
            title = title or listing.get("Job Title", "")
        company = company or click.prompt("Company")
        title = title or click.prompt("Job title")
        notes = notes or click.prompt("Notes (optional)", default="")
    elif not url:
        from commands.apply import _pick_listing_interactively
        listing = _pick_listing_interactively()
        if listing is None:
            raise click.Abort()
        url = listing.get("Url", "")
        company = company or listing.get("Company", "")
        title = title or listing.get("Job Title", "")
    else:
        listing = _find_listing(url)
        company = company or listing.get("Company", "")
        title = title or listing.get("Job Title", "")

    rows = _load()
    for row in rows:
        if row["URL"] == url:
            console.print(f"[yellow]Already tracked:[/] {row['Company']} — {row['Job Title']}")
            return

    rows.append({
        "Company": company,
        "Job Title": title,
        "URL": url,
        "Applied Date": applied_date or date.today().isoformat(),
        "Status": "applied",
        "Notes": notes,
        "Source": source,
    })
    _save(rows)
    label = f"{company} — {title}" if company or title else url
    console.print(f"[green]✓[/] Recorded application: [bold]{label}[/]")


@track_command.command("update")
@click.argument("url", required=False)
@click.option(
    "--status",
    type=click.Choice(sorted(VALID_STATUSES), case_sensitive=False),
    default=None,
    help="New status.",
)
@click.option("--notes", default=None, help="Replace notes (or append with --append-notes).")
@click.option("--append-notes", default=None, help="Append text to existing notes.")
def update_command(url, status, notes, append_notes):
    """Update the status or notes for a tracked application."""
    rows = _load()
    if not rows:
        console.print("[yellow]No tracked applications yet. Use [bold]job track add[/].[/]")
        return

    if not url:
        _print_table(rows)
        choice = click.prompt("Enter row number to update", type=int)
        if not (1 <= choice <= len(rows)):
            console.print("[red]Invalid selection.[/]")
            return
        target = rows[choice - 1]
    else:
        matches = [r for r in rows if r["URL"] == url]
        if not matches:
            console.print(f"[red]URL not found in tracked applications.[/]")
            return
        target = matches[0]

    if status is None:
        status = click.prompt(
            "New status",
            type=click.Choice(sorted(VALID_STATUSES), case_sensitive=False),
            default=target.get("Status", "applied"),
        )

    target["Status"] = status.lower()

    if notes is not None:
        target["Notes"] = notes
    if append_notes:
        existing = target.get("Notes", "")
        target["Notes"] = (existing + "; " + append_notes).lstrip("; ")

    _save(rows)
    label = f"{target['Company']} — {target['Job Title']}" if target.get("Company") else target["URL"]
    console.print(f"[green]✓[/] Updated [bold]{label}[/] → status: [bold]{status}[/]")


@track_command.command("list")
@click.option("--status", default=None, help="Filter by status.")
def list_command(status):
    """List all tracked job applications."""
    rows = _load()
    if not rows:
        console.print("[yellow]No tracked applications yet. Use [bold]job track add[/].[/]")
        return

    if status:
        rows = [r for r in rows if r.get("Status", "").lower() == status.lower()]
        if not rows:
            console.print(f"[yellow]No applications with status '{status}'.[/]")
            return

    _print_table(rows, show_index=False)
    console.print(f"\n[dim]{len(rows)} application(s) tracked.[/]")


def _print_table(rows: list[dict], show_index: bool = True) -> None:
    table = Table(show_lines=False, header_style="bold")
    if show_index:
        table.add_column("#", style="dim", width=3)
    table.add_column("Company", min_width=16)
    table.add_column("Job Title", min_width=24)
    table.add_column("Applied", width=11)
    table.add_column("Status", width=12)
    table.add_column("Source", width=14)
    table.add_column("Notes", min_width=20)

    for i, row in enumerate(rows, 1):
        status = row.get("Status", "")
        style = STATUS_STYLE.get(status, "")
        status_markup = f"[{style}]{status}[/]" if style else status
        notes = (row.get("Notes") or "")[:50]

        cols = [
            row.get("Company", ""),
            row.get("Job Title", ""),
            row.get("Applied Date", ""),
            status_markup,
            row.get("Source", ""),
            notes,
        ]
        if show_index:
            table.add_row(str(i), *cols)
        else:
            table.add_row(*cols)

    console.print(table)
