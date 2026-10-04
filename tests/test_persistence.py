import socket
import sqlite3

import pytest

from agent.persistence import (
    PromptRecord,
    RunRecord,
    SQLiteStore,
    StructuredOutputRecord,
)
from agent.task import PipelineRequest


def test_sqlite_store_persists_project_run_prompt_and_output(tmp_path):
    request = PipelineRequest(
        goal="Add durable project records to the harness.",
        source_repository="https://github.com/example/project.git",
        architecture_mermaid="flowchart LR\nA --> B",
        acceptance_criteria=["Records survive process restarts."],
    )
    database = tmp_path / "state" / "harness.sqlite3"
    store = SQLiteStore(database)

    project = store.get_or_create_project(request)
    same_project = store.get_or_create_project(request)
    run = store.start_run(project.id, tmp_path / "run-1", request)
    schema = {"type": "object", "properties": {"decision": {"type": "string"}}}
    store.record_prompt(
        run.id,
        "planner",
        1,
        "ollama",
        "test-model",
        "return JSON",
        schema,
    )
    store.record_output(run.id, "intake", schema, {"decision": "yes"})
    store.update_run(run.id, "review_ready", branch="codex/durable", completed=True)

    reopened = SQLiteStore(database)
    assert database.exists()
    assert project.id == same_project.id
    assert reopened.list_projects()[0].id == project.id
    with reopened.session_factory() as session:
        saved_run = session.get(RunRecord, run.id)
        assert saved_run is not None
        assert saved_run.state == "review_ready"
        assert saved_run.completed_at is not None
        assert session.query(PromptRecord).filter_by(run_id=run.id).count() == 1
        saved_output = (
            session.query(StructuredOutputRecord).filter_by(run_id=run.id).one()
        )
        assert saved_output.decision == "yes"


def test_project_chats_trigger_a_summary_on_the_seventeenth_conversation(tmp_path):
    request = PipelineRequest(
        goal="Keep an iterative review history for this project.",
        source_repository="https://github.com/example/reviewable.git",
        architecture_mermaid="flowchart LR\nReview --> Revision",
        acceptance_criteria=["Feedback remains tied to this project."],
    )
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    project = store.get_or_create_project(request)
    chat = store.create_chat(project.id, "First review suggestion")

    for number in range(15):
        result = store.append_chat_message(
            chat.id, "assistant", "reply {}".format(number)
        )
        assert not result.summary_due
    result = store.append_chat_message(chat.id, "user", "seventeenth conversation")

    assert result.conversation_count == 17
    assert result.summary_due
    assert store.chat_needs_summary(chat.id)
    store.record_chat_summary(chat.id, "A durable review summary with accepted scope.")
    assert not store.chat_needs_summary(chat.id)
    assert "durable review" in store.chat_context(chat.id)


def test_port_leases_skip_busy_ports_and_can_be_released(tmp_path):
    request = PipelineRequest(
        goal="Serve a preview from a managed local port.",
        source_repository="https://github.com/example/ports.git",
        architecture_mermaid="flowchart LR\nHarness --> Preview",
        acceptance_criteria=["A preview port is safely reserved."],
    )
    store = SQLiteStore(tmp_path / "harness.sqlite3")
    project = store.get_or_create_project(request)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy_socket:
        busy_socket.bind(("127.0.0.1", 0))
        busy_port = busy_socket.getsockname()[1]
        with pytest.raises(RuntimeError, match="No available port"):
            store.acquire_port(project.id, "preview", busy_port, busy_port)

    lease = store.acquire_port(project.id, "preview", busy_port, busy_port)
    assert lease.port == busy_port
    store.release_port(lease.id)
    assert store.list_port_leases(project.id) == []


def test_existing_first_mvp_database_is_migrated_for_project_control(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE projects (
                id VARCHAR(36) PRIMARY KEY,
                source_repository VARCHAR(1024) UNIQUE NOT NULL,
                name VARCHAR(256) NOT NULL,
                latest_goal TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE runs (
                id VARCHAR(36) PRIMARY KEY,
                project_id VARCHAR(36),
                run_directory TEXT NOT NULL,
                state VARCHAR(64) NOT NULL,
                branch VARCHAR(256) NOT NULL,
                request_json TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                completed_at DATETIME
            );
            """)

    store = SQLiteStore(database)
    with store.session_factory() as session:
        columns = {
            row[1]
            for row in session.connection().exec_driver_sql(
                "PRAGMA table_info(projects)"
            )
        }

    assert {"language", "architecture_mermaid", "default_config_json"} <= columns
