import subprocess

import pytest

from agent.agent import PatchApplyError, apply_unified_patch
from agent.config import SandboxConfig
from agent.git import GitRepository
from agent.sandbox import SandboxError, SandboxRunner


def test_patch_mode_validates_then_applies_diff(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    target = tmp_path / "message.txt"
    target.write_text("before\n", encoding="utf-8")
    patch = """diff --git a/message.txt b/message.txt
index 9a6c0e1..65c1dbe 100644
--- a/message.txt
+++ b/message.txt
@@ -1 +1 @@
-before
+after
"""

    apply_unified_patch(patch, tmp_path)

    assert target.read_text(encoding="utf-8") == "after\n"


def test_sandbox_refuses_cwd_outside_clone(tmp_path):
    runner = SandboxRunner(tmp_path, SandboxConfig())

    with pytest.raises(SandboxError, match="outside"):
        runner.run(["python", "--version"], tmp_path.parent, 5)


def test_patch_mode_refuses_worktree_escape(tmp_path):
    patch = """diff --git a/../outside.txt b/../outside.txt
new file mode 100644
--- /dev/null
+++ b/../outside.txt
@@ -0,0 +1 @@
+nope
"""

    with pytest.raises(PatchApplyError, match="outside"):
        apply_unified_patch(patch, tmp_path)


def test_clone_push_hook_requires_explicit_owner_opt_in(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    repository = GitRepository(tmp_path)

    repository.block_automatic_pushes()

    hook = tmp_path / ".git" / "hooks" / "pre-push"
    assert "SUPER_AGENT_ALLOW_PUSH" in hook.read_text(encoding="utf-8")
