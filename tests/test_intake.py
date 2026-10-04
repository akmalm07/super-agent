from agent.agent import PlannerAgent
from agent.task import PipelineRequest


def valid_request(**overrides):
    values = {
        "goal": "Add reliable keyboard navigation to the project search results.",
        "source_repository": "https://github.com/example/project.git",
        "architecture_mermaid": "flowchart LR\n  UI --> Service --> Store",
        "acceptance_criteria": [
            "Users can select the next search result with the keyboard."
        ],
    }
    values.update(overrides)
    return PipelineRequest(**values)


def test_intake_accepts_complete_specification():
    decision = PlannerAgent.assess_details(valid_request())

    assert decision.accepted
    assert decision.missing_or_invalid == []


def test_intake_rejects_missing_architecture_and_source():
    decision = PlannerAgent.assess_details(
        valid_request(source_repository="", architecture_mermaid="")
    )

    assert not decision.accepted
    assert any(
        item.startswith("source_repository") for item in decision.missing_or_invalid
    )
    assert any(
        item.startswith("architecture_mermaid") for item in decision.missing_or_invalid
    )


def test_intake_rejects_non_clone_github_path():
    decision = PlannerAgent.assess_details(
        valid_request(source_repository="https://github.com/example/project/tree/main")
    )

    assert not decision.accepted
    assert any(
        item.startswith("source_repository") for item in decision.missing_or_invalid
    )
