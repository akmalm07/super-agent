import sys

import pytest

from agent.config import ConfigError, DeploymentConfig, PreviewConfig
from agent.deployment import CurrentServerDeployer, LocalPreviewDeployer


def test_deployer_runs_deploy_then_health_check_on_current_host(tmp_path):
    config = DeploymentConfig(
        enabled=True,
        commands=[[sys.executable, "-c", "print('deployed')"]],
        health_check_commands=[[sys.executable, "-c", "print('healthy')"]],
    )

    results = CurrentServerDeployer(tmp_path, config).deploy()

    assert [result.returncode for result in results] == [0, 0]
    assert "deployed" in results[0].stdout
    assert "healthy" in results[1].stdout


def test_enabled_deployment_requires_a_command():
    with pytest.raises(ConfigError, match="deployment.commands"):
        DeploymentConfig.from_dict({"enabled": True})


def test_preview_requires_the_managed_port_placeholder():
    with pytest.raises(ConfigError, match=r"\{port\}"):
        PreviewConfig.from_dict(
            {"enabled": True, "command": [sys.executable, "-m", "http.server"]}
        )


def test_preview_starts_an_api_on_its_leased_port(tmp_path):
    config = PreviewConfig(
        enabled=True,
        command=[sys.executable, "-m", "http.server", "{port}"],
        port_start=0,
        port_end=0,
        startup_timeout_seconds=5,
    )
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as finder:
        finder.bind(("127.0.0.1", 0))
        port = finder.getsockname()[1]
    deployer = LocalPreviewDeployer(tmp_path, config, port)
    launch = deployer.start()
    try:
        assert launch.healthy
        assert launch.port == port
        assert launch.url.endswith(":{}".format(port))
    finally:
        LocalPreviewDeployer.stop_process(launch.process_id)
