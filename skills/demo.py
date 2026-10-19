"""Explicit offline replay backend, with no settings, network or personal files."""
from __future__ import annotations

import json
from contextlib import nullcontext
from importlib.resources import files
from pathlib import Path
from tempfile import mkdtemp

from schemas.models import ApplicationPackage
from skills.output_writer import _write_company_brief, _write_resume_report


class DemoReplay:
    trace_id = None
    trace_url = None

    def __init__(self, output_root: Path):
        self.data = files("skills").joinpath("demo_data")
        self.saved = ApplicationPackage.model_validate_json(self.data.joinpath("responses.json").read_text(encoding="utf-8"))
        self.profile = json.loads(self.data.joinpath("profile.json").read_text(encoding="utf-8"))
        self.output_root = output_root

    def step(self, name):
        return nullcontext(self)

    def set_output(self, output):
        pass

    def parse_jd(self, text):
        return self.saved.jd.model_copy(update={"raw_text": text})

    async def research_company(self, jd):
        return self.saved.company_brief.model_copy(deep=True)

    def select_resume(self, jd, brief):
        return self.saved.resume_selection.model_copy(deep=True)

    def generate_cover_letter(self, jd, brief, selection):
        return self.saved.cover_letter.model_copy(deep=True)

    def write_outputs(self, package):
        self.output_root.mkdir(parents=True, exist_ok=True)
        destination = Path(mkdtemp(prefix="demo-", dir=self.output_root))
        package.output_dir = str(destination)
        package.company_brief_path = str(_write_company_brief(package, destination))
        package.resume_report_path = str(_write_resume_report(package, destination))
        letter = destination / "cover_letter.md"
        letter.write_text(
            f"# {self.profile['name']}\n\n{package.cover_letter.body}\n\nSincerely,\n{self.profile['name']}\n",
            encoding="utf-8",
        )
        package.cover_letter_path = str(letter)
        for name in ("profile.json", "job.txt", "responses.json"):
            destination.joinpath(name).write_text(self.data.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
        return package


async def run_demo(output_root: Path) -> ApplicationPackage:
    from agents.orchestrator import orchestrate

    replay = DemoReplay(output_root)
    package = await orchestrate(replay.data.joinpath("job.txt").read_text(encoding="utf-8"), replay=replay)
    Path(package.output_dir, "application.json").write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return package
