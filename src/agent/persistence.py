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

from .task import PipelineRequest


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
    def _project_name(request: PipelineRequest) -> str:
        if request.task_id:
            return request.task_id
        tail = request.source_repository.rstrip("/").split("/")[-1]
        return tail[:-4] if tail.endswith(".git") else tail

    @staticmethod
    def _apply_request(project: ProjectRecord, request: PipelineRequest) -> None:
        project.name = SQLiteStore._project_name(request)
        project.language = request.language.value
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
        self, project_id: str, purpose: str, port_start: int, port_end: int
    ) -> PortLeaseRecord:
        if self.get_project(project_id) is None:
            raise ValueError("Unknown project: {}".format(project_id))
        self.reconcile_port_leases()
        with self.session_factory() as session:
            leased = {
                row.port
                for row in session.query(PortLeaseRecord)
                .filter_by(status="active")
                .all()
            }
        for port in range(port_start, port_end + 1):
            if port in leased or not self._is_port_available(port):
                continue
            lease = PortLeaseRecord(
                id=str(uuid4()),
                project_id=project_id,
                port=port,
                purpose=purpose,
                status="active",
                created_at=utc_now(),
            )
            try:
                with self.session_factory.begin() as session:
                    session.add(lease)
                return lease
            except Exception:
                # A concurrent local harness may have leased the same port.
                continue
        raise RuntimeError(
            "No available port in configured range {}-{}.".format(port_start, port_end)
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
        with self.session_factory.begin() as session:
            preview = session.get(PreviewRecord, preview_id)
            if preview is None:
                raise ValueError("Unknown preview: {}".format(preview_id))
            preview.state = "stopped"
            preview.stopped_at = utc_now()
            lease = session.get(PortLeaseRecord, preview.port_lease_id)
            if lease is not None:
                lease.status = "released"
                lease.released_at = utc_now()
