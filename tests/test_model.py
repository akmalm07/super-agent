from agent.model import ExecutionMode, ModelConfig, ModelProvider


def test_custom_model_command_substitutes_without_shell_parsing():
    config = ModelConfig(
        ModelProvider.COMMAND,
        "local-model",
        ExecutionMode.TEXT,
        ["runner", "--model", "{model}", "{prompt}"],
    )

    assert config.resolved_command("use 'quoted' input") == [
        "runner",
        "--model",
        "local-model",
        "use 'quoted' input",
    ]


def test_prompt_is_appended_for_stdin_style_command():
    config = ModelConfig(
        ModelProvider.COMMAND, "x", ExecutionMode.TEXT, ["runner", "--json"]
    )

    assert config.resolved_command("plan this") == ["runner", "--json", "plan this"]


def test_default_codex_planner_is_read_only():
    config = ModelConfig(ModelProvider.CODEX, "gpt-6.1-sol", ExecutionMode.TEXT)

    assert "read-only" in config.resolved_command("make a plan")
    assert "--approve-for-me" not in config.resolved_command("make a plan")
