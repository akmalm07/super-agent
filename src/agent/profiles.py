"""Persisted project execution profiles with JSON import/export support."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import HarnessConfig, load_config
from .persistence import ProjectRecord, SQLiteStore


class ProfileError(ValueError):
    pass


def _model_profile(model) -> Dict[str, Any]:
    return {
        "provider": model.provider.value,
        "model": model.model,
        "execution_mode": model.execution_mode.value,
        "command": model.command,
        "timeout_seconds": model.timeout_seconds,
        "structured_output_retries": model.structured_output_retries,
        "ollama_endpoint": model.ollama_endpoint,
    }


def execution_profile(config: HarnessConfig) -> Dict[str, Any]:
    """Return the non-secret runtime profile; project specification stays in SQLite."""
    database_path = config.workspace.database_path
    if database_path is None:
        raise ProfileError("Workspace database path was not initialized.")
    return {
        "planner": _model_profile(config.planner),
        "coder": _model_profile(config.coder),
        "tester": _model_profile(config.tester),
        "testing": {
            "commands": config.testing.commands,
            "timeout_seconds": config.testing.timeout_seconds,
        },
        "workspace": {
            "runs_directory": str(config.workspace.runs_directory),
            "base_branch": config.workspace.base_branch,
            "database_path": str(database_path),
        },
        "commit": {
            "enabled": config.commit.enabled,
            "message_prefix": config.commit.message_prefix,
        },
        "sandbox": {
            "mode": config.sandbox.mode,
            "image": config.sandbox.image,
            "network": config.sandbox.network,
        },
        "deployment": {
            "enabled": config.deployment.enabled,
            "commands": config.deployment.commands,
            "health_check_commands": config.deployment.health_check_commands,
            "timeout_seconds": config.deployment.timeout_seconds,
        },
        "preview": {
            "enabled": config.preview.enabled,
            "command": config.preview.command,
            "health_check_commands": config.preview.health_check_commands,
            "health_check_url": config.preview.health_check_url,
            "port_start": config.preview.port_start,
            "port_end": config.preview.port_end,
            "startup_timeout_seconds": config.preview.startup_timeout_seconds,
        },
    }


def import_profile(
    config_path: str, database_path: Optional[Path] = None
) -> Tuple[SQLiteStore, ProjectRecord]:
    """Import an external JSON profile into the project database once."""
    config = load_config(config_path)
    configured_database = config.workspace.database_path
    if configured_database is None:
        raise ProfileError("Workspace database path was not initialized.")
    destination_database = (database_path or configured_database).resolve()
    store = SQLiteStore(destination_database)
    project = store.get_or_create_project(config.request)
    profile = execution_profile(config)
    profile["workspace"]["database_path"] = str(destination_database)
    store.set_execution_profile(project.id, profile)
    return store, project


def config_for_project(
    store: SQLiteStore, reference: str
) -> Tuple[HarnessConfig, ProjectRecord]:
    project = store.resolve_project(reference)
    data = store.get_execution_profile(project.id)
    data["request"] = store.get_project_request(project.id).to_dict()
    config_file = store.database_path.parent / "stored-project-profile.json"
    config = HarnessConfig.from_dict(data, config_file)
    if config.workspace.database_path != store.database_path:
        raise ProfileError(
            "Project profile points to a different database. Re-import its exported profile "
            "into this database before running it."
        )
    return config, project


def profile_export_path(project: ProjectRecord, directory: Path) -> Path:
    safe_name = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in project.name.lower()
        ).strip("-")
        or "project"
    )
    return directory / "{}.{}.profile.json".format(safe_name, project.id[:8])


def export_profile(store: SQLiteStore, reference: str, output: str = "") -> Path:
    project = store.resolve_project(reference)
    profile = store.get_execution_profile(project.id)
    profile["request"] = store.get_project_request(project.id).to_dict()
    destination = Path(output) if output else profile_export_path(project, Path.cwd())
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    return destination
