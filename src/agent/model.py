"""Provider-neutral model invocation without a shell."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .prompts import PromptLibrary
from .structured import StructuredOutputError, parse_json, validate


class ModelProvider(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    OLLAMA = "ollama"
    COMMAND = "command"


class ExecutionMode(str, Enum):
    WORKSPACE = "workspace"  # A coding CLI edits the working directory.
    PATCH = "patch"  # The model returns a unified diff to apply.
    TEXT = "text"  # The model returns text only (suitable for planning).


class ModelInvocationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    provider: ModelProvider
    model: str
    execution_mode: ExecutionMode = ExecutionMode.TEXT
    command: List[str] = field(default_factory=list)
    timeout_seconds: int = 900
    structured_output_retries: int = 2
    ollama_endpoint: str = "http://localhost:11434/api/chat"

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> ModelConfig:
        return cls(
            provider=ModelProvider(str(data.get("provider", ""))),
            model=str(data.get("model", "")),
            execution_mode=ExecutionMode(str(data.get("execution_mode", "text"))),
            command=list(data.get("command", [])),
            timeout_seconds=int(data.get("timeout_seconds", 900)),
            structured_output_retries=int(data.get("structured_output_retries", 2)),
            ollama_endpoint=str(
                data.get("ollama_endpoint", "http://localhost:11434/api/chat")
            ),
        )

    def resolved_command(
        self, prompt: str, output_schema: Optional[Path] = None
    ) -> List[str]:
        """Return argv; custom commands may use model, prompt, and output_schema."""
        command: Sequence[str]
        if self.command:
            command = self.command
        elif self.provider is ModelProvider.CODEX:
            if self.execution_mode is ExecutionMode.TEXT:
                command = [
                    "codex",
                    "exec",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--model",
                    "{model}",
                    "{prompt}",
                ]
            else:
                command = [
                    "codex",
                    "exec",
                    "--approve-for-me",
                    "--sandbox",
                    "workspace-write",
                    "--skip-git-repo-check",
                    "--model",
                    "{model}",
                    "{prompt}",
                ]
        elif self.provider is ModelProvider.CLAUDE:
            command = ["claude", "-p", "--model", "{model}", "{prompt}"]
        elif self.provider is ModelProvider.OLLAMA:
            command = ["ollama", "run", "{model}", "{prompt}"]
        else:
            raise ModelInvocationError("The command provider needs a command array.")
        schema_value = str(output_schema) if output_schema else ""
        resolved = [
            item.replace("{model}", self.model)
            .replace("{prompt}", prompt)
            .replace("{output_schema}", schema_value)
            for item in command
        ]
        if output_schema and self.provider is ModelProvider.CODEX and not self.command:
            resolved[-1:-1] = ["--output-schema", schema_value]
        if output_schema and self.provider is ModelProvider.CLAUDE and not self.command:
            resolved[-1:-1] = ["--output-format", "json"]
        return (
            resolved
            if any("{prompt}" in item for item in command)
            else resolved + [prompt]
        )


@dataclass(frozen=True)
class ModelResult:
    stdout: str
    stderr: str
    command: List[str]


class ModelClient:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.prompts = PromptLibrary.from_environment()

    def ask(self, prompt: str, cwd: Path, runner=None) -> ModelResult:
        command = self.config.resolved_command(prompt)
        if runner is not None:
            result = runner.run(command, cwd, self.config.timeout_seconds)
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip()
                raise ModelInvocationError(
                    f"Provider exited with code {result.returncode}: {detail}"
                )
            return ModelResult(
                result.stdout.strip(),
                result.stderr.strip(),
                self.config.resolved_command("<redacted prompt>"),
            )
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise ModelInvocationError(
                "Provider CLI was not found; install it or configure command."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ModelInvocationError(
                f"Provider timed out after {self.config.timeout_seconds} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ModelInvocationError(
                f"Provider exited with code {completed.returncode}: {detail}"
            )
        return ModelResult(
            completed.stdout.strip(),
            completed.stderr.strip(),
            self.config.resolved_command("<redacted prompt>"),
        )

    def ask_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        cwd: Path,
        runner=None,
        on_attempt=None,
    ) -> Dict[str, Any]:
        """Return a schema-valid object, retrying malformed provider responses once."""
        attempts = 1 + max(0, self.config.structured_output_retries)
        last_error = ""
        for attempt in range(attempts):
            augmented = self._structured_prompt(prompt, schema, last_error)
            if on_attempt is not None:
                on_attempt(augmented, schema, attempt + 1)
            try:
                if (
                    self.config.provider is ModelProvider.OLLAMA
                    and not self.config.command
                ):
                    value = self._ask_ollama_structured(augmented, schema)
                else:
                    schema_path = cwd / ".super-agent-output-schema.json"
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    try:
                        response = self._ask_with_schema(
                            augmented, schema_path, cwd, runner
                        )
                    finally:
                        schema_path.unlink(missing_ok=True)
                    value = self._parse_provider_json(response.stdout)
                validate(value, schema)
                return value
            except (
                ModelInvocationError,
                StructuredOutputError,
                OSError,
                urllib.error.URLError,
            ) as exc:
                last_error = str(exc)
        raise StructuredOutputError(
            "Model did not return a valid structured response after {} attempts: {}".format(
                attempts, last_error
            )
        )

    def _ask_with_schema(
        self, prompt: str, schema_path: Path, cwd: Path, runner=None
    ) -> ModelResult:
        command = self.config.resolved_command(prompt, schema_path)
        if runner is not None:
            result = runner.run(command, cwd, self.config.timeout_seconds)
            if result.returncode:
                raise ModelInvocationError(
                    result.stderr.strip() or result.stdout.strip()
                )
            return ModelResult(result.stdout.strip(), result.stderr.strip(), command)
        return self._run(command, cwd)

    def _run(self, command: List[str], cwd: Path) -> ModelResult:
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise ModelInvocationError(
                "Provider CLI was not found; install it or configure command."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ModelInvocationError(
                "Provider timed out after {} seconds.".format(
                    self.config.timeout_seconds
                )
            ) from exc
        if completed.returncode != 0:
            raise ModelInvocationError(
                completed.stderr.strip() or completed.stdout.strip()
            )
        return ModelResult(completed.stdout.strip(), completed.stderr.strip(), command)

    def _parse_provider_json(self, output: str) -> Dict[str, Any]:
        if self.config.provider is ModelProvider.CLAUDE and not self.config.command:
            envelope = parse_json(output)
            if envelope.get("is_error"):
                raise StructuredOutputError(
                    str(envelope.get("result", "Claude returned an error."))
                )
            result = envelope.get("result")
            if not isinstance(result, str):
                raise StructuredOutputError(
                    "Claude JSON result did not contain response text."
                )
            return parse_json(result)
        return parse_json(output)

    def _ask_ollama_structured(
        self, prompt: str, schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        body = json.dumps(
            {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": schema,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.config.ollama_endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise StructuredOutputError(
                "Ollama returned HTTP {}.".format(exc.code)
            ) from exc
        content = envelope.get("message", {}).get("content")
        if not isinstance(content, str):
            raise StructuredOutputError("Ollama did not return message.content.")
        return parse_json(content)

    def _structured_prompt(
        self, prompt: str, schema: Dict[str, Any], previous_error: str
    ) -> str:
        return self.prompts.render(
            "structured_output",
            base_prompt=prompt,
            json_schema=json.dumps(schema),
            previous_error=previous_error,
        )


# Compatibility aliases from the original scaffold.
ModelType = ModelProvider
Model = ModelConfig
