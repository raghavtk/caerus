from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from jinja2 import Template
from loguru import logger

from config import get_settings, get_user_profile
from schemas.models import ApplicationPackage


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "unknown"


def _make_output_dir(company: str, role: str, outputs_dir: str) -> Path:
    date_part = datetime.now().strftime("%Y%m%d")
    c = _slugify(company)
    r = _slugify(role)
    suffix = f"{c}_{r}"[:80]
    out_dir = Path(outputs_dir) / f"{date_part}_{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _write_cover_letter_pdf(package: ApplicationPackage, out_dir: Path) -> Path:
    profile = get_user_profile()
    candidate_name = profile.get("name", "Candidate")
    today = datetime.now().strftime("%B %d, %Y")
    body = (package.cover_letter.body if package.cover_letter else "").strip()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

    html_template = Template(
        """
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.55; color: #111;">
            <p>{{ date }}</p>
            <p>{{ name }}</p>
            {% for para in paragraphs %}
              <p>{{ para }}</p>
            {% endfor %}
          </body>
        </html>
        """
    )
    html = html_template.render(date=today, name=candidate_name, paragraphs=paragraphs)
    pdf_path = out_dir / "cover_letter.pdf"
    try:
        from weasyprint import HTML

        HTML(string=html).write_pdf(str(pdf_path))
        return pdf_path
    except Exception as exc:
        logger.warning("weasyprint failed; writing txt fallback: {}", exc)
        txt_path = out_dir / "cover_letter.txt"
        txt_path.write_text(body, encoding="utf-8")
        return txt_path


def _write_company_brief(package: ApplicationPackage, out_dir: Path) -> Path:
    path = out_dir / "company_brief.md"
    brief = package.company_brief
    lines = [
        f"# Company Brief: {brief.company}",
        f"- Stage: {brief.stage}",
        f"- Fit Score: {brief.fit_score}",
        f"- Sponsorship: {brief.sponsorship}",
        "",
        "## Strong Overlaps",
        *[f"- {x}" for x in brief.strong_overlaps],
        "",
        "## Potential Angles",
        *[f"- {x}" for x in brief.potential_angles],
        "",
        "## Tech Highlights",
        *[f"- {x}" for x in brief.tech_highlights],
        "",
        "## Culture Notes",
        *[f"- {x}" for x in brief.culture_notes],
        "",
        "## Sources",
        *[f"- {x}" for x in brief.sources],
    ]
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def _write_resume_report(package: ApplicationPackage, out_dir: Path) -> Path:
    path = out_dir / "resume_report.md"
    sel = package.resume_selection
    lines = [
        "# Resume Selection Report",
        f"- Variant: {sel.variant}",
        f"- Grade: {sel.grade}",
        f"- Fit Score: {sel.fit_score}",
        f"- Selected Resume Path: {sel.selected_resume_path}",
        "",
        "## Strengths",
        *[f"- {x}" for x in sel.strengths],
        "",
        "## Gaps",
        *[f"- {x}" for x in sel.gaps],
        "",
        "## Talking Points",
        *[f"- {x}" for x in sel.talking_points],
    ]
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return path


def write_outputs(package: ApplicationPackage) -> ApplicationPackage:
    settings = get_settings()
    company = package.jd.company or "unknown_company"
    role = package.jd.role or "unknown_role"
    out_dir = _make_output_dir(company, role, settings.outputs_dir)

    cover_letter_path = _write_cover_letter_pdf(package, out_dir)
    company_brief_path = _write_company_brief(package, out_dir)
    resume_report_path = _write_resume_report(package, out_dir)

    selected_copy: Path | None = None
    src = package.resume_selection.selected_resume_path if package.resume_selection else None
    if src:
        src_path = Path(src)
        if src_path.exists():
            selected_copy = out_dir / src_path.name
            shutil.copy2(src_path, selected_copy)
        else:
            logger.warning("resume file missing, cannot copy: {}", src_path)

    package.output_dir = str(out_dir)
    package.cover_letter_path = str(cover_letter_path)
    package.company_brief_path = str(company_brief_path)
    package.resume_report_path = str(resume_report_path)
    package.selected_resume_copy_path = str(selected_copy) if selected_copy else None
    return package
