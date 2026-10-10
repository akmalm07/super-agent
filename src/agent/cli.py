"""Short terminal interface for building and deploying personal apps."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .project import (
    ProjectError,
    change_project,
    create_project,
    project_status,
    resolve_project,
    slug,
)
from .server import configure_deployment, deploy_project, install_nginx


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="super-agent",
        description="Build a private local app from an idea, then run it on your own server.",
        epilog='Try: super-agent new "a private notes app"',
    )
    commands = root.add_subparsers(dest="action")

    new = commands.add_parser("new", help="Build a frontend and backend from one idea")
    new.add_argument("idea")
    new.add_argument("--detail", action="append", default=[])
    new.add_argument("--details-file", type=Path)
    new.add_argument("--flowchart-file", type=Path)
    new.add_argument("--criterion", action="append", default=[])
    new.add_argument("--output", type=Path)

    commands.add_parser("projects", help="List locally built apps")
    status = commands.add_parser("status", help="Show the latest result for an app")
    status.add_argument("project")
    change = commands.add_parser("change", help="Improve an existing app")
    change.add_argument("project")
    change.add_argument("request")

    server = commands.add_parser("server", help="Deploy on this private server or VPS")
    actions = server.add_subparsers(dest="server_action")
    configure = actions.add_parser("configure", help="Save argv deployment and health checks")
    configure.add_argument("project")
    configure.add_argument("--file", type=Path, required=True)
    configure.add_argument("--auto", action="store_true", help="Deploy after successful changes")
    run = actions.add_parser("run", help="Verify and deploy now")
    run.add_argument("project")
    nginx = actions.add_parser("nginx", help="Dry run or install a reviewed Nginx site")
    nginx.add_argument("--file", type=Path, required=True)
    nginx.add_argument("--root", type=Path, required=True)
    nginx.add_argument("--target", type=Path, required=True)
    nginx.add_argument("--apply", action="store_true")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.action is None:
        parser().print_help()
        return 0
    try:
        if arguments.action == "new":
            details = list(arguments.detail)
            if arguments.details_file:
                details.append(arguments.details_file.read_text(encoding="utf-8"))
            flowchart = (
                arguments.flowchart_file.read_text(encoding="utf-8")
                if arguments.flowchart_file else ""
            )
            output = (arguments.output or Path.cwd() / "projects" / slug(arguments.idea)).resolve()
            print("Creating {}".format(output), flush=True)
            created = create_project(
                arguments.idea, output=output, details=details,
                flowchart=flowchart, criteria=arguments.criterion,
            )
            print("Ready: {}".format(created))
            return 0
        if arguments.action == "projects":
            directory = Path.cwd() / "projects"
            projects = sorted(directory.glob("*/.super-agent.json")) if directory.exists() else []
            if not projects:
                print('No apps yet. Try `super-agent new "a private notes app"`.')
            for metadata in projects:
                report = project_status(metadata.parent)
                print("{}  {}  {}".format(metadata.parent.name, report["state"], report["path"]))
            return 0
        if arguments.action == "status":
            report = project_status(resolve_project(arguments.project))
            for label in ("idea", "path", "state", "commit", "detail"):
                print("{}: {}".format(label.title(), report[label]))
            return 0
        if arguments.action == "change":
            path = resolve_project(arguments.project)
            print("Updating {}".format(path), flush=True)
            change_project(path, arguments.request)
            print("Ready: {}".format(path))
            return 0
        if arguments.action == "server":
            if arguments.server_action == "configure":
                path = resolve_project(arguments.project)
                configure_deployment(path, arguments.file, auto=arguments.auto)
                print("Deployment configured for {}. Automatic: {}".format(
                    path.name, "on" if arguments.auto else "off"
                ))
                return 0
            if arguments.server_action == "run":
                path = resolve_project(arguments.project)
                deploy_project(path)
                print("Deployment and health checks passed: {}".format(path.name))
                return 0
            if arguments.server_action == "nginx":
                print(install_nginx(
                    arguments.file, root=arguments.root, target=arguments.target,
                    apply=arguments.apply,
                ))
                return 0
            parser().error("Choose `server configure`, `server run`, or `server nginx`.")
    except (ProjectError, OSError, ValueError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
