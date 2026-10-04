import sys

import pytest

from agent.memory import RunMemory
from agent.model import ExecutionMode, ModelClient, ModelConfig, ModelProvider
from agent.structured import StructuredOutputError, stage_schema


def decision_schema():
    return stage_schema(
        "intake",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"details": {"type": "string"}},
            "required": ["details"],
        },
    )


def test_structured_custom_command_is_parsed_and_validated(tmp_path):
    response = '{"stage":"intake","decision":"yes","summary":"accepted","reason":"complete","payload":{"details":"ok"}}'
    config = ModelConfig(
        ModelProvider.COMMAND,
        "test",
        ExecutionMode.TEXT,
        [sys.executable, "-c", "print({!r})".format(response)],
        structured_output_retries=1,
    )

    result = ModelClient(config).ask_structured("review", decision_schema(), tmp_path)

    assert result["decision"] == "yes"
    assert result["payload"]["details"] == "ok"


def test_structured_output_rejects_invalid_decision(tmp_path):
    response = '{"stage":"intake","decision":"maybe","summary":"x","reason":"x","payload":{"details":"x"}}'
    config = ModelConfig(
        ModelProvider.COMMAND,
        "test",
        ExecutionMode.TEXT,
        [sys.executable, "-c", "print({!r})".format(response)],
        structured_output_retries=1,
    )

    with pytest.raises(StructuredOutputError, match="decision"):
        ModelClient(config).ask_structured("review", decision_schema(), tmp_path)


def test_codex_native_schema_flag_is_added(tmp_path):
    config = ModelConfig(ModelProvider.CODEX, "gpt-6.1-sol", ExecutionMode.TEXT)

    command = config.resolved_command("review", tmp_path / "schema.json")

    assert "--output-schema" in command
    assert str(tmp_path / "schema.json") in command


def test_run_memory_survives_a_new_instance(tmp_path):
    memory = RunMemory(tmp_path)
    memory.record("intake", {"decision": "yes"})

    reloaded = RunMemory(tmp_path)

    assert reloaded.entries[0]["payload"]["decision"] == "yes"
