"""CLI for persisted projects; JSON files are import/export artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .config import ConfigError, load_config
from .dashboard import run_dashboard
from .orchestrator import PipelineOrchestrator, PipelineState
from .persistence import SQLiteStore
from .profiles import ProfileError, config_for_project, export_profile, import_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="User-controlled local software delivery harness"
    )
    parser.add_argument(
        "--database",
        help="SQLite project database; defaults to runs/super-agent.sqlite3",
    )
    subcommands = parser.add_subparsers(dest="action")
    dashboard = subcommands.add_parser("dashboard", help="Open the terminal dashboard")
    dashboard.add_argument("--database", dest="dashboard_database")

    validate = subcommands.add_parser(
        "validate", help="Validate an importable JSON profile"
    )
    validate.add_argument("config", help="Path to a JSON profile file")

    importer = subcommands.add_parser(
        "import", help="Import a JSON profile into SQLite"
    )
    importer.add_argument("config", help="Path to a JSON profile file")

    exporter = subcommands.add_parser(
        "export", help="Export a persisted project as JSON"
    )
    exporter.add_argument("project", help="Project ID, unique ID prefix, or exact name")
    exporter.add_argument("--output", help="Destination JSON path")

    run = subcommands.add_parser("run", help="Run a persisted project")
    run.add_argument("project", help="Project ID, unique ID prefix, or exact name")
    run.add_argument(
        "--deploy",
        action="store_true",
        help="Run enabled deployment and health-check commands on the current host",
    )
    run.add_argument(
        "--preview",
        action="store_true",
        help="Start the configured preview API on a safely leased local port",
    )
    run.add_argument(
        "--chat-id", help="Apply one persisted revision chat during this run"
    )
    return parser


def _store(database: Optional[str]) -> SQLiteStore:
    return SQLiteStore(Path(database or "runs/super-agent.sqlite3"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.action in {None, "dashboard"}:
        return run_dashboard(
            arguments.dashboard_database
            if arguments.action == "dashboard"
            else arguments.database
        )
    try:
        if arguments.action == "validate":
            decision = PipelineOrchestrator(load_config(arguments.config)).intake()
            print(json.dumps(decision.to_dict(), indent=2))
            return 0 if decision.accepted else 2
        if arguments.action == "import":
            store, project = import_profile(
                arguments.config,
                Path(arguments.database).resolve() if arguments.database else None,
            )
            print(
                json.dumps(
                    {
                        "project_id": project.id,
                        "project_name": project.name,
                        "database_path": str(store.database_path),
                    },
                    indent=2,
                )
            )
            return 0
        store = _store(arguments.database)
        if arguments.action == "export":
            destination = export_profile(
                store, arguments.project, arguments.output or ""
            )
            print(json.dumps({"profile_path": str(destination)}, indent=2))
            return 0
        config, _ = config_for_project(store, arguments.project)
        result = PipelineOrchestrator(config).run(
            deploy=arguments.deploy,
            preview=arguments.preview,
            chat_id=arguments.chat_id,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.state is PipelineState.REVIEW_WAITING else 2
    except (ConfigError, ProfileError, ValueError) as exc:
        print("Configuration error: {}".format(exc))
        return 2
