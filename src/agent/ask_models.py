"""Backwards-compatible convenience helpers around provider CLIs."""

import json
from pathlib import Path
from typing import Any

from .model import ExecutionMode, ModelClient, ModelConfig, ModelProvider


def ask_text(model: str, prompt: str) -> str:
    config = ModelConfig(
        ModelProvider.COMMAND, model, ExecutionMode.TEXT, [model, "{prompt}"]
    )
    return ModelClient(config).ask(prompt, Path.cwd()).stdout


def ask_json(model: str, prompt: str) -> Any:
    return json.loads(ask_text(model, prompt))


def ask_codex(prompt: str) -> str:
    return (
        ModelClient(ModelConfig(ModelProvider.CODEX, "default"))
        .ask(prompt, Path.cwd())
        .stdout
    )


def ask_claude(prompt: str) -> str:
    return (
        ModelClient(ModelConfig(ModelProvider.CLAUDE, "default"))
        .ask(prompt, Path.cwd())
        .stdout
    )
