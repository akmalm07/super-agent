"""JSON configuration loading and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import ExecutionMode, ModelConfig
from .task import PipelineRequest


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TestConfig:
    __test__ = False
    commands: List[List[str]]
    timeout_seconds: int = 300

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TestConfig:
        commands = data.get("commands", [])
        if not commands or not all(
            isinstance(command, list) and command for command in commands
        ):
            raise ConfigError(
                "testing.commands must be a non-empty list of argv arrays."
            )
        return cls(
            commands=[[str(part) for part in command] for command in commands],
            timeout_seconds=int(data.get("timeout_seconds", 300)),
        )


@dataclass(frozen=True)
class WorkspaceConfig:
    runs_directory: Path = Path("runs")
    base_branch: str = "main"
    database_path: Optional[Path] = None

    def __post_init__(self) -> None:
        # Keep programmatic configuration self-contained too: a test or
        # embedding caller that supplies only runs_directory gets its database
        # alongside those run artifacts rather than in the process CWD.
        if self.database_path is None:
            object.__setattr__(
                self, "database_path", self.runs_directory / "super-agent.sqlite3"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any], config_file: Path) -> WorkspaceConfig:
        configured = Path(str(data.get("runs_directory", "runs")))
        path = (
            configured if configured.is_absolute() else config_file.parent / configured
        )
        configured_database = data.get("database_path")
        if configured_database is None:
            database_path = path / "super-agent.sqlite3"
        else:
            database = Path(str(configured_database))
            database_path = (
                database if database.is_absolute() else config_file.parent / database
            )
        return cls(
            path.resolve(),
            str(data.get("base_branch", "main")),
            database_path.resolve(),
        )


@dataclass(frozen=True)
class DeploymentConfig:
    """Explicit host-side deployment and health-check commands."""

    enabled: bool = False
    commands: List[List[str]] = field(default_factory=list)
    health_check_commands: List[List[str]] = field(default_factory=list)
    timeout_seconds: int = 300

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeploymentConfig":
        commands = data.get("commands", [])
        health_checks = data.get("health_check_commands", [])

        def valid(values: Any) -> bool:
            return all(isinstance(command, list) and command for command in values)

        if not valid(commands) or not valid(health_checks):
            raise ConfigError("deployment commands must be argv arrays.")
        if data.get("enabled", False) and not commands:
            raise ConfigError(
                "deployment.commands is required when deployment.enabled is true."
            )
        return cls(
            bool(data.get("enabled", False)),
            [[str(part) for part in command] for command in commands],
            [[str(part) for part in command] for command in health_checks],
            int(data.get("timeout_seconds", 300)),
        )


@dataclass(frozen=True)
class CommitConfig:
    """Local commits only. The harness never pushes or opens pull requests."""

    enabled: bool = True
    message_prefix: str = "Super-agent: "

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CommitConfig":
        return cls(
            bool(data.get("enabled", True)),
            str(data.get("message_prefix", "Super-agent: ")),
        )


@dataclass(frozen=True)
class PreviewConfig:
    """A user-declared local preview process with a leased loopback port."""

    enabled: bool = False
    command: List[str] = field(default_factory=list)
    health_check_commands: List[List[str]] = field(default_factory=list)
    health_check_url: str = ""
    port_start: int = 4300
    port_end: int = 4399
    startup_timeout_seconds: int = 20

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PreviewConfig":
        command = data.get("command", [])
        health_checks = data.get("health_check_commands", [])
        if not isinstance(command, list) or not command:
            if data.get("enabled", False):
                raise ConfigError(
                    "preview.command is required when preview.enabled is true."
                )
            command = []
        if not all(isinstance(part, str) and part for part in command):
            raise ConfigError("preview.command must be one non-empty argv array.")
        if data.get("enabled", False) and not any("{port}" in part for part in command):
            raise ConfigError(
                "preview.command must contain {port} so the harness can lease safely."
            )
        if not all(isinstance(item, list) and item for item in health_checks):
            raise ConfigError("preview.health_check_commands must be argv arrays.")
        port_start = int(data.get("port_start", 4300))
        port_end = int(data.get("port_end", 4399))
        if not 1 <= port_start <= port_end <= 65535:
            raise ConfigError("preview port range must be between 1 and 65535.")
        return cls(
            enabled=bool(data.get("enabled", False)),
            command=[str(part) for part in command],
            health_check_commands=[
                [str(part) for part in item] for item in health_checks
            ],
            health_check_url=str(data.get("health_check_url", "")),
            port_start=port_start,
            port_end=port_end,
            startup_timeout_seconds=int(data.get("startup_timeout_seconds", 20)),
        )


@dataclass(frozen=True)
class SandboxConfig:
    """Execution guardrails. Docker is the isolation option; process is not a security boundary."""

    mode: str = "process"
    image: str = ""
    network: str = "none"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SandboxConfig:
        instance = cls(
            str(data.get("mode", "process")),
            str(data.get("image", "")),
            str(data.get("network", "none")),
        )
        if instance.mode not in {"process", "docker"}:
            raise ConfigError("sandbox.mode must be 'process' or 'docker'.")
        if instance.mode == "docker" and not instance.image:
            raise ConfigError("sandbox.image is required when sandbox.mode is docker.")
        return instance


@dataclass(frozen=True)
class HarnessConfig:
    request: PipelineRequest
    planner: ModelConfig
    coder: ModelConfig
    tester: ModelConfig
    testing: TestConfig
    workspace: WorkspaceConfig
    commit: CommitConfig = field(default_factory=CommitConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    deployment: DeploymentConfig = field(default_factory=DeploymentConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], config_file: Path) -> HarnessConfig:
        try:
            instance = cls(
                request=PipelineRequest.from_dict(data["request"]),
                planner=ModelConfig.from_dict(data["planner"]),
                coder=ModelConfig.from_dict(data["coder"]),
                tester=ModelConfig.from_dict(data["tester"]),
                testing=TestConfig.from_dict(data["testing"]),
                workspace=WorkspaceConfig.from_dict(
                    data.get("workspace", {}), config_file
                ),
                commit=CommitConfig.from_dict(data.get("commit", {})),
                sandbox=SandboxConfig.from_dict(data.get("sandbox", {})),
                deployment=DeploymentConfig.from_dict(data.get("deployment", {})),
                preview=PreviewConfig.from_dict(data.get("preview", {})),
            )
            if (
                not instance.planner.model
                or not instance.coder.model
                or not instance.tester.model
            ):
                raise ConfigError("planner, coder, and tester each need a model name.")
            if instance.planner.execution_mode is not ExecutionMode.TEXT:
                raise ConfigError("planner.execution_mode must be text.")
            if instance.coder.execution_mode is ExecutionMode.TEXT:
                raise ConfigError("coder.execution_mode must be workspace or patch.")
            if instance.tester.execution_mode is ExecutionMode.TEXT:
                raise ConfigError("tester.execution_mode must be workspace or patch.")
            for role, model in (
                ("planner", instance.planner),
                ("coder", instance.coder),
                ("tester", instance.tester),
            ):
                if model.structured_output_retries < 0:
                    raise ConfigError(
                        "{}.structured_output_retries cannot be negative.".format(role)
                    )
            for role, model in (("coder", instance.coder), ("tester", instance.tester)):
                if (
                    model.provider.value == "ollama"
                    and model.execution_mode is ExecutionMode.WORKSPACE
                ):
                    raise ConfigError(
                        "{}. Ollama must use patch mode because it cannot edit the workspace through its local API.".format(
                            role
                        )
                    )
            return instance
        except KeyError as exc:
            raise ConfigError(
                f"Missing required configuration section: {exc.args[0]}"
            ) from exc
        except ValueError as exc:
            raise ConfigError(
                "Configuration contains an invalid value: {}".format(exc)
            ) from exc


def load_config(path: str) -> HarnessConfig:
    config_path = Path(path).resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Configuration is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Configuration root must be a JSON object.")
    return HarnessConfig.from_dict(raw, config_path)
