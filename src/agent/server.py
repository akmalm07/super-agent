"""Explicit current-host deployment and guarded Nginx configuration."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .project import ProjectError, _git, _latest, _metadata, _save_json, verify_project


def _argv(value: Any, name: str) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        raise ProjectError("{} needs at least one argv array.".format(name))
    for command in value:
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ProjectError("{} must contain nonempty string argv arrays.".format(name))
    return value


def configure_deployment(path: Path, file: Path, *, auto: bool = False) -> None:
    if _git(path, "status", "--porcelain"):
        raise ProjectError("Commit or remove local edits before configuring deployment.")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError("Read a valid deployment JSON file first.") from exc
    if not isinstance(data, dict):
        raise ProjectError("Deployment JSON must be an object.")
    commands = _argv(data.get("commands"), "commands")
    checks = _argv(data.get("health_check_commands"), "health_check_commands")
    metadata = _metadata(path)
    metadata["deployment"] = {
        "commands": commands, "health_check_commands": checks,
    }
    metadata["auto_deploy"] = bool(auto)
    _save_json(path / ".super-agent.json", metadata)
    _git(path, "add", ".super-agent.json")
    if _git(path, "diff", "--cached", "--name-only"):
        _git(
            path, "-c", "user.name=Super-agent", "-c",
            "user.email=local@super-agent.invalid", "commit", "-m", "Configure deployment",
        )


def _run(argv: list[str], cwd: Path, timeout: int = 60) -> None:
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectError("Command could not finish: {} ({})".format(argv[0], exc)) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ProjectError("{} failed: {}".format(argv[0], detail[-1000:]))


def deploy_project(
    path: Path, *, runner: Callable[[list[str], Path], None] = _run,
) -> None:
    deployment = _metadata(path).get("deployment")
    if not isinstance(deployment, dict):
        raise ProjectError("Configure deployment first with `super-agent server configure`.")
    commands = _argv(deployment.get("commands"), "commands")
    checks = _argv(deployment.get("health_check_commands"), "health_check_commands")
    try:
        verify_project(path)
        for command in [*commands, *checks]:
            runner(command, path)
    except ProjectError as exc:
        _latest(path, "deploy_failed", str(exc))
        raise
    _latest(path, "deployed", "Tests, deployment commands, and health checks passed.")


def _safe_target(root: Path, target: Path) -> Path:
    if not root.is_absolute() or not target.is_absolute() or not root.is_dir():
        raise ProjectError("Nginx root and target must be absolute; root must exist.")
    if ".." in target.parts or ".." in root.parts:
        raise ProjectError("Nginx paths cannot contain parent traversal.")
    root = root.resolve(strict=True)
    target = target.absolute()
    if target.parent.resolve(strict=True) != root or target.is_symlink():
        raise ProjectError("Nginx target must be a regular file directly inside the approved root.")
    if target.exists() and not target.is_file():
        raise ProjectError("Nginx target must be a regular file.")
    return target


def _write_atomic(target: Path, content: bytes, mode: int) -> None:
    handle, temporary = tempfile.mkstemp(prefix=".super-agent-", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_nginx(
    file: Path,
    *,
    root: Path,
    target: Path,
    apply: bool = False,
    runner: Callable[[list[str], Path], None] = _run,
) -> str:
    destination = _safe_target(root, target)
    try:
        content = file.read_bytes()
    except OSError as exc:
        raise ProjectError("Cannot read Nginx site file: {}".format(file)) from exc
    if not content or len(content) > 1024 * 1024:
        raise ProjectError("Nginx site file must be nonempty and under 1 MiB.")
    if not apply:
        return "Dry run: {} is ready for review.".format(destination)
    previous = destination.read_bytes() if destination.exists() else None
    mode = stat.S_IMODE(destination.stat().st_mode) if destination.exists() else 0o640
    _write_atomic(destination, content, mode)
    try:
        runner(["nginx", "-t"], destination.parent)
        runner(["systemctl", "reload", "nginx"], destination.parent)
    except Exception:
        if previous is None:
            destination.unlink(missing_ok=True)
        else:
            _write_atomic(destination, previous, mode)
        raise
    return "Installed and reloaded Nginx site: {}".format(destination)
