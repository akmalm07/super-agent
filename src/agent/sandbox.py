"""Controlled command execution for the cloned project workspace."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .config import SandboxConfig


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str


class SandboxRunner:
    """Runs argv commands under a project root without using a shell.

    ``process`` offers path and timeout guardrails only. Select ``docker`` for
    filesystem/network isolation; the selected image must contain the configured
    provider and test CLIs.
    """

    def __init__(self, root: Path, settings: SandboxConfig):
        self.root = root.resolve()
        self.settings = settings

    def run(
        self, command: Sequence[str], cwd: Path, timeout_seconds: int
    ) -> CommandResult:
        if not command:
            raise SandboxError("Refusing an empty command.")
        safe_cwd = cwd.resolve()
        try:
            safe_cwd.relative_to(self.root)
        except ValueError as exc:
            raise SandboxError(
                "Refusing to execute outside the cloned workspace."
            ) from exc
        argv = list(command)
        run_cwd = safe_cwd
        if self.settings.mode == "docker":
            relative = safe_cwd.relative_to(self.root).as_posix()
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                self.settings.network,
                "-v",
                f"{self.root}:/workspace",
                "-w",
                f"/workspace/{relative}",
                self.settings.image,
            ] + argv
            run_cwd = self.root
        try:
            completed = subprocess.run(
                argv,
                cwd=str(run_cwd),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise SandboxError(f"Command not found: {argv[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(
                f"Command timed out after {timeout_seconds} seconds: {command[0]}"
            ) from exc
        return CommandResult(
            list(command), completed.returncode, completed.stdout, completed.stderr
        )

    def require_success(
        self, command: Sequence[str], cwd: Path, timeout_seconds: int
    ) -> CommandResult:
        result = self.run(command, cwd, timeout_seconds)
        if result.returncode:
            raise SandboxError(
                f"Command failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
            )
        return result
