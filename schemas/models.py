from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ResumeVariant(str, Enum):
    AI_ML = "AI_ML"
    NETWORK_SECURITY = "NETWORK_SECURITY"
    DATABASE = "DATABASE"
    SYSTEMS = "SYSTEMS"
    GENERAL = "GENERAL"


class CompanyStage(str, Enum):
    EARLY = "Early"
    GROWTH = "Growth"
    PUBLIC = "Public"
    ENTERPRISE = "Enterprise"
    UNKNOWN = "Unknown"


class ApplicationStatus(str, Enum):
    CREATED = "created"
    OUTPUTS_WRITTEN = "outputs_written"
    NOTION_SYNCED = "notion_synced"
    COMPLETED = "completed"
    FAILED = "failed"


class ParsedJD(BaseModel):
    company: str | None = None
    role: str | None = None
    location: str | None = None
    ats: str | None = None
    seniority: str | None = None
    requirements: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    domain_signals: list[str] = Field(default_factory=list)
    raw_text: str = ""


class ResumeSelection(BaseModel):
    variant: ResumeVariant = ResumeVariant.GENERAL
    grade: str = "C"
    fit_score: int = 50
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    talking_points: list[str] = Field(default_factory=list)
    selected_resume_path: str | None = None


class CompanyBrief(BaseModel):
    company: str = "Unknown"
    stage: CompanyStage = CompanyStage.UNKNOWN
    fit_score: int = 50
    strong_overlaps: list[str] = Field(default_factory=list)
    potential_angles: list[str] = Field(default_factory=list)
    sponsorship: str = "Unknown"
    tech_highlights: list[str] = Field(default_factory=list)
    culture_notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class CoverLetter(BaseModel):
    company: str = "Unknown"
    role: str = "Unknown"
    body: str = ""
    hook_summary: str = ""
    word_count: int = 0


class ApplicationPackage(BaseModel):
    jd: ParsedJD
    company_brief: CompanyBrief | None = None
    resume_selection: ResumeSelection | None = None
    cover_letter: CoverLetter | None = None
    output_dir: str | None = None
    company_brief_path: str | None = None
    resume_report_path: str | None = None
    cover_letter_path: str | None = None
    selected_resume_copy_path: str | None = None
    notion_page_id: str | None = None
    notion_url: str | None = None
    status: ApplicationStatus = ApplicationStatus.CREATED
