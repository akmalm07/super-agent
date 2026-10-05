"""A dependency-free interactive terminal dashboard for local project control."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .agent import PlannerAgent
from .config import ConfigError
from .deployment import LocalPreviewDeployer
from .orchestrator import PipelineOrchestrator
from .persistence import ProjectRecord, SQLiteStore
from .profiles import config_for_project, export_profile, import_profile
from .task import Difficulty, PipelineRequest, ProjectLanguage

LANGUAGES = [
    ProjectLanguage.GO,
    ProjectLanguage.C,
    ProjectLanguage.CPP,
    ProjectLanguage.RUST,
    ProjectLanguage.TYPESCRIPT,
    ProjectLanguage.PYTHON,
    ProjectLanguage.JAVA,
]


class TerminalDashboard:
    """Small, keyboard-driven dashboard designed for a normal terminal session."""

    def __init__(self, database_path: Path):
        self.store = SQLiteStore(database_path)

    def run(self) -> int:
        while True:
            self._show_home()
            choice = input("Choose an action: ").strip().lower()
            try:
                if choice in {"q", "quit", "exit"}:
                    print("Local project state is saved. Goodbye.")
                    return 0
                if choice == "1":
                    self._create_project()
                elif choice == "2":
                    self._show_project()
                elif choice == "3":
                    self._new_suggestion()
                elif choice == "4":
                    self._continue_chat()
                elif choice == "5":
                    self._run_revision()
                elif choice == "6":
                    self._stop_preview()
                elif choice == "7":
                    self._import_profile()
                elif choice == "8":
                    self._export_profile()
                else:
                    print("Enter 1-8, or q to exit.")
            except (ConfigError, OSError, RuntimeError, ValueError) as exc:
                print("\nAction could not be completed: {}\n".format(exc))

    def _show_home(self) -> None:
        projects = self.store.list_projects()
        print("\n" + "=" * 72)
        print("SUPER-AGENT  |  Local Project Control Plane")
        print("Database: {}".format(self.store.database_path))
        print("=" * 72)
        if not projects:
            print("No projects yet. Start with ‘Create a project’.\n")
        else:
            print("Projects")
            for project in projects:
                active_ports = self.store.list_port_leases(project.id)
                print(
                    "  {}  {:18} {:10}  {}{}".format(
                        project.id[:8],
                        project.name[:18],
                        project.language,
                        project.latest_goal[:32],
                        (
                            "  [ports: {}]".format(
                                ", ".join(str(item.port) for item in active_ports)
                            )
                            if active_ports
                            else ""
                        ),
                    )
                )
        print(
            "\n1 Create project  2 View project  3 New suggestion  "
            "4 Continue chat\n5 Run build/revision  6 Stop preview  "
            "7 Import profile  8 Export profile  q Exit\n"
        )

    def _select_project(self) -> ProjectRecord:
        token = input("Project ID (first 8 characters are enough): ").strip()
        matches = [
            project
            for project in self.store.list_projects()
            if project.id.startswith(token)
        ]
        if len(matches) != 1:
            raise ValueError("Choose one project by an unambiguous ID prefix.")
        return matches[0]

    @staticmethod
    def _multiline(label: str) -> str:
        print(label)
        print("Finish with a line containing only a period.")
        lines = []
        while True:
            line = input()
            if line == ".":
                return "\n".join(lines).strip()
            lines.append(line)

    @staticmethod
    def _criteria() -> List[str]:
        print("Acceptance criteria, one per line. Leave a blank line when finished.")
        entries = []
        while True:
            item = input("- ").strip()
            if not item:
                return entries
            entries.append(item)

    def _choose_language(self) -> ProjectLanguage:
        print("Choose the primary project language:")
        for index, language in enumerate(LANGUAGES, start=1):
            print("  {}. {}".format(index, language.value))
        value = input("Language [6 for python]: ").strip() or "6"
        try:
            return LANGUAGES[int(value) - 1]
        except (IndexError, ValueError) as exc:
            raise ValueError("Choose a language number from 1 to 7.") from exc

    def _create_project(self) -> None:
        print("\nCreate a project specification")
        language = self._choose_language()
        source = input("GitHub clone URL: ").strip()
        goal = input("Concrete delivery goal: ").strip()
        chart = self._multiline("Paste Mermaid architecture:")
        criteria = self._criteria()
        constraints = self._criteria_prompt("Constraints (optional), one per line:")
        request = PipelineRequest(
            goal=goal,
            source_repository=source,
            architecture_mermaid=chart,
            acceptance_criteria=criteria,
            constraints=constraints,
            difficulty=Difficulty.MEDIUM,
            language=language,
        )
        decision = PlannerAgent.assess_details(request)
        if not decision.accepted:
            raise ValueError(
                "Intake rejected this project: {}".format(
                    "; ".join(decision.missing_or_invalid)
                )
            )
        project = self.store.get_or_create_project(request)
        print(
            "\nSaved project {} ({}) with language {}.\n".format(
                project.name, project.id[:8], project.language
            )
        )

    @staticmethod
    def _criteria_prompt(label: str) -> List[str]:
        print(label)
        entries = []
        while True:
            item = input("- ").strip()
            if not item:
                return entries
            entries.append(item)

    def _show_project(self) -> None:
        project = self._select_project()
        print("\n{} ({})".format(project.name, project.id))
        print(
            "Language: {}\nRepository: {}".format(
                project.language, project.source_repository
            )
        )
        print("Goal: {}".format(project.latest_goal))
        print(
            "Execution profile: {}".format(
                "configured"
                if self.store.has_execution_profile(project.id)
                else "missing"
            )
        )
        print(
            "\nSuggested next actions: create a focused chat, add feedback to an "
            "existing chat, run a revision with its chat ID, or stop an active preview."
        )
        chats = self.store.list_chats(project.id)
        if chats:
            print("\nRevision chats")
            for chat in chats:
                marker = " summary due" if chat.summary_due else ""
                print(
                    "  {}  {} messages  {}{}".format(
                        chat.id[:8], chat.conversation_count, chat.title[:48], marker
                    )
                )
        previews = self.store.list_previews(project.id)
        if previews:
            print("\nPreviews")
            for preview in previews:
                print(
                    "  {}  {}  {}  pid {}".format(
                        preview.id[:8], preview.state, preview.url, preview.process_id
                    )
                )
        runs = self.store.list_project_runs(project.id)
        if runs:
            print("\nRecent revisions")
            for run in runs:
                print(
                    "  {}  {:15}  {}".format(
                        run.id[:8], run.state, run.commit_sha[:12] or "uncommitted"
                    )
                )
        print()

    def _new_suggestion(self) -> None:
        project = self._select_project()
        suggestion = self._multiline("Describe one focused revision suggestion:")
        chat = self.store.create_chat(project.id, suggestion)
        print(
            "Created separate chat {}. Use action 5 with this chat ID to apply it.\n".format(
                chat.id[:8]
            )
        )

    def _continue_chat(self) -> None:
        chat_id = input("Chat ID: ").strip()
        message = self._multiline("Add a review message:")
        result = self.store.append_chat_message(chat_id, "user", message)
        print(
            "Saved conversation {} in chat {}.".format(
                result.conversation_count, chat_id[:8]
            )
        )
        if result.summary_due:
            print(
                "The seventeenth conversation triggered a summary request. The next "
                "revision run will ask the planner to compact this chat before coding."
            )

    def _run_revision(self) -> None:
        project = self._select_project()
        config, _ = config_for_project(self.store, project.id)
        chat_id = input("Chat ID (blank for a first build): ").strip() or None
        wants_preview = input(
            "Start the configured local preview after tests? [y/N]: "
        ).strip()
        result = PipelineOrchestrator(config).run(
            preview=wants_preview.lower() in {"y", "yes"}, chat_id=chat_id
        )
        print(json_result(result.to_dict()))
        if result.state.value == "review_waiting":
            print("Review is waiting. Add a suggestion or feedback in a separate chat.")

    def _import_profile(self) -> None:
        path = input("JSON profile to import: ").strip()
        _, project = import_profile(path, self.store.database_path)
        print(
            "Imported execution profile for {} ({}). JSON can now be archived or used "
            "only as an export/import backup.\n".format(project.name, project.id[:8])
        )

    def _export_profile(self) -> None:
        project = self._select_project()
        output = input(
            "Output path (blank for the standard project-ID filename): "
        ).strip()
        destination = export_profile(self.store, project.id, output)
        print("Exported {}.\n".format(destination))

    def _stop_preview(self) -> None:
        project = self._select_project()
        previews = [
            preview
            for preview in self.store.list_previews(project.id)
            if preview.state in {"starting", "running"}
        ]
        if not previews:
            print("No active preview for this project.\n")
            return
        for preview in previews:
            print(
                "{}  {}  pid {}".format(preview.id[:8], preview.url, preview.process_id)
            )
        token = input("Preview ID to stop: ").strip()
        matches = [item for item in previews if item.id.startswith(token)]
        if len(matches) != 1:
            raise ValueError("Choose one active preview by an unambiguous ID prefix.")
        preview = matches[0]
        LocalPreviewDeployer.stop_process(preview.process_id)
        self.store.stop_preview(preview.id)
        print("Stopped preview {} and released its port.\n".format(preview.id[:8]))


def json_result(value: dict) -> str:
    import json

    return json.dumps(value, indent=2)


def run_dashboard(database_path: Optional[str] = None) -> int:
    path = Path(database_path or "runs/super-agent.sqlite3")
    return TerminalDashboard(path).run()
