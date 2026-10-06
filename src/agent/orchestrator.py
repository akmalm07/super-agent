"""The explicit intake -> plan -> build -> test -> preview/review pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .agent import BuilderAgent, PlannerAgent, TestAgent
from .config import HarnessConfig
from .deployment import CurrentServerDeployer, LocalPreviewDeployer
from .git import GitRepository, safe_branch_slug
from .memory import RunMemory
from .model import ModelClient
from .persistence import SQLiteStore
from .sandbox import CommandResult, SandboxRunner
from .structured import (
    CHAT_SUMMARY_RESULT_SCHEMA,
    CODER_RESULT_SCHEMA,
    INTAKE_RESULT_SCHEMA,
    PLANNER_RESULT_SCHEMA,
    TESTER_RESULT_SCHEMA,
)
from .task import IntakeDecision, WorkPlan


class PipelineState(str, Enum):
    INTAKE_REJECTED = "intake_rejected"
    PLANNED = "planned"
    BUILT = "built"
    TESTED = "tested"
    COMMITTED = "committed"
    DEPLOYED = "deployed"
    PREVIEW_READY = "preview_ready"
    REVIEW_WAITING = "review_waiting"
    FAILED = "failed"


@dataclass
class PipelineResult:
    state: PipelineState
    run_directory: Path
    branch: str = ""
    intake: Optional[IntakeDecision] = None
    plan: Optional[WorkPlan] = None
    test_results: List[CommandResult] = field(default_factory=list)
    deployment_results: List[CommandResult] = field(default_factory=list)
    error: str = ""
    memory_file: str = ""
    project_id: str = ""
    persistence_run_id: str = ""
    revision_id: str = ""
    database_path: str = ""
    commit_sha: str = ""
    preview_id: str = ""
    preview_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "run_directory": str(self.run_directory),
            "branch": self.branch,
            "intake": self.intake.to_dict() if self.intake else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "test_results": [asdict(item) for item in self.test_results],
            "deployment_results": [asdict(item) for item in self.deployment_results],
            "error": self.error,
            "memory_file": self.memory_file,
            "project_id": self.project_id,
            "persistence_run_id": self.persistence_run_id,
            "revision_id": self.revision_id,
            "database_path": self.database_path,
            "commit_sha": self.commit_sha,
            "preview_id": self.preview_id,
            "preview_url": self.preview_url,
        }


class PipelineOrchestrator:
    """Coordinates local agent work. It never pushes or creates a pull request."""

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.planner = PlannerAgent(ModelClient(config.planner))
        self.builder = BuilderAgent(ModelClient(config.coder))
        self.tester = TestAgent(ModelClient(config.tester))

    def intake(self) -> IntakeDecision:
        return self.planner.assess_details(self.config.request)

    def run(
        self, deploy: bool = False, preview: bool = False, chat_id: Optional[str] = None
    ) -> PipelineResult:
        decision = self.intake()
        run_directory = self._new_run_directory()
        memory = RunMemory(run_directory)
        database_path = self.config.workspace.database_path
        if database_path is None:
            raise RuntimeError("Workspace database path was not initialized.")
        store = SQLiteStore(database_path)
        persisted_run = store.start_run(
            None, run_directory, self.config.request, chat_id=chat_id
        )
        result = PipelineResult(
            PipelineState.INTAKE_REJECTED,
            run_directory,
            intake=decision,
            memory_file=str(memory.path),
            persistence_run_id=persisted_run.id,
            database_path=str(store.database_path),
        )
        memory.record("intake", decision.to_dict())
        store.record_output(
            persisted_run.id, "intake", INTAKE_RESULT_SCHEMA, decision.to_dict()
        )
        if not decision.accepted:
            memory.record("final", result.to_dict())
            store.update_run(persisted_run.id, result.state.value, completed=True)
            self._write_report(result)
            return result

        revision_id = ""
        attached_chat_id = None
        try:
            project = store.get_or_create_project(self.config.request)
            store.attach_project(persisted_run.id, project.id)
            result.project_id = project.id
            revision_context = ""
            if chat_id:
                chat = store.get_chat(chat_id)
                if chat is None or chat.project_id != project.id:
                    raise ValueError(
                        "The selected revision chat does not belong to this project."
                    )
                attached_chat_id = chat_id
                revision_context = store.chat_context(chat_id)

            project_directory = run_directory / "project"
            repository = GitRepository.clone(
                self.config.request.source_repository,
                project_directory,
                self.config.workspace.base_branch,
            )
            branch = "codex/{}-{}".format(
                safe_branch_slug(self.config.request.goal), run_directory.name[-8:]
            )
            repository.branch(branch)
            repository.block_automatic_pushes()
            result.branch = branch
            runner = SandboxRunner(repository.path, self.config.sandbox)
            revision = store.record_revision(project.id, persisted_run.id, chat_id)
            revision_id = revision.id
            result.revision_id = revision.id
            store.update_run(persisted_run.id, result.state.value, branch=branch)

            if chat_id and store.chat_needs_summary(chat_id):
                summary = self.planner.summarize_chat(
                    revision_context,
                    repository.path,
                    runner=runner,
                    prompt_recorder=self._prompt_recorder(
                        store, persisted_run.id, "chat_summary", self.config.planner
                    ),
                )
                store.record_chat_summary(chat_id, summary)
                summary_output = {
                    "stage": "chat_summary",
                    "decision": "yes",
                    "summary": "Chat context compacted.",
                    "reason": "The seventeenth conversation triggered a summary.",
                    "payload": {"summary": summary},
                }
                memory.record("chat_summary", summary_output)
                store.record_output(
                    persisted_run.id,
                    "chat_summary",
                    CHAT_SUMMARY_RESULT_SCHEMA,
                    summary_output,
                )
                revision_context = store.chat_context(chat_id)

            plan = self.planner.execute(
                self.config.request,
                repository.path,
                runner=runner,
                prompt_recorder=self._prompt_recorder(
                    store, persisted_run.id, "planner", self.config.planner
                ),
                revision_context=revision_context,
            )
            result.plan = plan
            result.state = PipelineState.PLANNED
            memory.record("planner", self.planner.last_response)
            store.record_output(
                persisted_run.id,
                "planner",
                PLANNER_RESULT_SCHEMA,
                self.planner.last_response,
            )
            store.update_run(persisted_run.id, result.state.value, branch=branch)
            (run_directory / "plan.json").write_text(
                json.dumps(plan.to_dict(), indent=2) + "\n", encoding="utf-8"
            )

            builder_response = self.builder.execute(
                self.config.request,
                plan,
                repository.path,
                runner=runner,
                memory_context=memory.context() + revision_context,
                prompt_recorder=self._prompt_recorder(
                    store, persisted_run.id, "coder", self.config.coder
                ),
            )
            memory.record("coder", builder_response)
            store.record_output(
                persisted_run.id, "coder", CODER_RESULT_SCHEMA, builder_response
            )
            result.state = PipelineState.BUILT
            store.update_run(persisted_run.id, result.state.value, branch=branch)

            tester_response = self.tester.design_and_apply_tests(
                self.config.request,
                plan,
                repository.path,
                runner=runner,
                memory_context=memory.context() + revision_context,
                prompt_recorder=self._prompt_recorder(
                    store, persisted_run.id, "tester", self.config.tester
                ),
            )
            memory.record("tester", tester_response)
            store.record_output(
                persisted_run.id, "tester", TESTER_RESULT_SCHEMA, tester_response
            )
            result.test_results = self._run_test_commands(runner, repository.path)
            if any(item.returncode for item in result.test_results):
                result.state = PipelineState.FAILED
                result.error = "One or more configured test commands failed."
                return result
            result.state = PipelineState.TESTED
            store.update_run(persisted_run.id, result.state.value, branch=branch)

            if self.config.commit.enabled and repository.changed_files():
                result.commit_sha = repository.commit(
                    "{}{}".format(
                        self.config.commit.message_prefix,
                        self.config.request.goal[:72],
                    )
                )
                result.state = PipelineState.COMMITTED
                store.update_run(
                    persisted_run.id,
                    result.state.value,
                    branch=branch,
                    commit_sha=result.commit_sha,
                )

            if self.config.deployment.enabled and not deploy:
                raise ValueError(
                    "Deployment is enabled; pass --deploy to run current-server commands."
                )
            if deploy:
                self._deploy_current_server(store, persisted_run.id, repository, result)

            if preview:
                self._start_preview(
                    store, persisted_run.id, project.id, repository, result
                )

            result.state = PipelineState.REVIEW_WAITING
            store.update_run(
                persisted_run.id,
                result.state.value,
                branch=branch,
                commit_sha=result.commit_sha,
            )
            return result
        except Exception as exc:
            result.state = PipelineState.FAILED
            result.error = str(exc)
            store.update_run(
                persisted_run.id,
                result.state.value,
                branch=result.branch,
                error=result.error,
                commit_sha=result.commit_sha,
            )
            return result
        finally:
            if attached_chat_id:
                store.append_chat_message(
                    attached_chat_id,
                    "assistant",
                    json.dumps(
                        {
                            "state": result.state.value,
                            "commit_sha": result.commit_sha,
                            "preview_url": result.preview_url,
                            "error": result.error,
                        }
                    ),
                )
            if revision_id:
                store.update_revision(
                    revision_id,
                    result.state.value,
                    result.commit_sha,
                    result.preview_url,
                )
            memory.record("final", result.to_dict())
            store.update_run(
                persisted_run.id,
                result.state.value,
                branch=result.branch,
                error=result.error,
                commit_sha=result.commit_sha,
                completed=True,
            )
            self._write_report(result)

    def _deploy_current_server(
        self,
        store: SQLiteStore,
        run_id: str,
        repository: GitRepository,
        result: PipelineResult,
    ) -> None:
        if not self.config.deployment.enabled:
            raise ValueError("Deployment requires deployment.enabled=true.")
        result.deployment_results = CurrentServerDeployer(
            repository.path, self.config.deployment
        ).deploy()
        for index, command_result in enumerate(result.deployment_results):
            kind = (
                "deploy"
                if index < len(self.config.deployment.commands)
                else "health_check"
            )
            store.record_deployment(
                run_id,
                kind,
                command_result.command,
                "passed" if command_result.returncode == 0 else "failed",
                command_result.stdout,
                command_result.stderr,
            )
        if any(item.returncode for item in result.deployment_results):
            raise RuntimeError("Deployment or health check failed.")
        result.state = PipelineState.DEPLOYED

    def _start_preview(
        self,
        store: SQLiteStore,
        run_id: str,
        project_id: str,
        repository: GitRepository,
        result: PipelineResult,
    ) -> None:
        if not self.config.preview.enabled:
            raise ValueError("Preview requires preview.enabled=true in configuration.")
        # The server owner chooses the port pool during dashboard onboarding.
        # Per-project profile ranges are retained only for legacy import support
        # and must never expand what a project can bind.
        lease = store.acquire_port(project_id, "preview")
        try:
            launch = LocalPreviewDeployer(
                repository.path, self.config.preview, lease.port
            ).start()
        except Exception:
            store.release_port(lease.id)
            raise
        store.set_port_process(lease.id, launch.process_id)
        preview = store.record_preview(
            project_id,
            run_id,
            lease.id,
            launch.command,
            launch.process_id,
            launch.url,
            "running",
            "healthy" if launch.healthy else "unhealthy",
        )
        result.preview_id = preview.id
        result.preview_url = preview.url
        result.state = PipelineState.PREVIEW_READY

    @staticmethod
    def _prompt_recorder(store: SQLiteStore, run_id: str, stage: str, model) -> Any:
        def record(prompt: str, schema: Dict[str, Any], attempt: int) -> None:
            store.record_prompt(
                run_id,
                stage,
                attempt,
                model.provider.value,
                model.model,
                prompt,
                schema,
            )

        return record

    def _new_run_directory(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = "{}-{}-{}".format(
            timestamp,
            safe_branch_slug(self.config.request.goal)[:24],
            uuid4().hex[:8],
        )
        for suffix in range(1000):
            name = stem if suffix == 0 else "{}-{:02d}".format(stem, suffix)
            path = self.config.workspace.runs_directory / name
            try:
                path.mkdir(parents=True, exist_ok=False)
                return path
            except FileExistsError:
                continue
        raise RuntimeError("Could not allocate a unique run directory.")

    def _run_test_commands(
        self, runner: SandboxRunner, repository: Path
    ) -> List[CommandResult]:
        return [
            runner.run(command, repository, self.config.testing.timeout_seconds)
            for command in self.config.testing.commands
        ]

    def _write_report(self, result: PipelineResult) -> None:
        result.run_directory.mkdir(parents=True, exist_ok=True)
        (result.run_directory / "pipeline-report.json").write_text(
            json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
