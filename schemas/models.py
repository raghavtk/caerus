from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


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


class ResearchStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ResearchSource(BaseModel):
    id: str
    title: str
    url: str
    published_date: str | None = None
    snippet: str | None = None
    query_tags: list[str] = Field(default_factory=list)


class EvidenceClaim(BaseModel):
    category: str
    statement: str
    source_ids: list[str] = Field(min_length=1)


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


class ProjectRecommendation(BaseModel):
    project_id: str
    name: str
    repository_url: str | None = None
    source_ref: str | None = None
    score: int = Field(ge=0, le=100)
    technology_score: int = Field(default=0, ge=0, le=40)
    domain_score: int = Field(default=0, ge=0, le=25)
    company_role_score: int = Field(default=0, ge=0, le=20)
    tier_score: int = Field(default=0, ge=0, le=10)
    recency_score: int = Field(default=0, ge=0, le=5)
    reason: str
    matched_required: list[str] = Field(default_factory=list)
    matched_preferred: list[str] = Field(default_factory=list)
    matched_domains: list[str] = Field(default_factory=list)
    matched_company_signals: list[str] = Field(default_factory=list)


class ResumeSelection(BaseModel):
    variant: ResumeVariant = ResumeVariant.GENERAL
    grade: str = "C"
    fit_score: int = 50
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    talking_points: list[str] = Field(default_factory=list)
    selected_resume_path: str | None = None
    project_recommendations: list[ProjectRecommendation] = Field(default_factory=list)


class CompanyBrief(BaseModel):
    company: str = "Unknown"
    stage: CompanyStage = CompanyStage.UNKNOWN
    fit_score: int = Field(default=0, ge=0, le=100)
    strong_overlaps: list[str] = Field(default_factory=list)
    potential_angles: list[str] = Field(default_factory=list)
    sponsorship: str = "Unknown"
    tech_highlights: list[str] = Field(default_factory=list)
    culture_notes: list[str] = Field(default_factory=list)
    research_status: ResearchStatus = ResearchStatus.PARTIAL
    role_context: list[str] = Field(default_factory=list)
    recent_developments: list[str] = Field(default_factory=list)
    candidate_overlaps: list[str] = Field(default_factory=list)
    concerns_or_unknowns: list[str] = Field(default_factory=list)
    talking_points: list[str] = Field(default_factory=list)
    evidence: list[EvidenceClaim] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _upgrade_legacy_sources(cls, value: object) -> object:
        if not isinstance(value, list):
            return []
        upgraded: list[object] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, str):
                upgraded.append({"id": f"S{index}", "title": item, "url": item})
            else:
                upgraded.append(item)
        return upgraded


class CoverLetter(BaseModel):
    company: str = "Unknown"
    role: str = "Unknown"
    body: str = ""
    hook_summary: str = ""
    word_count: int = 0
    optimization_diagnostics: CoverLetterOptimizationDiagnostics | None = None


class CoverLetterCritique(BaseModel):
    """Structured assessment of a cover-letter draft by the optimization critic."""

    approved: bool = False
    findings: list[str] = Field(default_factory=list)
    factual_grounding: int = Field(ge=0, le=5)
    role_fit: int = Field(ge=0, le=5)
    company_specificity: int = Field(ge=0, le=5)
    voice: int = Field(ge=0, le=5)
    clarity: int = Field(ge=0, le=5)
    repetition: int = Field(ge=0, le=5)
    cliches: int = Field(ge=0, le=5)
    professional_tone: int = Field(ge=0, le=5)


class CoverLetterOptimizationDiagnostics(BaseModel):
    revision_count: int = Field(default=0, ge=0, le=2)
    critic: CoverLetterCritique | None = None
    validation_codes: list[str] = Field(default_factory=list)
    approved: bool = False


class CoverLetterRenderDiagnostics(BaseModel):
    """Outcome data for canonical DOCX rendering and PDF validation."""

    stage: str = "not_started"
    success: bool = False
    validation_codes: list[str] = Field(default_factory=list)
    pdf_page_count: int | None = Field(default=None, ge=0)
    diagnostics_path: str | None = None
    message: str | None = None


class CoverLetterRecipient(BaseModel):
    """Known recipient fields rendered as-is; missing fields are omitted."""

    name: str | None = None
    title: str | None = None
    company: str | None = None
    address_lines: list[str] = Field(default_factory=list)

    @field_validator("name", "title", "company", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("address_lines", mode="before")
    @classmethod
    def normalize_address_lines(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [line.strip() for line in value if isinstance(line, str) and line.strip()]


class ApplicationPackage(BaseModel):
    jd: ParsedJD
    company_brief: CompanyBrief | None = None
    resume_selection: ResumeSelection | None = None
    cover_letter: CoverLetter | None = None
    cover_letter_recipient: CoverLetterRecipient | None = None
    optimization_diagnostics: CoverLetterOptimizationDiagnostics | None = None
    render_diagnostics: CoverLetterRenderDiagnostics | None = None
    output_dir: str | None = None
    company_brief_path: str | None = None
    resume_report_path: str | None = None
    cover_letter_path: str | None = None
    cover_letter_docx_path: str | None = None
    selected_resume_copy_path: str | None = None
    notion_page_id: str | None = None
    notion_url: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    trace_url: str | None = None
    status: ApplicationStatus = ApplicationStatus.CREATED
