from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from agents.orchestrator import orchestrate
from config import get_settings, get_user_profile

app = typer.Typer(help="Caerus job application CLI")
console = Console()


@app.command()
def run(
    url_or_text: str | None = typer.Argument(default=None),
    no_notion: bool = typer.Option(default=False, help="Skip Notion logging"),
    print_cover_letter: bool = typer.Option(default=False, help="Print generated cover letter body"),
) -> None:
    value = url_or_text or typer.prompt("Paste job URL or JD text")
    try:
        package = orchestrate(value, skip_notion=no_notion)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    table = Table(title="Caerus Application Summary")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Company", package.jd.company or "Unknown")
    table.add_row("Role", package.jd.role or "Unknown")
    table.add_row("Location", package.jd.location or "Unknown")
    table.add_row("Stage", str(package.company_brief.stage) if package.company_brief else "Unknown")
    table.add_row("Fit Score", str(package.company_brief.fit_score) if package.company_brief else "Unknown")
    table.add_row("Resume Variant", str(package.resume_selection.variant) if package.resume_selection else "Unknown")
    table.add_row("Resume Grade", package.resume_selection.grade if package.resume_selection else "Unknown")
    table.add_row("Cover Letter Word Count", str(package.cover_letter.word_count) if package.cover_letter else "0")
    table.add_row("Hook Used", package.cover_letter.hook_summary if package.cover_letter else "")
    table.add_row("Output Dir", package.output_dir or "")
    table.add_row("Notion URL", package.notion_url or "")
    console.print(table)

    if print_cover_letter and package.cover_letter:
        console.print("\n[bold]Cover Letter[/bold]\n")
        console.print(package.cover_letter.body)


@app.command()
def check() -> None:
    settings = get_settings()
    checks = {
        "GEMINI_API_KEY": bool(settings.gemini_api_key),
        "SERPER_API_KEY": bool(settings.serper_api_key),
        "TAVILY_API_KEY": bool(settings.tavily_api_key),
        "NOTION_TOKEN": bool(settings.notion_token),
        "NOTION_DATABASE_ID": bool(settings.notion_database_id),
        "LANGFUSE_PUBLIC_KEY": bool(settings.langfuse_public_key),
        "LANGFUSE_SECRET_KEY": bool(settings.langfuse_secret_key),
    }
    for key, ok in checks.items():
        console.print(f"{'✅' if ok else '❌'} {key}")


@app.command()
def profile() -> None:
    profile_data = get_user_profile()
    console.print_json(json.dumps(profile_data))


if __name__ == "__main__":
    app()
