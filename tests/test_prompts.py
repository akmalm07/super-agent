from pathlib import Path

import pytest

from agent.prompts import PromptConfigurationError, PromptLibrary


def test_env_selects_an_editable_relative_prompt_directory(tmp_path):
    prompt_directory = tmp_path / "my-prompts"
    prompt_directory.mkdir()
    (prompt_directory / "planner.txt").write_text(
        "Custom planner for $goal", encoding="utf-8"
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "SUPER_AGENT_PROMPTS_DIRECTORY=my-prompts\n", encoding="utf-8"
    )

    library = PromptLibrary.from_environment(
        working_directory=tmp_path, environment_file=environment_file, environment={}
    )

    assert library.render("planner", goal="a durable project") == (
        "Custom planner for a durable project"
    )


def test_prompt_templates_fail_clearly_for_missing_variables(tmp_path):
    template = tmp_path / "planner.txt"
    template.write_text("Need $missing_value", encoding="utf-8")
    library = PromptLibrary({"planner": template})

    with pytest.raises(PromptConfigurationError, match="missing_value"):
        library.render("planner", goal="unused")


def test_workspace_prompt_files_are_available_for_each_model_stage():
    library = PromptLibrary.from_environment(
        working_directory=Path.cwd(), environment={}
    )

    assert "planning agent" in library.render(
        "planner",
        goal="A goal",
        source_repository="https://github.com/example/project.git",
        architecture_mermaid="flowchart LR",
        acceptance_criteria="[]",
        constraints="[]",
        language="python",
        revision_context="",
    )


def test_structured_output_template_includes_the_schema_contract():
    library = PromptLibrary.from_environment(
        working_directory=Path.cwd(), environment={}
    )

    rendered = library.render(
        "structured_output",
        base_prompt="Base role instruction",
        json_schema='{"type": "object"}',
        previous_error="",
    )

    assert "Base role instruction" in rendered
    assert '"type": "object"' in rendered
    assert "Return exactly one valid JSON object" in rendered
