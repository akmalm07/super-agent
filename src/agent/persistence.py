"""SQLite ORM persistence for the local project control plane."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .task import Difficulty, PipelineRequest, ProjectLanguage


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_repository: Mapped[str] = mapped_column(
        String(1024), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    language: Mapped[str] = mapped_column(String(32), default="python")
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    task_id: Mapped[str] = mapped_column(String(256), default="")
    latest_goal: Mapped[str] = mapped_column(Text)
    architecture_mermaid: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria_json: Mapped[str] = mapped_column(Text, default="[]")
    constraints_json: Mapped[str] = mapped_column(Text, default="[]")
    default_config_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True
    )
    chat_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("chats.id"), index=True, nullable=True
    )
    run_directory: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(64), index=True)
    branch: Mapped[str] = mapped_column(String(256), default="")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    request_json: Mapped[str] = mapped_column(Text)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PromptRecord(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    prompt: Mapped[str] = mapped_column(Text)
    schema_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StructuredOutputRecord(Base):
    __tablename__ = "structured_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(16), default="")
    schema_json: Mapped[str] = mapped_column(Text)
    output_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeploymentRecord(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    command_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    stdout: Mapped[str] = mapped_column(Text)
    stderr: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatRecord(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="open")
    conversation_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_since_summary: Mapped[int] = mapped_column(Integer, default=0)
    summary_due: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RevisionRecord(Base):
    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    chat_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("chats.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(64), default="in_progress")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    preview_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortLeaseRecord(Base):
    __tablename__ = "port_leases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    port: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    process_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ServerSettingsRecord(Base):
    """Singleton settings owned by the person operating this harness server."""

    __tablename__ = "server_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preview_port_start: Mapped[int] = mapped_column(Integer)
    preview_port_end: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortInventoryRecord(Base):
    """Latest observed state of one port in the globally managed preview pool."""

    __tablename__ = "port_inventory"

    port: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    lease_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("port_leases.id"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortEventRecord(Base):
    """Append-only audit trail for a managed port becoming taken or free."""

    __tablename__ = "port_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    port: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32))
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    lease_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("port_leases.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PreviewRecord(Base):
    __tablename__ = "previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    port_lease_id: Mapped[str] = mapped_column(ForeignKey("port_leases.id"))
    command_json: Mapped[str] = mapped_column(Text)
    process_id: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="starting")
    health: Mapped[str] = mapped_column(String(32), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


@dataclass(frozen=True)
class ChatAppendResult:
    chat_id: str
    summary_due: bool
    conversation_count: int


class SQLiteStore:
    """Durable local state. Provider credentials are never stored here."""

    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path.resolve()
        self.engine = create_engine(
            "sqlite:///{}".format(self.database_path.as_posix())
        )
        Base.metadata.create_all(self.engine)
        self._migrate_existing_database()
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def _migrate_existing_database(self) -> None:
        """Add columns introduced after the first local SQLite MVP release."""
        additions = {
            "projects": {
                "language": "VARCHAR(32) NOT NULL DEFAULT 'python'",
                "difficulty": "VARCHAR(16) NOT NULL DEFAULT 'medium'",
                "task_id": "VARCHAR(256) NOT NULL DEFAULT ''",
                "architecture_mermaid": "TEXT NOT NULL DEFAULT ''",
                "acceptance_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
                "constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                "default_config_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "runs": {
                "chat_id": "VARCHAR(36)",
                "commit_sha": "VARCHAR(64) NOT NULL DEFAULT ''",
            },
        }
        with self.engine.begin() as connection:
            for table, columns in additions.items():
                existing = {
                    row[1]
                    for row in connection.execute(
                        text("PRAGMA table_info({})".format(table))
                    )
                }
                for name, declaration in columns.items():
                    if name not in existing:
                        connection.execute(
                            text(
                                "ALTER TABLE {} ADD COLUMN {} {}".format(
                                    table, name, declaration
                                )
                            )
                        )

    @staticmethod
    def _validate_port_range(port_start: int, port_end: int) -> None:
        if not 1 <= port_start <= port_end <= 65535:
            raise ValueError("Port range must be between 1 and 65535.")
        if port_end - port_start + 1 < 5:
            raise ValueError(
                "The global preview pool must contain at least five ports."
            )

    def get_port_policy(self) -> Optional[ServerSettingsRecord]:
        with self.session_factory() as session:
            return session.get(ServerSettingsRecord, 1)

    def require_port_policy(self) -> ServerSettingsRecord:
        policy = self.get_port_policy()
        if policy is None:
            raise RuntimeError(
                "No global preview port range is configured. Open `super-agent` "
                "and complete port-pool onboarding first."
            )
        return policy

    def configure_port_policy(
        self, port_start: int, port_end: int
    ) -> ServerSettingsRecord:
        """Persist the only range the harness may use for preview processes."""
        self._validate_port_range(port_start, port_end)
        with self.session_factory.begin() as session:
            active_outside_range = (
                session.query(PortLeaseRecord)
                .filter_by(status="active")
                .filter(
                    (PortLeaseRecord.port < port_start)
                    | (PortLeaseRecord.port > port_end)
                )
                .order_by(PortLeaseRecord.port.asc())
                .all()
            )
            if active_outside_range:
                raise ValueError(
                    "Stop previews using ports {} before changing the global range.".format(
                        ", ".join(str(lease.port) for lease in active_outside_range)
                    )
                )
            policy = session.get(ServerSettingsRecord, 1)
            now = utc_now()
            if policy is None:
                policy = ServerSettingsRecord(
                    id=1,
                    preview_port_start=port_start,
                    preview_port_end=port_end,
                    created_at=now,
                    updated_at=now,
                )
                session.add(policy)
            else:
                policy.preview_port_start = port_start
                policy.preview_port_end = port_end
                policy.updated_at = now
        self.refresh_port_inventory()
        return self.require_port_policy()

    @staticmethod
    def _event_for_transition(previous: Optional[str], current: str) -> str:
        if current == "leased":
            return "leased"
        if current == "available" and previous == "occupied":
            return "observed_freed"
        if current == "occupied":
            return "observed_taken"
        return "observed_{}".format(current)

    @staticmethod
    def _set_inventory_status(
        session: Any,
        port: int,
        status: str,
        source: str,
        project_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        action: Optional[str] = None,
    ) -> None:
        record = session.get(PortInventoryRecord, port)
        previous = record.status if record is not None else None
        changed = (
            record is None
            or record.status != status
            or record.project_id != project_id
            or record.lease_id != lease_id
        )
        if record is None:
            record = PortInventoryRecord(
                port=port,
                status=status,
                project_id=project_id,
                lease_id=lease_id,
                observed_at=utc_now(),
            )
            session.add(record)
        else:
            record.status = status
            record.project_id = project_id
            record.lease_id = lease_id
            record.observed_at = utc_now()
        if changed or action:
            session.add(
                PortEventRecord(
                    port=port,
                    action=action
                    or SQLiteStore._event_for_transition(previous, status),
                    source=source,
                    project_id=project_id,
                    lease_id=lease_id,
                    created_at=utc_now(),
                )
            )

    def refresh_port_inventory(self) -> List[PortInventoryRecord]:
        """Probe the configured pool and persist every observed state transition."""
        policy = self.require_port_policy()
        self.reconcile_port_leases()
        with self.session_factory.begin() as session:
            active_leases = {
                lease.port: lease
                for lease in session.query(PortLeaseRecord)
                .filter_by(status="active")
                .all()
            }
            for port in range(policy.preview_port_start, policy.preview_port_end + 1):
                lease = active_leases.get(port)
                if lease is not None:
                    self._set_inventory_status(
                        session,
                        port,
                        "leased",
                        "harness",
                        lease.project_id,
                        lease.id,
                    )
                else:
                    self._set_inventory_status(
                        session,
                        port,
                        "available" if self._is_port_available(port) else "occupied",
                        "system_probe",
                    )
        return self.list_port_inventory(refresh=False)

    def list_port_inventory(self, refresh: bool = True) -> List[PortInventoryRecord]:
        if refresh:
            self.refresh_port_inventory()
        policy = self.require_port_policy()
        with self.session_factory() as session:
            return (
                session.query(PortInventoryRecord)
                .filter(PortInventoryRecord.port >= policy.preview_port_start)
                .filter(PortInventoryRecord.port <= policy.preview_port_end)
                .order_by(PortInventoryRecord.port.asc())
                .all()
            )

    def list_port_events(self, port: Optional[int] = None) -> List[PortEventRecord]:
        with self.session_factory() as session:
            query = session.query(PortEventRecord)
            if port is not None:
                query = query.filter_by(port=port)
            return query.order_by(PortEventRecord.id.asc()).all()

    @staticmethod
    def _project_name(request: PipelineRequest) -> str:
        if request.task_id:
            return request.task_id
        tail = request.source_repository.rstrip("/").split("/")[-1]
        return tail[:-4] if tail.endswith(".git") else tail

    @staticmethod
    def _apply_request(project: ProjectRecord, request: PipelineRequest) -> None:
        project.name = SQLiteStore._project_name(request)
        project.language = request.language.value
        project.difficulty = request.difficulty.value
        project.task_id = request.task_id
        project.latest_goal = request.goal
        project.architecture_mermaid = request.architecture_mermaid
        project.acceptance_criteria_json = json.dumps(request.acceptance_criteria)
        project.constraints_json = json.dumps(request.constraints)
        project.updated_at = utc_now()

    def get_or_create_project(self, request: PipelineRequest) -> ProjectRecord:
        with self.session_factory.begin() as session:
            project = (
                session.query(ProjectRecord)
                .filter_by(source_repository=request.source_repository)
                .one_or_none()
            )
            if project is None:
                project = ProjectRecord(
                    id=str(uuid4()),
                    source_repository=request.source_repository,
                    name=self._project_name(request),
                    language=request.language.value,
                    difficulty=request.difficulty.value,
                    task_id=request.task_id,
                    latest_goal=request.goal,
                    architecture_mermaid=request.architecture_mermaid,
                    acceptance_criteria_json=json.dumps(request.acceptance_criteria),
                    constraints_json=json.dumps(request.constraints),
                    default_config_json="{}",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(project)
            else:
                self._apply_request(project, request)
        return project

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        with self.session_factory() as session:
            return session.get(ProjectRecord, project_id)

    def resolve_project(self, reference: str) -> ProjectRecord:
        with self.session_factory() as session:
            matches = (
                session.query(ProjectRecord)
                .filter(
                    (ProjectRecord.id == reference)
                    | (ProjectRecord.id.like("{}%".format(reference)))
                    | (ProjectRecord.name == reference)
                )
                .all()
            )
            if len(matches) != 1:
                raise ValueError(
                    "Project reference must match exactly one project ID, ID prefix, or name."
                )
            return matches[0]

    def get_project_request(self, project_id: str) -> PipelineRequest:
        project = self.get_project(project_id)
        if project is None:
            raise ValueError("Unknown project: {}".format(project_id))
        return PipelineRequest(
            goal=project.latest_goal,
            source_repository=project.source_repository,
            architecture_mermaid=project.architecture_mermaid,
            acceptance_criteria=json.loads(project.acceptance_criteria_json),
            constraints=json.loads(project.constraints_json),
            difficulty=Difficulty(project.difficulty),
            task_id=project.task_id,
            language=ProjectLanguage(project.language),
        )

    def set_execution_profile(self, project_id: str, profile: Dict[str, Any]) -> None:
        with self.session_factory.begin() as session:
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise ValueError("Unknown project: {}".format(project_id))
            project.default_config_json = json.dumps(profile, indent=2, sort_keys=True)
            project.updated_at = utc_now()

    def get_execution_profile(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            raise ValueError("Unknown project: {}".format(project_id))
        try:
            profile = json.loads(project.default_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Stored execution profile is invalid JSON.") from exc
        if not isinstance(profile, dict) or not profile:
            raise ValueError(
                "Project has no execution profile. Import or configure one."
            )
        return profile

    def has_execution_profile(self, project_id: str) -> bool:
        try:
            self.get_execution_profile(project_id)
            return True
        except ValueError:
            return False

    def list_projects(self) -> List[ProjectRecord]:
        with self.session_factory() as session:
            return (
                session.query(ProjectRecord)
                .order_by(ProjectRecord.updated_at.desc())
                .all()
            )

    def list_project_runs(self, project_id: str, limit: int = 10) -> List[RunRecord]:
        with self.session_factory() as session:
            return (
                session.query(RunRecord)
                .filter_by(project_id=project_id)
                .order_by(RunRecord.created_at.desc())
                .limit(limit)
                .all()
            )

    def start_run(
        self,
        project_id: Optional[str],
        run_directory: Path,
        request: PipelineRequest,
        chat_id: Optional[str] = None,
    ) -> RunRecord:
        run = RunRecord(
            id=str(uuid4()),
            project_id=project_id,
            chat_id=chat_id,
            run_directory=str(run_directory),
            state="intake_rejected",
            request_json=json.dumps(request.to_dict()),
            created_at=utc_now(),
        )
        with self.session_factory.begin() as session:
            session.add(run)
        return run

    def attach_project(self, run_id: str, project_id: str) -> None:
        with self.session_factory.begin() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError("Unknown pipeline run: {}".format(run_id))
            run.project_id = project_id

    def update_run(
        self,
        run_id: str,
        state: str,
        branch: str = "",
        error: str = "",
        commit_sha: str = "",
        completed: bool = False,
    ) -> None:
        with self.session_factory.begin() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise ValueError("Unknown pipeline run: {}".format(run_id))
            run.state = state
            if branch:
                run.branch = branch
            if error:
                run.error = error
            if commit_sha:
                run.commit_sha = commit_sha
            if completed:
                run.completed_at = utc_now()

    def record_prompt(
        self,
        run_id: str,
        stage: str,
        attempt: int,
        provider: str,
        model: str,
        prompt: str,
        schema: Dict[str, Any],
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                PromptRecord(
                    run_id=run_id,
                    stage=stage,
                    attempt=attempt,
                    provider=provider,
                    model=model,
                    prompt=prompt,
                    schema_json=json.dumps(schema),
                    created_at=utc_now(),
                )
            )

    def record_output(
        self, run_id: str, stage: str, schema: Dict[str, Any], output: Dict[str, Any]
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                StructuredOutputRecord(
                    run_id=run_id,
                    stage=stage,
                    decision=str(output.get("decision", "")),
                    schema_json=json.dumps(schema),
                    output_json=json.dumps(output),
                    created_at=utc_now(),
                )
            )

    def record_deployment(
        self,
        run_id: str,
        kind: str,
        command: List[str],
        status: str,
        stdout: str,
        stderr: str,
    ) -> None:
        with self.session_factory.begin() as session:
            session.add(
                DeploymentRecord(
                    run_id=run_id,
                    kind=kind,
                    command_json=json.dumps(command),
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    created_at=utc_now(),
                )
            )

    def create_chat(self, project_id: str, suggestion: str) -> ChatRecord:
        if not suggestion.strip():
            raise ValueError("A chat needs a non-empty suggestion.")
        if self.get_project(project_id) is None:
            raise ValueError("Unknown project: {}".format(project_id))
        chat = ChatRecord(
            id=str(uuid4()),
            project_id=project_id,
            title=suggestion.strip().replace("\n", " ")[:160],
            status="open",
            conversation_count=1,
            messages_since_summary=1,
            summary_due=False,
            summary="",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        with self.session_factory.begin() as session:
            session.add(chat)
            session.add(
                ChatMessageRecord(
                    chat_id=chat.id,
                    ordinal=1,
                    role="user",
                    content=suggestion.strip(),
                    created_at=utc_now(),
                )
            )
        return chat

    def get_chat(self, chat_id: str) -> Optional[ChatRecord]:
        with self.session_factory() as session:
            return session.get(ChatRecord, chat_id)

    def list_chats(self, project_id: str, limit: int = 20) -> List[ChatRecord]:
        with self.session_factory() as session:
            return (
                session.query(ChatRecord)
                .filter_by(project_id=project_id)
                .order_by(ChatRecord.updated_at.desc())
                .limit(limit)
                .all()
            )

    def append_chat_message(
        self, chat_id: str, role: str, content: str
    ) -> ChatAppendResult:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("Chat role must be user, assistant, or system.")
        if not content.strip():
            raise ValueError("Chat messages cannot be empty.")
        with self.session_factory.begin() as session:
            chat = session.get(ChatRecord, chat_id)
            if chat is None:
                raise ValueError("Unknown chat: {}".format(chat_id))
            ordinal = (
                session.query(ChatMessageRecord).filter_by(chat_id=chat_id).count() + 1
            )
            session.add(
                ChatMessageRecord(
                    chat_id=chat_id,
                    ordinal=ordinal,
                    role=role,
                    content=content.strip(),
                    created_at=utc_now(),
                )
            )
            if role in {"user", "assistant"}:
                chat.conversation_count += 1
                chat.messages_since_summary += 1
            if chat.messages_since_summary >= 17 and not chat.summary_due:
                chat.summary_due = True
            chat.updated_at = utc_now()
            return ChatAppendResult(chat.id, chat.summary_due, chat.conversation_count)

    def chat_context(self, chat_id: str) -> str:
        with self.session_factory() as session:
            chat = session.get(ChatRecord, chat_id)
            if chat is None:
                raise ValueError("Unknown chat: {}".format(chat_id))
            messages = (
                session.query(ChatMessageRecord)
                .filter_by(chat_id=chat_id)
                .order_by(ChatMessageRecord.ordinal.desc())
                .limit(16)
                .all()
            )
            payload = {
                "chat_id": chat.id,
                "summary": chat.summary,
                "summary_due": chat.summary_due,
                "recent_messages": [
                    {"role": item.role, "content": item.content}
                    for item in reversed(messages)
                ],
            }
            return json.dumps(payload, indent=2)

    def chat_needs_summary(self, chat_id: str) -> bool:
        chat = self.get_chat(chat_id)
        if chat is None:
            raise ValueError("Unknown chat: {}".format(chat_id))
        return chat.summary_due

    def record_chat_summary(self, chat_id: str, summary: str) -> None:
        if not summary.strip():
            raise ValueError("Chat summaries cannot be empty.")
        with self.session_factory.begin() as session:
            chat = session.get(ChatRecord, chat_id)
            if chat is None:
                raise ValueError("Unknown chat: {}".format(chat_id))
            chat.summary = summary.strip()
            chat.summary_due = False
            chat.messages_since_summary = 0
            chat.updated_at = utc_now()

    def record_revision(
        self, project_id: str, run_id: str, chat_id: Optional[str]
    ) -> RevisionRecord:
        revision = RevisionRecord(
            id=str(uuid4()),
            project_id=project_id,
            run_id=run_id,
            chat_id=chat_id,
            status="in_progress",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        with self.session_factory.begin() as session:
            session.add(revision)
        return revision

    def update_revision(
        self,
        revision_id: str,
        status: str,
        commit_sha: str = "",
        preview_url: str = "",
    ) -> None:
        with self.session_factory.begin() as session:
            revision = session.get(RevisionRecord, revision_id)
            if revision is None:
                raise ValueError("Unknown revision: {}".format(revision_id))
            revision.status = status
            if commit_sha:
                revision.commit_sha = commit_sha
            if preview_url:
                revision.preview_url = preview_url
            revision.updated_at = utc_now()

    @staticmethod
    def _is_port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def acquire_port(
        self,
        project_id: str,
        purpose: str,
        port_start: Optional[int] = None,
        port_end: Optional[int] = None,
    ) -> PortLeaseRecord:
        """Lease one port from the global pool.

        ``port_start`` and ``port_end`` remain accepted only for compatibility with
        older callers and saved profiles. They intentionally do not override the
        server owner's persisted global policy.
        """
        if self.get_project(project_id) is None:
            raise ValueError("Unknown project: {}".format(project_id))
        policy = self.require_port_policy()
        self.refresh_port_inventory()
        with self.session_factory() as session:
            leased = {
                row.port
                for row in session.query(PortLeaseRecord)
                .filter_by(status="active")
                .all()
            }
            available = {
                row.port
                for row in session.query(PortInventoryRecord)
                .filter_by(status="available")
                .all()
            }
        for port in range(policy.preview_port_start, policy.preview_port_end + 1):
            if (
                port in leased
                or port not in available
                or not self._is_port_available(port)
            ):
                continue
            try:
                with self.session_factory.begin() as session:
                    # ``port`` was unique in the original MVP schema. Reusing its
                    # row preserves compatibility; the append-only port event log
                    # retains the complete taken/freed history.
                    lease = (
                        session.query(PortLeaseRecord)
                        .filter_by(port=port)
                        .one_or_none()
                    )
                    if lease is None:
                        lease = PortLeaseRecord(
                            id=str(uuid4()),
                            project_id=project_id,
                            port=port,
                            purpose=purpose,
                            status="active",
                            created_at=utc_now(),
                        )
                        session.add(lease)
                    else:
                        lease.project_id = project_id
                        lease.purpose = purpose
                        lease.status = "active"
                        lease.process_id = None
                        lease.created_at = utc_now()
                        lease.released_at = None
                    self._set_inventory_status(
                        session,
                        port,
                        "leased",
                        "harness",
                        project_id,
                        lease.id,
                        action="leased",
                    )
                    lease_id = lease.id
                with self.session_factory() as session:
                    return session.get(PortLeaseRecord, lease_id)
            except Exception:
                # A concurrent local harness may have leased the same port.
                continue
        raise RuntimeError(
            "No available port in the global configured range {}-{}.".format(
                policy.preview_port_start, policy.preview_port_end
            )
        )

    def reconcile_port_leases(self) -> None:
        """Release leases whose managed preview process no longer exists."""
        with self.session_factory.begin() as session:
            leases = session.query(PortLeaseRecord).filter_by(status="active").all()
            for lease in leases:
                if lease.process_id is None:
                    continue
                try:
                    os.kill(lease.process_id, 0)
                except (OSError, ProcessLookupError):
                    lease.status = "released"
                    lease.released_at = utc_now()
                    session.add(
                        PortEventRecord(
                            port=lease.port,
                            action="released",
                            source="process_reconcile",
                            project_id=lease.project_id,
                            lease_id=lease.id,
                            created_at=utc_now(),
                        )
                    )

    def set_port_process(self, lease_id: str, process_id: int) -> None:
        with self.session_factory.begin() as session:
            lease = session.get(PortLeaseRecord, lease_id)
            if lease is None:
                raise ValueError("Unknown port lease: {}".format(lease_id))
            lease.process_id = process_id

    def release_port(self, lease_id: str) -> None:
        with self.session_factory.begin() as session:
            lease = session.get(PortLeaseRecord, lease_id)
            if lease is None:
                raise ValueError("Unknown port lease: {}".format(lease_id))
            lease.status = "released"
            lease.released_at = utc_now()
            self._set_inventory_status(
                session,
                lease.port,
                "available" if self._is_port_available(lease.port) else "occupied",
                "harness",
                action="released",
            )

    def list_port_leases(
        self, project_id: Optional[str] = None
    ) -> List[PortLeaseRecord]:
        with self.session_factory() as session:
            query = session.query(PortLeaseRecord).filter_by(status="active")
            if project_id:
                query = query.filter_by(project_id=project_id)
            return query.order_by(PortLeaseRecord.port.asc()).all()

    def record_preview(
        self,
        project_id: str,
        run_id: str,
        lease_id: str,
        command: List[str],
        process_id: int,
        url: str,
        state: str,
        health: str,
    ) -> PreviewRecord:
        preview = PreviewRecord(
            id=str(uuid4()),
            project_id=project_id,
            run_id=run_id,
            port_lease_id=lease_id,
            command_json=json.dumps(command),
            process_id=process_id,
            url=url,
            state=state,
            health=health,
            created_at=utc_now(),
        )
        with self.session_factory.begin() as session:
            session.add(preview)
        return preview

    def list_previews(self, project_id: str) -> List[PreviewRecord]:
        with self.session_factory() as session:
            return (
                session.query(PreviewRecord)
                .filter_by(project_id=project_id)
                .order_by(PreviewRecord.created_at.desc())
                .all()
            )

    def stop_preview(self, preview_id: str) -> None:
        lease_id = ""
        with self.session_factory.begin() as session:
            preview = session.get(PreviewRecord, preview_id)
            if preview is None:
                raise ValueError("Unknown preview: {}".format(preview_id))
            preview.state = "stopped"
            preview.stopped_at = utc_now()
            lease_id = preview.port_lease_id
        self.release_port(lease_id)
