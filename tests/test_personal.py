import json
import sys
from pathlib import Path

import pytest

from agent.project import (
    ProjectError,
    change_project,
    create_project,
    project_status,
    verify_project,
)
from agent.server import configure_deployment, deploy_project, install_nginx

APP = '''
import argparse
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--database', default='data/app.sqlite3')
    args = parser.parse_args()
    db = Path(args.database)
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute('create table if not exists notes (text text)')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                body = Path('static/index.html').read_bytes()
                content_type = 'text/html'
            elif self.path == '/static/app.js':
                body = Path('static/app.js').read_bytes()
                content_type = 'text/javascript'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
'''


def write_app(path: Path, _prompt: str) -> str:
    (path / "static").mkdir(exist_ok=True)
    (path / "tests").mkdir(exist_ok=True)
    (path / "app.py").write_text(APP, encoding="utf-8")
    (path / "static" / "index.html").write_text(
        "<html><body><h1>Notes</h1><script src='/static/app.js'></script></body></html>",
        encoding="utf-8",
    )
    (path / "static" / "app.js").write_text("document.title = 'Notes';\n", encoding="utf-8")
    (path / "static" / "styles.css").write_text("body { color: black; }\n", encoding="utf-8")
    (path / "tests" / "test_app.py").write_text(
        "import unittest\n"
        "from pathlib import Path\n"
        "class AppTest(unittest.TestCase):\n"
        "    def test_frontend_exists(self):\n"
        "        self.assertTrue(Path('static/index.html').is_file())\n",
        encoding="utf-8",
    )
    return "completed"


def test_one_idea_builds_tests_smokes_and_commits(tmp_path):
    path = tmp_path / "notes"
    create_project(
        "a private notes app", output=path, details=["Search notes"],
        flowchart="flowchart LR\nBrowser --> API", criteria=["Notes persist"],
        codex_runner=write_app,
    )

    report = project_status(path)
    assert report["state"] == "completed"
    assert "Tests and HTTP smoke check passed" in report["detail"]
    assert "Search notes" in (path / "SPEC.md").read_text(encoding="utf-8")
    assert "flowchart LR" in (path / "SPEC.md").read_text(encoding="utf-8")
    assert verify_project(path)
    assert (path / ".super-agent" / "latest.json").is_file()


def test_verified_files_can_be_recovered_after_model_timeout(tmp_path):
    def interrupted(path, prompt):
        write_app(path, prompt)
        return "Codex timed out"

    path = tmp_path / "notes"
    create_project("a private notes app", output=path, codex_runner=interrupted)
    assert "verified after Codex timed out" in project_status(path)["detail"]


def test_missing_tests_fail_instead_of_committing(tmp_path):
    def incomplete(path, prompt):
        (path / "app.py").write_text("print('incomplete')\n", encoding="utf-8")
        return "completed"

    path = tmp_path / "notes"
    with pytest.raises(ProjectError, match="Build failed verification"):
        create_project("a private notes app", output=path, codex_runner=incomplete)
    assert project_status(path)["state"] == "failed"
    assert (path / ".git").is_dir()


def test_change_and_auto_deploy_with_health_check(tmp_path):
    path = tmp_path / "notes"
    create_project("a private notes app", output=path, codex_runner=write_app)
    configuration = tmp_path / "deploy.json"
    configuration.write_text(json.dumps({
        "commands": [[sys.executable, "-c", "from pathlib import Path; Path('deployed.txt').write_text('ready')"]],
        "health_check_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('deployed.txt').read_text() == 'ready'"]],
    }), encoding="utf-8")
    configure_deployment(path, configuration, auto=True)
    configure_deployment(path, configuration, auto=True)

    def update(path, prompt):
        (path / "static" / "app.js").write_text("document.title = 'Updated';\n", encoding="utf-8")
        return "completed"

    change_project(path, "Change the title", codex_runner=update)
    assert (path / "deployed.txt").read_text(encoding="utf-8") == "ready"
    assert project_status(path)["state"] == "deployed"


def test_deploy_refuses_missing_health_check(tmp_path):
    path = tmp_path / "notes"
    create_project("a private notes app", output=path, codex_runner=write_app)
    file = tmp_path / "deploy.json"
    file.write_text(json.dumps({"commands": [["echo", "ok"]]}), encoding="utf-8")
    with pytest.raises(ProjectError, match="health_check_commands"):
        configure_deployment(path, file, auto=True)
    with pytest.raises(ProjectError, match="Configure deployment"):
        deploy_project(path)


def test_deploy_failure_is_visible_in_status(tmp_path):
    path = tmp_path / "notes"
    create_project("a private notes app", output=path, codex_runner=write_app)
    file = tmp_path / "deploy.json"
    file.write_text(json.dumps({
        "commands": [["restart", "notes"]],
        "health_check_commands": [["check", "notes"]],
    }), encoding="utf-8")
    configure_deployment(path, file)

    def failing_runner(argv, cwd):
        raise ProjectError("restart failed")

    with pytest.raises(ProjectError, match="restart failed"):
        deploy_project(path, runner=failing_runner)
    assert project_status(path)["state"] == "deploy_failed"


def test_nginx_dry_run_and_failed_validation_restores_file(tmp_path):
    root = tmp_path / "nginx"
    root.mkdir()
    target = root / "notes.conf"
    target.write_text("old", encoding="utf-8")
    source = tmp_path / "new.conf"
    source.write_text("server { listen 8080; }", encoding="utf-8")

    assert "Dry run" in install_nginx(source, root=root, target=target)
    assert target.read_text(encoding="utf-8") == "old"

    def failing_runner(argv, cwd):
        raise ProjectError("nginx validation failed")

    with pytest.raises(ProjectError, match="validation failed"):
        install_nginx(source, root=root, target=target, apply=True, runner=failing_runner)
    assert target.read_text(encoding="utf-8") == "old"


def test_nginx_apply_and_reload_failure_restore(tmp_path):
    root = tmp_path / "nginx"
    root.mkdir()
    target = root / "app.conf"
    target.write_text("old", encoding="utf-8")
    source = tmp_path / "new.conf"
    source.write_text("server { listen 8080; }", encoding="utf-8")
    commands = []

    def success(argv, cwd):
        commands.append(argv)
        assert cwd == root

    install_nginx(source, root=root, target=target, apply=True, runner=success)
    assert target.read_text(encoding="utf-8") == "server { listen 8080; }"
    assert commands == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]

    target.write_text("old", encoding="utf-8")

    def fail_reload(argv, cwd):
        if argv[0] == "systemctl":
            raise ProjectError("reload failed")

    with pytest.raises(ProjectError, match="reload failed"):
        install_nginx(source, root=root, target=target, apply=True, runner=fail_reload)
    assert target.read_text(encoding="utf-8") == "old"
