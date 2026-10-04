"""Command-line interface and the default interactive terminal dashboard."""

from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from .config import ConfigError, load_config
from .dashboard import run_dashboard
from .orchestrator import PipelineOrchestrator, PipelineState


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="User-controlled local software delivery harness"
    )
    parser.add_argument(
        "--database",
        help="SQLite state database used by the interactive dashboard",
    )
    subcommands = parser.add_subparsers(dest="action")
    dashboard = subcommands.add_parser("dashboard", help="Open the terminal dashboard")
    dashboard.add_argument("--database", dest="dashboard_database")
    for action in ("validate", "run"):
        command = subcommands.add_parser(action)
        command.add_argument("config", help="Path to a harness JSON configuration file")
        if action == "run":
            command.add_argument(
                "--deploy",
                action="store_true",
                help="Run enabled deployment and health-check commands on the current host",
            )
            command.add_argument(
                "--preview",
                action="store_true",
                help="Start the configured preview API on a safely leased local port",
            )
            command.add_argument(
                "--chat-id",
                help="Apply one persisted revision chat during this run",
            )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.action in {None, "dashboard"}:
        return run_dashboard(
            arguments.dashboard_database
            if arguments.action == "dashboard"
            else arguments.database
        )
    try:
        config = load_config(arguments.config)
    except ConfigError as exc:
        print("Configuration error: {}".format(exc))
        return 2
    pipeline = PipelineOrchestrator(config)
    if arguments.action == "validate":
        decision = pipeline.intake()
        print(json.dumps(decision.to_dict(), indent=2))
        return 0 if decision.accepted else 2
    result = pipeline.run(
        deploy=arguments.deploy, preview=arguments.preview, chat_id=arguments.chat_id
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.state is PipelineState.REVIEW_WAITING else 2
