"""Editable prompt-template loading with minimal, dependency-free .env support."""

from __future__ import annotations

import os
from pathlib import Path
from string import Template
from typing import Dict, Mapping, Optional


class PromptConfigurationError(ValueError):
    pass


STAGES = {
    "planner": "planner.txt",
    "chat_summary": "chat_summary.txt",
    "coder": "coder.txt",
    "tester": "tester.txt",
    "structured_output": "structured_output.txt",
}


def load_dotenv(path: Path, environment: Dict[str, str]) -> None:
    """Load simple KEY=VALUE records without overriding a real process setting."""
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PromptConfigurationError(
            "Could not read .env file: {}".format(path)
        ) from exc
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise PromptConfigurationError(
                "Invalid .env entry at {}:{}".format(path, number)
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise PromptConfigurationError(
                "Missing key in .env at {}:{}".format(path, number)
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        environment.setdefault(key, value)


class PromptLibrary:
    """Resolves each agent stage to an editable text template."""

    def __init__(self, template_paths: Mapping[str, Path]):
        self.template_paths = dict(template_paths)

    @classmethod
    def from_environment(
        cls,
        working_directory: Optional[Path] = None,
        environment_file: Optional[Path] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> "PromptLibrary":
        env = os.environ if environment is None else environment
        working_directory = (working_directory or Path.cwd()).resolve()
        configured_env = env.get("SUPER_AGENT_ENV_FILE")
        env_path = (
            Path(configured_env)
            if configured_env
            else (environment_file or working_directory / ".env")
        )
        if not env_path.is_absolute():
            env_path = working_directory / env_path
        load_dotenv(env_path, env)
        root_value = env.get("SUPER_AGENT_PROMPTS_DIRECTORY", "")
        if root_value:
            prompt_root = Path(root_value)
            if not prompt_root.is_absolute():
                prompt_root = env_path.parent / prompt_root
        else:
            workspace_templates = working_directory / "prompts"
            prompt_root = (
                workspace_templates
                if workspace_templates.is_dir()
                else Path(__file__).parent / "prompt_templates"
            )
        paths = {}
        for stage, filename in STAGES.items():
            override = env.get("SUPER_AGENT_{}_PROMPT_FILE".format(stage.upper()))
            path = Path(override) if override else prompt_root / filename
            if not path.is_absolute():
                path = env_path.parent / path
            paths[stage] = path.resolve()
        return cls(paths)

    def render(self, stage: str, **values: str) -> str:
        path = self.template_paths.get(stage)
        if path is None:
            raise PromptConfigurationError("Unknown prompt stage: {}".format(stage))
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PromptConfigurationError(
                "Prompt template for {} does not exist: {}".format(stage, path)
            ) from exc
        except OSError as exc:
            raise PromptConfigurationError(
                "Could not read prompt template for {}: {}".format(stage, path)
            ) from exc
        try:
            return (
                Template(source)
                .substitute({key: str(value) for key, value in values.items()})
                .strip()
            )
        except (KeyError, ValueError) as exc:
            raise PromptConfigurationError(
                "Invalid {} prompt template ({}). Use $$ for a literal dollar sign.".format(
                    stage, exc
                )
            ) from exc
