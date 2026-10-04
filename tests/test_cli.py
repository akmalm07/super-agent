from agent.cli import build_parser


def test_no_subcommand_opens_the_dashboard():
    assert build_parser().parse_args([]).action is None


def test_run_supports_preview_and_revision_chat_flags():
    arguments = build_parser().parse_args(
        ["run", "pipeline.json", "--preview", "--chat-id", "chat-123"]
    )

    assert arguments.preview
    assert arguments.chat_id == "chat-123"
    assert not hasattr(arguments, "publish")
