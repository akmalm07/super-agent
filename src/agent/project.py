"""One idea, one local Git project, one Codex edit pass, then real checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class ProjectError(RuntimeError):
    """A build stopped with a message suitable for the owner."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "app"


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ProjectError(result.stderr.strip() or "Git command failed.")
    return result.stdout.strip()


def _save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads((path / ".super-agent.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError("This is not a Super-agent project: {}".format(path)) from exc
    if not isinstance(data, dict):
        raise ProjectError("Project metadata is invalid.")
    return data


def resolve_project(reference: str, base: Path | None = None) -> Path:
    root = Path(base or Path.cwd()).resolve()
    supplied = Path(reference).expanduser()
    choices = [supplied, root / "projects" / reference]
    for choice in choices:
        candidate = choice.resolve()
        if (candidate / ".super-agent.json").is_file():
            return candidate
    raise ProjectError("Project not found: {}".format(reference))


def _prompt(path: Path, request: str, *, repair: str = "") -> str:
    spec = (path / "SPEC.md").read_text(encoding="utf-8")
    return (
        "Build or update the local single-user application in this repository.\n"
        "Follow SPEC.md. Make the actual browser frontend, Python backend, and "
        "SQLite persistence work together. Use the Python standard library "
        "for the backend and no external runtime services.\n"
        "Required files: app.py, static/index.html, static/app.js, "
        "static/styles.css, and meaningful tests/test_*.py files using unittest.\n"
        "app.py must accept --port and --database, bind only to 127.0.0.1, "
        "serve / and /static/app.js, and store data in SQLite. Make a fresh "
        "database when --database points to a new file.\n"
        "Run unittest and JavaScript syntax checks. The host will perform "
        "browser and server checks after your edits. Keep checks finite. "
        "Do not install a service or deploy.\n"
        "Use this Python executable for checks: {}.\n"
        "Write a short README with portable `python` run and test commands.\n\n"
        "Current request: {}\n\nSpecification:\n{}\n{}".format(
            sys.executable, request, spec,
            "Repair these verification failures:\n" + repair if repair else ""
        )
    )


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_codex(path: Path, prompt: str, timeout: int = 600) -> str:
    """Run the installed Codex CLI and bound its entire process tree."""

    executable = os.getenv("SUPER_AGENT_CODEX")
    if not executable and os.name == "nt":
        desktop = Path(os.getenv("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
        candidates = sorted(desktop.glob("*/codex.exe"), key=lambda item: item.stat().st_mtime)
        if candidates:
            executable = str(candidates[-1])
    executable = executable or shutil.which("codex")
    if executable is None:
        raise ProjectError("Codex CLI is missing. Install and sign in to `codex` first.")
    command = [
        executable, "exec", "--approve-for-me", "--skip-git-repo-check",
    ]
    if model := os.getenv("SUPER_AGENT_MODEL"):
        command.extend(["--model", model])
    command.extend(["--ephemeral", "-C", str(path), prompt])
    log_path = path / ".super-agent" / "codex.log"
    log_path.parent.mkdir(exist_ok=True)
    options: dict[str, Any] = {"cwd": path, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n--- Codex build ---\n")
        log.flush()
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        except OSError as exc:
            raise ProjectError("Cannot start Codex: {}".format(exc)) from exc
        deadline = time.monotonic() + timeout
        next_notice = time.monotonic() + 30
        while True:
            try:
                result = process.wait(timeout=min(15, max(0.1, deadline - time.monotonic())))
                return "completed" if result == 0 else "Codex exited with an error; see {}".format(log_path)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    _stop_process(process)
                    return "Codex timed out; see {}".format(log_path)
                if time.monotonic() >= next_notice:
                    print("Codex is building... log: {}".format(log_path), flush=True)
                    next_notice = time.monotonic() + 30


def _command(argv: list[str], path: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            argv, cwd=path, capture_output=True, text=True, timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectError("Command could not finish: {} ({})".format(argv[0], exc)) from exc
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        raise ProjectError("{} failed:\n{}".format(" ".join(argv), output[-4000:]))
    return output


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_smoke(path: Path) -> None:
    port = _free_port()
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "smoke.sqlite3"
        options: dict[str, Any] = {
            "cwd": path, "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(
            [sys.executable, "app.py", "--port", str(port), "--database", str(database)],
            **options,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ProjectError("The app server exited before accepting a request.")
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:{}/".format(port), timeout=1
                    ) as response:
                        page = response.read()
                        if response.status != 200 or b"<html" not in page.lower():
                            raise ProjectError("The app did not serve a usable HTML page.")
                    with urllib.request.urlopen(
                        "http://127.0.0.1:{}/static/app.js".format(port), timeout=1
                    ) as response:
                        if response.status != 200 or not response.read():
                            raise ProjectError("The app did not serve its frontend script.")
                    return
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.2)
            raise ProjectError("The app did not start on its local port within 15 seconds.")
        finally:
            _stop_process(process)


def verify_project(path: Path) -> str:
    required = [
        "app.py", "static/index.html", "static/app.js", "static/styles.css",
    ]
    missing = [item for item in required if not (path / item).is_file()]
    if missing:
        raise ProjectError("Missing app files: {}".format(", ".join(missing)))
    if not (path / "tests").is_dir() or not list((path / "tests").glob("test_*.py")):
        raise ProjectError("Add meaningful unittest tests under tests/test_*.py.")
    output = _command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], path)
    if "Ran 0 tests" in output:
        raise ProjectError("No tests ran.")
    node = shutil.which("node")
    if node is None:
        raise ProjectError("Install Node.js to check the generated frontend JavaScript.")
    _command([node, "--check", "static/app.js"], path)
    _http_smoke(path)
    return output


def _latest(path: Path, state: str, detail: str) -> None:
    _save_json(path / ".super-agent" / "latest.json", {
        "state": state, "detail": detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def _build(
    path: Path,
    request: str,
    *,
    codex_runner: Callable[[Path, str], str] = run_codex,
) -> Path:
    _latest(path, "running", request)
    problem = ""
    model_result = ""
    for attempt in range(2):
        try:
            model_result = codex_runner(path, _prompt(path, request, repair=problem))
        except ProjectError as exc:
            _latest(path, "failed", str(exc))
            raise
        try:
            verify_project(path)
            break
        except ProjectError as exc:
            problem = str(exc)
            if model_result != "completed" and not (path / "app.py").is_file():
                _latest(path, "failed", model_result)
                raise ProjectError(model_result) from exc
            if attempt == 1:
                _latest(path, "failed", "{}; {}".format(model_result, problem))
                raise ProjectError("Build failed verification: {}".format(problem)) from exc
    _git(path, "add", "-A")
    if not _git(path, "diff", "--cached", "--name-only"):
        _latest(path, "failed", "The model made no project changes.")
        raise ProjectError("The model made no project changes.")
    _git(
        path, "-c", "user.name=Super-agent", "-c",
        "user.email=local@super-agent.invalid", "commit", "-m",
        "Build app" if request == "Initial app" else "Update app",
    )
    recovery = " (verified after {})".format(model_result) if model_result != "completed" else ""
    _latest(path, "completed", "Tests and HTTP smoke check passed; commit {}{}".format(_git(path, "rev-parse", "--short", "HEAD"), recovery))
    metadata = _metadata(path)
    if metadata.get("auto_deploy"):
        from .server import deploy_project

        deploy_project(path)
    return path


def create_project(
    idea: str,
    *,
    output: Path | None = None,
    details: list[str] | None = None,
    flowchart: str = "",
    criteria: list[str] | None = None,
    codex_runner: Callable[[Path, str], str] = run_codex,
) -> Path:
    idea = idea.strip()
    if len(idea) < 3:
        raise ProjectError("Describe the app in at least three characters.")
    path = (output or Path.cwd() / "projects" / slug(idea)).resolve()
    if path.exists():
        raise ProjectError("Project already exists: {}".format(path))
    path.mkdir(parents=True)
    (path / ".gitignore").write_text(
        "__pycache__/\n*.pyc\ndata/\n*.sqlite3\n.super-agent/\n.test-data/\n",
        encoding="utf-8",
    )
    _save_json(path / ".super-agent.json", {
        "idea": idea, "auto_deploy": False, "deployment": None,
    })
    spec = ["# {}".format(idea), "", "Build a local, single-user full-stack app."]
    for label, values in (("Details", details or []), ("Success criteria", criteria or [])):
        if values:
            spec.extend(["", "## {}".format(label), *["- {}".format(value) for value in values]])
    if flowchart.strip():
        spec.extend(["", "## Flowchart", "```mermaid", flowchart.strip(), "```"])
    (path / "SPEC.md").write_text("\n".join(spec) + "\n", encoding="utf-8")
    _git(path, "init", "-b", "main")
    _git(path, "add", "-A")
    _git(path, "-c", "user.name=Super-agent", "-c", "user.email=local@super-agent.invalid", "commit", "-m", "Start app")
    return _build(path, "Initial app", codex_runner=codex_runner)


def change_project(
    path: Path, request: str, *, codex_runner: Callable[[Path, str], str] = run_codex
) -> Path:
    if _git(path, "status", "--porcelain"):
        raise ProjectError("Commit or remove local edits before asking for a change.")
    request = request.strip()
    if not request:
        raise ProjectError("Describe the change you want.")
    with (path / "SPEC.md").open("a", encoding="utf-8") as spec:
        spec.write("\n## Change request\n- {}\n".format(request))
    return _build(path, request, codex_runner=codex_runner)


def project_status(path: Path) -> dict[str, Any]:
    report = path / ".super-agent" / "latest.json"
    latest = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return {
        "path": str(path), "idea": _metadata(path).get("idea", ""),
        "commit": _git(path, "rev-parse", "--short", "HEAD"),
        "state": latest.get("state", "new"),
        "detail": latest.get("detail", ""),
    }
