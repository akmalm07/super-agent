"""Planner, builder, and test agents."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from .model import ExecutionMode, ModelClient
from .prompts import PromptLibrary
from .structured import (
    CHAT_SUMMARY_RESULT_SCHEMA,
    CODER_RESULT_SCHEMA,
    PLANNER_RESULT_SCHEMA,
    TESTER_RESULT_SCHEMA,
)
from .task import IntakeDecision, PipelineRequest, WorkPlan

MERMAID_STARTS = (
    "flowchart",
    "graph",
    "sequencediagram",
    "classdiagram",
    "statediagram",
    "erdiagram",
    "journey",
    "gantt",
    "mindmap",
    "timeline",
    "quadrantchart",
    "requirementdiagram",
)


def normalise_mermaid(chart: str) -> str:
    text = chart.strip()
    match = re.fullmatch(
        r"```mermaid\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE
    )
    return (match.group(1) if match else text).strip()


def is_github_repository(value: str) -> bool:
    if value.startswith("git@github.com:"):
        parts = value.split(":", 1)[1].strip("/").split("/")
        return len(parts) == 2 and all(parts)
    parsed = urlparse(value)
    parts = parsed.path.strip("/").split("/")
    return (
        parsed.scheme in {"https", "ssh"}
        and parsed.hostname == "github.com"
        and len(parts) == 2
        and all(parts)
    )


class PlannerAgent:
    """Reject incomplete requests before any model may change a repository."""

    def __init__(self, model: ModelClient):
        self.model = model
        self.prompts = PromptLibrary.from_environment()
        self.last_response = None

    @staticmethod
    def assess_details(task: PipelineRequest) -> IntakeDecision:
        issues: List[str] = []
        if len(task.goal.strip()) < 20:
            issues.append("goal: give a concrete outcome of at least 20 characters")
        if not is_github_repository(task.source_repository):
            issues.append("source_repository: provide a GitHub clone URL")
        chart = normalise_mermaid(task.architecture_mermaid)
        if not chart:
            issues.append(
                "architecture_mermaid: provide a Mermaid architecture diagram"
            )
        elif not chart.lower().startswith(MERMAID_STARTS):
            issues.append(
                "architecture_mermaid: start with a supported Mermaid declaration"
            )
        if not task.acceptance_criteria:
            issues.append(
                "acceptance_criteria: provide at least one observable success condition"
            )
        elif any(len(str(item).strip()) < 5 for item in task.acceptance_criteria):
            issues.append("acceptance_criteria: each condition must be specific")
        notes = (
            []
            if issues
            else ["Intake accepted; only documented assumptions are allowed."]
        )
        return IntakeDecision(not issues, issues, notes)

    def execute(
        self,
        task: PipelineRequest,
        repository: Path,
        runner=None,
        prompt_recorder=None,
        revision_context: str = "",
    ) -> WorkPlan:
        decision = self.assess_details(task)
        if not decision.accepted:
            raise ValueError(
                "Request rejected: {}".format("; ".join(decision.missing_or_invalid))
            )
        prompt = self.prompts.render(
            "planner",
            goal=task.goal,
            source_repository=task.source_repository,
            architecture_mermaid=normalise_mermaid(task.architecture_mermaid),
            acceptance_criteria=json.dumps(task.acceptance_criteria),
            constraints=json.dumps(task.constraints),
            language=task.language.value,
            revision_context=revision_context,
        )
        response = self.model.ask_structured(
            prompt, PLANNER_RESULT_SCHEMA, repository, runner, prompt_recorder
        )
        self.last_response = response
        if response["decision"] != "yes":
            raise ValueError(
                "Planner rejected the request: {}".format(response["reason"])
            )
        plan = WorkPlan.from_dict(response["payload"]["plan"])
        plan.validate()
        return plan

    def summarize_chat(
        self, chat_context: str, repository: Path, runner=None, prompt_recorder=None
    ) -> str:
        response = self.model.ask_structured(
            self.prompts.render("chat_summary", chat_context=chat_context),
            CHAT_SUMMARY_RESULT_SCHEMA,
            repository,
            runner,
            prompt_recorder,
        )
        if response["decision"] != "yes":
            raise ValueError("Chat summary declined: {}".format(response["reason"]))
        return response["payload"]["summary"]


class PatchApplyError(RuntimeError):
    pass


def apply_unified_patch(output: str, repository: Path) -> None:
    """Apply an explicit git diff only after git validates it in the clone."""
    patch = output.strip()
    fenced = re.search(r"```(?:diff|patch)?\s*(diff --git .*?)\s*```", patch, re.DOTALL)
    if fenced:
        patch = fenced.group(1)
    start = patch.find("diff --git ")
    if start < 0:
        raise PatchApplyError("Patch-mode agent did not return a git unified diff.")
    patch = patch[start:]
    for old_path, new_path in re.findall(
        r"^diff --git a/(.+?) b/(.+?)$", patch, re.MULTILINE
    ):
        for candidate in (old_path, new_path):
            parts = candidate.replace("\\", "/").split("/")
            if candidate.startswith("/") or ".." in parts or ".git" in parts:
                raise PatchApplyError(
                    "Patch attempts to write outside the project worktree."
                )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".patch", delete=False
    ) as handle:
        handle.write(patch.rstrip() + "\n")
        path = Path(handle.name)
    try:
        checked = subprocess.run(
            ["git", "apply", "--whitespace=fix", "--check", str(path)],
            cwd=str(repository),
            text=True,
            capture_output=True,
            check=False,
        )
        if checked.returncode:
            raise PatchApplyError(
                checked.stderr.strip() or "Generated patch cannot be applied safely."
            )
        applied = subprocess.run(
            ["git", "apply", "--whitespace=fix", str(path)],
            cwd=str(repository),
            text=True,
            capture_output=True,
            check=False,
        )
        if applied.returncode:
            raise PatchApplyError(
                applied.stderr.strip() or "Generated patch could not be applied."
            )
    finally:
        path.unlink(missing_ok=True)


class BuilderAgent:
    def __init__(self, model: ModelClient):
        self.model = model
        self.prompts = PromptLibrary.from_environment()
        self.last_response = None

    def execute(
        self,
        task: PipelineRequest,
        plan: WorkPlan,
        repository: Path,
        runner=None,
        memory_context: str = "",
        prompt_recorder=None,
    ) -> Dict[str, Any]:
        if self.model.config.execution_mode is ExecutionMode.TEXT:
            raise ValueError("A coder must use workspace or patch execution_mode.")
        patch_mode = self.model.config.execution_mode is ExecutionMode.PATCH
        prompt = self.prompts.render(
            "coder",
            goal=task.goal,
            language=task.language.value,
            architecture_mermaid=normalise_mermaid(task.architecture_mermaid),
            plan=json.dumps(plan.to_dict(), indent=2),
            acceptance_criteria=json.dumps(task.acceptance_criteria),
            memory_context=memory_context,
            execution_mode="patch" if patch_mode else "workspace",
        )
        output = self.model.ask_structured(
            prompt, CODER_RESULT_SCHEMA, repository, runner, prompt_recorder
        )
        self.last_response = output
        if output["decision"] != "yes":
            raise ValueError("Coder declined delivery: {}".format(output["reason"]))
        if not output["payload"]["acceptance_criteria_met"]:
            raise ValueError("Coder reported unmet acceptance criteria.")
        if patch_mode:
            apply_unified_patch(output["payload"]["patch"], repository)
        return output


class TestAgent:
    def __init__(self, model: ModelClient):
        self.model = model
        self.prompts = PromptLibrary.from_environment()
        self.last_response = None

    def design_and_apply_tests(
        self,
        task: PipelineRequest,
        plan: WorkPlan,
        repository: Path,
        runner=None,
        memory_context: str = "",
        prompt_recorder=None,
    ) -> Dict[str, Any]:
        if self.model.config.execution_mode is ExecutionMode.TEXT:
            raise ValueError("A tester must use workspace or patch execution_mode.")
        patch_mode = self.model.config.execution_mode is ExecutionMode.PATCH
        prompt = self.prompts.render(
            "tester",
            goal=task.goal,
            language=task.language.value,
            acceptance_criteria=json.dumps(task.acceptance_criteria),
            plan=json.dumps(plan.to_dict(), indent=2),
            memory_context=memory_context,
            execution_mode="patch" if patch_mode else "workspace",
        )
        output = self.model.ask_structured(
            prompt, TESTER_RESULT_SCHEMA, repository, runner, prompt_recorder
        )
        self.last_response = output
        if output["decision"] != "yes":
            raise ValueError(
                "Tester declined review readiness: {}".format(output["reason"])
            )
        if not output["payload"]["all_checks_passed"]:
            raise ValueError("Tester reported failing checks.")
        if patch_mode:
            apply_unified_patch(output["payload"]["patch"], repository)
        return output
