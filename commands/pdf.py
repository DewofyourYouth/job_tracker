"""Re-export an existing application HTML file to PDF via Playwright."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from commands.apply import html_to_pdf

console = Console()


APPLICATIONS_DIR = Path("output/applications")


@click.command("repdf")
@click.argument("html_file", type=click.Path(path_type=Path))
def repdf_command(html_file: Path) -> None:
    """Convert an existing CV or cover letter HTML file to PDF.

    Useful after manually editing the HTML — re-runs just the Playwright
    PDF export step without regenerating content via the LLM.

    Paths are resolved relative to output/applications/ if not found directly.
    """
    if not html_file.exists():
        html_file = APPLICATIONS_DIR / html_file
    if not html_file.exists():
        raise click.BadParameter(f"File not found: {html_file}")

    pdf_path = html_file.with_suffix(".pdf")
    console.print(f"[bold]Exporting:[/] {html_file} → {pdf_path}")

    is_cover_letter = "cover-letter" in html_file.name
    if is_cover_letter:
        ok = html_to_pdf(html_file, pdf_path, margin_top="18mm", margin_bottom="18mm", margin_left="20mm", margin_right="20mm")
    else:
        ok = html_to_pdf(html_file, pdf_path)

    if ok:
        console.print(f"  [green]✓[/] PDF → [bold]{pdf_path}[/]")
    else:
        console.print("  [red]PDF export failed.[/]")
