"""Explicit deployment stage for the machine running the harness."""

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .config import DeploymentConfig, PreviewConfig, SandboxConfig
from .sandbox import CommandResult, SandboxRunner


class CurrentServerDeployer:
    """Runs user-configured deployment commands on the current host after tests pass."""

    def __init__(self, project_directory: Path, config: DeploymentConfig):
        self.project_directory = project_directory
        self.config = config
        # Deployment targets the current host, never the optional build/test container.
        self.runner = SandboxRunner(project_directory, SandboxConfig())

    def deploy(self) -> List[CommandResult]:
        results = []
        for command in self.config.commands:
            result = self.runner.run(
                command, self.project_directory, self.config.timeout_seconds
            )
            results.append(result)
            if result.returncode:
                return results
        for command in self.config.health_check_commands:
            result = self.runner.run(
                command, self.project_directory, self.config.timeout_seconds
            )
            results.append(result)
            if result.returncode:
                return results
        return results


@dataclass(frozen=True)
class PreviewLaunch:
    command: List[str]
    process_id: int
    port: int
    url: str
    healthy: bool


class LocalPreviewDeployer:
    """Starts a configured preview API on a leased loopback port without a shell."""

    def __init__(self, project_directory: Path, config: PreviewConfig, port: int):
        self.project_directory = project_directory
        self.config = config
        self.port = port
        self.runner = SandboxRunner(project_directory, SandboxConfig())

    def start(self) -> PreviewLaunch:
        command = [
            part.replace("{port}", str(self.port)) for part in self.config.command
        ]
        environment = os.environ.copy()
        environment["SUPER_AGENT_PORT"] = str(self.port)
        environment["SUPER_AGENT_HOST"] = "127.0.0.1"
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.project_directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Preview command was not found: {}".format(command[0])
            ) from exc
        url = "http://127.0.0.1:{}".format(self.port)
        if not self._wait_for_port(process):
            self.stop_process(process.pid)
            raise RuntimeError(
                "Preview process did not open leased port {}.".format(self.port)
            )
        if self.config.health_check_url:
            health_url = self.config.health_check_url.replace("{port}", str(self.port))
            try:
                with urllib.request.urlopen(health_url, timeout=5) as response:
                    if response.status >= 400:
                        raise RuntimeError("Preview health endpoint returned an error.")
            except (urllib.error.URLError, OSError) as exc:
                self.stop_process(process.pid)
                raise RuntimeError(
                    "Preview health endpoint failed: {}".format(exc)
                ) from exc
        for health_command in self.config.health_check_commands:
            command_result = self.runner.run(
                [part.replace("{port}", str(self.port)) for part in health_command],
                self.project_directory,
                self.config.startup_timeout_seconds,
            )
            if command_result.returncode:
                self.stop_process(process.pid)
                raise RuntimeError(
                    "Preview health check failed: {}".format(
                        command_result.stderr.strip() or command_result.stdout.strip()
                    )
                )
        return PreviewLaunch(command, process.pid, self.port, url, True)

    def _wait_for_port(self, process: subprocess.Popen) -> bool:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(0.2)
                if connection.connect_ex(("127.0.0.1", self.port)) == 0:
                    return True
            time.sleep(0.1)
        return False

    @staticmethod
    def stop_process(process_id: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                text=True,
                capture_output=True,
                check=False,
            )
            return
        try:
            os.kill(process_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
