from agent.config import (
    CommitConfig,
    HarnessConfig,
    SandboxConfig,
    TestConfig,
    WorkspaceConfig,
)
from agent.model import ExecutionMode, ModelConfig, ModelProvider
from agent.orchestrator import PipelineOrchestrator, PipelineState
from agent.task import PipelineRequest


def test_rejected_intake_does_not_clone_or_call_provider(tmp_path):
    model = ModelConfig(ModelProvider.OLLAMA, "qwen", ExecutionMode.TEXT)
    config = HarnessConfig(
        request=PipelineRequest("too short", "", "", []),
        planner=model,
        coder=ModelConfig(ModelProvider.OLLAMA, "qwen", ExecutionMode.PATCH),
        tester=ModelConfig(ModelProvider.OLLAMA, "qwen", ExecutionMode.PATCH),
        testing=TestConfig([["python", "--version"]]),
        workspace=WorkspaceConfig(tmp_path / "runs"),
        commit=CommitConfig(),
        sandbox=SandboxConfig(),
    )

    result = PipelineOrchestrator(config).run()

    assert result.state is PipelineState.INTAKE_REJECTED
    assert not (result.run_directory / "project").exists()
    assert (result.run_directory / "pipeline-report.json").exists()
    assert result.persistence_run_id
    assert result.database_path == str(tmp_path / "runs" / "super-agent.sqlite3")
    assert (tmp_path / "runs" / "super-agent.sqlite3").exists()
