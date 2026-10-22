"""Versioned local checkpoints; atomic writes and process-lifetime ownership."""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from schemas.models import ApplicationPackage


STAGES = ("created", "parse_jd", "company_research", "resume_selection", "cover_letter", "write_outputs", "completed")
Stage = Literal["created", "parse_jd", "company_research", "resume_selection", "cover_letter", "write_outputs", "completed"]


class CheckpointError(RuntimeError):
    """A checkpoint cannot be safely read, written, or resumed."""


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    session_id: str
    input: str
    working_directory: str
    skip_notion: bool = False
    stage: Stage = "created"
    package: ApplicationPackage | None = None
    notion: Literal["pending", "in_flight", "done", "skipped"] = "pending"

    @model_validator(mode="after")
    def validate_progress(self) -> PipelineState:
        index = STAGES.index(self.stage)
        if not self.input.strip() or not self.session_id:
            raise ValueError("input and session ID are required")
        if index == 0:
            if self.package is not None:
                raise ValueError("created checkpoint cannot contain a package")
        else:
            if self.package is None or self.package.session_id != self.session_id:
                raise ValueError("checkpoint requires a package with the same session ID")
            for position, field in ((2, "company_brief"), (3, "resume_selection"), (4, "cover_letter")):
                if index >= position and getattr(self.package, field) is None:
                    raise ValueError(f"completed stage requires {field}")
        if index < 5 and self.notion != "pending":
            raise ValueError("Notion progress requires published outputs")
        if index == 6 and self.notion not in {"done", "skipped"}:
            raise ValueError("completed checkpoint requires a Notion disposition")
        return self

    def reached(self, stage: Stage) -> bool:
        return STAGES.index(self.stage) >= STAGES.index(stage)


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Lock a persistent sidecar inode; the OS releases it on process exit."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CheckpointError(f"Run is already in use: {self.path}") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self) -> PipelineState:
        try:
            return PipelineState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Validation errors can include saved personal content: don't echo them.
            raise CheckpointError(f"Invalid or unsupported checkpoint: {self.path}") from exc

    def save(self, state: PipelineState) -> None:
        # Revalidate mutable models before committing them to disk.
        payload = PipelineState.model_validate(state.model_dump()).model_dump_json(indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".state-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
