import json

from agent.config import (
    CommitConfig,
    HarnessConfig,
    SandboxConfig,
    TestConfig,
    WorkspaceConfig,
)
from agent.model import ExecutionMode, ModelConfig, ModelProvider
from agent.persistence import SQLiteStore
from agent.profiles import (
    config_for_project,
    execution_profile,
    export_profile,
    import_profile,
)
from agent.task import Difficulty, PipelineRequest, ProjectLanguage


def test_project_database_is_the_source_of_truth_for_config_and_export(tmp_path):
    request = PipelineRequest(
        goal="Persist the full execution profile with this project.",
        source_repository="https://github.com/example/profiled.git",
        architecture_mermaid="flowchart LR\nUser --> Harness",
        acceptance_criteria=["The project runs without a JSON input file."],
        constraints=["Keep the execution profile local."],
        difficulty=Difficulty.HIGH,
        task_id="profiled-app",
        language=ProjectLanguage.RUST,
    )
    workspace = WorkspaceConfig(tmp_path / "runs")
    planner = ModelConfig(ModelProvider.COMMAND, "planner", ExecutionMode.TEXT, ["x"])
    coder = ModelConfig(ModelProvider.COMMAND, "coder", ExecutionMode.PATCH, ["x"])
    tester = ModelConfig(ModelProvider.COMMAND, "tester", ExecutionMode.PATCH, ["x"])
    config = HarnessConfig(
        request=request,
        planner=planner,
        coder=coder,
        tester=tester,
        testing=TestConfig([["python", "--version"]]),
        workspace=workspace,
        commit=CommitConfig(enabled=False),
        sandbox=SandboxConfig(),
    )
    store = SQLiteStore(workspace.database_path)
    project = store.get_or_create_project(request)
    store.set_execution_profile(project.id, execution_profile(config))

    restored, restored_project = config_for_project(store, project.id[:8])
    exported = export_profile(store, project.name, str(tmp_path / "backup.json"))
    document = json.loads(exported.read_text(encoding="utf-8"))

    assert restored_project.id == project.id
    assert restored.request == request
    assert restored.coder.execution_mode is ExecutionMode.PATCH
    assert document["request"]["language"] == "rust"
    assert document["planner"]["model"] == "planner"


def test_json_import_can_target_a_different_local_database(tmp_path):
    request = PipelineRequest(
        goal="Import this profile into a selected project database.",
        source_repository="https://github.com/example/imported.git",
        architecture_mermaid="flowchart LR\nImport --> Database",
        acceptance_criteria=["The profile is stored under the selected database."],
    )
    source_workspace = WorkspaceConfig(tmp_path / "source-runs")
    config = HarnessConfig(
        request=request,
        planner=ModelConfig(
            ModelProvider.COMMAND, "planner", ExecutionMode.TEXT, ["x"]
        ),
        coder=ModelConfig(ModelProvider.COMMAND, "coder", ExecutionMode.PATCH, ["x"]),
        tester=ModelConfig(ModelProvider.COMMAND, "tester", ExecutionMode.PATCH, ["x"]),
        testing=TestConfig([["python", "--version"]]),
        workspace=source_workspace,
    )
    document = execution_profile(config)
    document["request"] = request.to_dict()
    source = tmp_path / "portable.profile.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    destination_database = tmp_path / "destination" / "projects.sqlite3"

    store, project = import_profile(str(source), destination_database)
    restored, _ = config_for_project(store, project.id)

    assert store.database_path == destination_database.resolve()
    assert restored.workspace.database_path == destination_database.resolve()
    assert restored.request.source_repository == request.source_repository
