"""Request and planning domain types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class Difficulty(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProjectLanguage(str, Enum):
    GO = "go"
    C = "c"
    CPP = "c++"
    RUST = "rust"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    JAVA = "java"


@dataclass(frozen=True)
class PipelineRequest:
    """The user-owned specification for one delivery run."""

    goal: str
    source_repository: str
    architecture_mermaid: str
    acceptance_criteria: List[str]
    constraints: List[str] = field(default_factory=list)
    difficulty: Difficulty = Difficulty.MEDIUM
    task_id: str = ""
    language: ProjectLanguage = ProjectLanguage.PYTHON

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PipelineRequest:
        return cls(
            goal=str(data.get("goal", "")),
            source_repository=str(data.get("source_repository", "")),
            architecture_mermaid=str(data.get("architecture_mermaid", "")),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            constraints=list(data.get("constraints", [])),
            difficulty=Difficulty(data.get("difficulty", Difficulty.MEDIUM.value)),
            task_id=str(data.get("task_id", "")),
            language=ProjectLanguage(
                data.get("language", ProjectLanguage.PYTHON.value)
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Compatibility name from the original scaffold.
Task = PipelineRequest


@dataclass(frozen=True)
class IntakeDecision:
    accepted: bool
    missing_or_invalid: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["stage"] = "intake"
        value["decision"] = "yes" if self.accepted else "no"
        return value


@dataclass(frozen=True)
class Milestone:
    name: str
    objective: str
    files_or_areas: List[str]
    verification: List[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Milestone:
        return cls(
            name=str(data.get("name", "")),
            objective=str(data.get("objective", "")),
            files_or_areas=list(data.get("files_or_areas", [])),
            verification=list(data.get("verification", [])),
        )


@dataclass(frozen=True)
class WorkPlan:
    summary: str
    assumptions: List[str]
    milestones: List[Milestone]
    risks: List[str]
    architecture_review: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkPlan:
        return cls(
            summary=str(data.get("summary", "")),
            assumptions=list(data.get("assumptions", [])),
            milestones=[
                Milestone.from_dict(item) for item in data.get("milestones", [])
            ],
            risks=list(data.get("risks", [])),
            architecture_review=str(data.get("architecture_review", "")),
        )

    def validate(self) -> None:
        if len(self.summary.strip()) < 20:
            raise ValueError("The planner returned a plan without a useful summary.")
        if not self.milestones:
            raise ValueError("The planner returned a plan without milestones.")
        for milestone in self.milestones:
            if not milestone.name.strip() or not milestone.objective.strip():
                raise ValueError("Every plan milestone needs a name and objective.")
            if not milestone.verification:
                raise ValueError("Every plan milestone needs verification.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "assumptions": self.assumptions,
            "milestones": [asdict(milestone) for milestone in self.milestones],
            "risks": self.risks,
            "architecture_review": self.architecture_review,
        }
