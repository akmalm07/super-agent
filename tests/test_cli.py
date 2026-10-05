from agent.cli import build_parser


def test_no_subcommand_opens_the_dashboard():
    assert build_parser().parse_args([]).action is None


def test_run_supports_preview_and_revision_chat_flags():
    arguments = build_parser().parse_args(
        ["run", "project-123", "--preview", "--chat-id", "chat-123"]
    )

    assert arguments.preview
    assert arguments.chat_id == "chat-123"
    assert not hasattr(arguments, "publish")


def test_import_and_export_are_available_for_json_profile_interchange():
    parser = build_parser()

    assert parser.parse_args(["import", "profile.json"]).action == "import"
    exported = parser.parse_args(["export", "project-123", "--output", "out.json"])
    assert exported.action == "export"
    assert exported.project == "project-123"
