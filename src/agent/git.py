"""Git/GitHub operations that preserve the user's existing identity."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


class GitError(RuntimeError):
    pass


def _run(command: List[str], cwd: Optional[Path] = None, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitError(f"Required command is not installed: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"Command timed out: {command[0]}") from exc
    if result.returncode:
        raise GitError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"Command failed: {command[0]}"
        )
    return result.stdout.strip()


def safe_branch_slug(goal: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:48]
    return slug or "delivery"


@dataclass(frozen=True)
class GitRepository:
    path: Path

    @classmethod
    def clone(cls, source: str, destination: Path, base_branch: str) -> GitRepository:
        if destination.exists():
            raise GitError(f"Run directory already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--branch",
                base_branch,
                "--single-branch",
                source,
                str(destination),
            ],
            timeout=600,
        )
        return cls(destination.resolve())

    def branch(self, name: str) -> None:
        if not name.startswith("codex/"):
            raise GitError("Harness branches must use the codex/ prefix.")
        _run(["git", "switch", "-c", name], self.path)

    def block_automatic_pushes(self) -> None:
        """Require an explicit user environment opt-in before this clone can push."""
        hooks = self.path / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            'if [ "${SUPER_AGENT_ALLOW_PUSH:-}" != "1" ]; then\n'
            "  echo 'Push blocked by super-agent. The repository owner must explicitly opt in.' >&2\n"
            "  exit 1\n"
            "fi\n",
            encoding="utf-8",
        )
        hook.chmod(hook.stat().st_mode | 0o111)

    def changed_files(self) -> List[str]:
        output = _run(["git", "status", "--porcelain"], self.path)
        return [line[3:] for line in output.splitlines() if line]

    def ensure_user_identity(self) -> None:
        name = _run(["git", "config", "--get", "user.name"], self.path)
        email = _run(["git", "config", "--get", "user.email"], self.path)
        if not name or not email:
            raise GitError(
                "Configure your own git user.name and user.email before committing."
            )

    def commit(self, message: str) -> str:
        if not self.changed_files():
            raise GitError("The agents produced no changes to commit.")
        self.ensure_user_identity()
        _run(["git", "add", "-A"], self.path)
        _run(["git", "commit", "-m", message], self.path, timeout=300)
        return _run(["git", "rev-parse", "HEAD"], self.path)
