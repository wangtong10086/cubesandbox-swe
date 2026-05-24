from __future__ import annotations

import subprocess

import pytest

from cubesandbox_swe import cubesandbox_mcp as mcp


def test_normalize_app_path_stays_under_app() -> None:
    assert mcp.normalize_app_path("lib/example.rb") == "/app/lib/example.rb"
    assert mcp.normalize_app_path("/app/lib/../README.md") == "/app/README.md"


def test_normalize_app_path_rejects_escape() -> None:
    with pytest.raises(ValueError, match="must stay under /app"):
        mcp.normalize_app_path("../../etc/passwd")


def test_normalize_absolute_path_rejects_relative() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        mcp.normalize_absolute_path("workspace/file")


def test_executor_run_uses_cubecli_exec(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b"^@ok\r\r\n"
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli", workdir="/app", default_timeout=99)

    result = executor.run("pwd")

    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"
    assert calls[0][0][:3] == ["script", "-q", "-c"]
    assert "sudo /tmp/cubecli exec -i -t sandbox-id /bin/bash -lc" in calls[0][0][3]
    assert "cd /app && pwd" in calls[0][0][3]
    assert calls[0][1]["timeout"] == 99


def test_apply_patch_does_not_put_patch_in_argv(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")
    patch = "diff --git a/a.py b/a.py\n+secret-ish content\n"

    result = executor.apply_patch(patch)

    assert result["exit_code"] == 0
    assert patch not in calls[0][3]
    assert "base64 -d" in calls[0][3]


def test_codex_patch_to_git_diff_update_file() -> None:
    patch = """*** Begin Patch
*** Update File: pycodestyle.py
@@ -1,3 +1,4 @@
 line 1
-line 2
+line two
+line 3
*** End Patch
"""

    converted = mcp.codex_patch_to_git_diff(patch)

    assert converted.startswith("diff --git a/pycodestyle.py b/pycodestyle.py\n")
    assert "--- a/pycodestyle.py\n+++ b/pycodestyle.py\n" in converted
    assert "@@ -1,3 +1,4 @@" in converted
    assert "+line two" in converted


def test_codex_patch_to_git_diff_update_file_without_wrapper() -> None:
    patch = """*** Update File: pycodestyle.py
@@ -1,3 +1,4 @@
 line 1
-line 2
+line two
 line 3
"""

    converted = mcp.codex_patch_to_git_diff(patch)

    assert converted.startswith("diff --git a/pycodestyle.py b/pycodestyle.py\n")
    assert "--- a/pycodestyle.py\n+++ b/pycodestyle.py\n" in converted


def test_codex_patch_to_git_diff_add_file() -> None:
    patch = """*** Begin Patch
*** Add File: new_module.py
+print("hello")
+print("world")
*** End Patch
"""

    converted = mcp.codex_patch_to_git_diff(patch)

    assert "new file mode 100644" in converted
    assert "--- /dev/null\n+++ b/new_module.py\n" in converted
    assert "@@ -0,0 +1,2 @@" in converted


def test_apply_codex_update_hunks_matches_by_context_not_line_number() -> None:
    original = "\n".join(
        [
            "def missing_whitespace_after_keyword(logical_line, tokens):",
            "                tok0.string not in ('async', 'await') and",
            "                not (tok0.string == 'except' and tok1.string == '*') and",
            "                tok1.string not in ':\\n'):",
            '            yield tok0.end, "E275 missing whitespace after keyword"',
            "",
        ]
    )
    hunk = [
        "                 tok0.string not in ('async', 'await') and",
        "                 not (tok0.string == 'except' and tok1.string == '*') and",
        "                 tok1.string not in ':\\n'):",
        "+            if tok0.string == 'yield' and tok1.string == ')':",
        "+                continue",
        '             yield tok0.end, "E275 missing whitespace after keyword"',
    ]

    updated = mcp.apply_codex_update_hunks(original, [hunk], "pycodestyle.py")

    assert "if tok0.string == 'yield' and tok1.string == ')':" in updated


def test_executor_apply_codex_patch_writes_updated_file(monkeypatch) -> None:
    files = {
        "/app/pycodestyle.py": "\n".join(
            [
                "def missing_whitespace_after_keyword(logical_line, tokens):",
                "                tok0.string not in ('async', 'await') and",
                "                not (tok0.string == 'except' and tok1.string == '*') and",
                "                tok1.string not in ':\\n'):",
                '            yield tok0.end, "E275 missing whitespace after keyword"',
                "",
            ]
        )
    }
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    monkeypatch.setattr(executor, "read_text_file", lambda path: files[path])
    monkeypatch.setattr(executor, "write_text_file", lambda path, text, mode=0o644: files.__setitem__(path, text))
    patch = """*** Update File: pycodestyle.py
@@ -495,6 +495,7 @@ def missing_whitespace_after_keyword(logical_line, tokens):
                 tok0.string not in ('async', 'await') and
                 not (tok0.string == 'except' and tok1.string == '*') and
                 tok1.string not in ':\\n'):
+            if tok0.string == 'yield' and tok1.string == ')':
+                continue
             yield tok0.end, "E275 missing whitespace after keyword"
"""

    result = executor.apply_patch(patch)

    assert result["exit_code"] == 0
    assert "if tok0.string == 'yield' and tok1.string == ')':" in files["/app/pycodestyle.py"]


def test_executor_apply_unified_diff_fallback_writes_updated_file(monkeypatch) -> None:
    files = {
        "/app/pycodestyle.py": "\n".join(
            [
                "                tok0.string not in SINGLETONS and",
                "                tok0.string not in ('async', 'await') and",
                "                not (tok0.string == 'except' and tok1.string == '*') and",
                "                tok1.string not in ':\\n'):",
                '            yield tok0.end, "E275 missing whitespace after keyword"',
                "",
            ]
        )
    }

    class Completed:
        returncode = 128
        stdout = b"error: corrupt patch at line 11\n"
        stderr = b""

    monkeypatch.setattr(mcp.subprocess, "run", lambda *args, **kwargs: Completed())
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")
    monkeypatch.setattr(executor, "read_text_file", lambda path: files[path])
    monkeypatch.setattr(executor, "write_text_file", lambda path, text, mode=0o644: files.__setitem__(path, text))
    patch = """diff --git a/pycodestyle.py b/pycodestyle.py
--- a/pycodestyle.py
+++ b/pycodestyle.py
@@ -496,6 +496,7 @@ def missing_whitespace_after_keyword(logical_line, tokens):
                 tok0.string not in SINGLETONS and
                 tok0.string not in ('async', 'await') and
                 not (tok0.string == 'except' and tok1.string == '*') and
+                not (tok0.string == 'yield' and tok1.string == ')') and
                 tok1.string not in ':\\n'):
             yield tok0.end, "E275 missing whitespace after keyword"

"""

    result = executor.apply_patch(patch)

    assert result["exit_code"] == 0
    assert "not (tok0.string == 'yield' and tok1.string == ')') and" in files["/app/pycodestyle.py"]


def test_executor_run_reports_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli", default_timeout=1)

    with pytest.raises(subprocess.TimeoutExpired):
        executor.run("sleep 10")


def test_executor_run_parses_cubecli_exit_from_pty(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stdout = b"^@before\r\r\ncubecli run fail: exec failed with exit code 7\n"
        stderr = b""

    monkeypatch.setattr(mcp.subprocess, "run", lambda *args, **kwargs: Completed())
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    result = executor.run("printf before; exit 7")

    assert result["exit_code"] == 7
    assert result["stdout"] == "before"
    assert result["stderr"] == "cubecli exec failed with exit code 7\n"


def test_diff_disables_git_pager(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    executor.diff("'*.rb'")

    assert "git --no-pager diff --no-ext-diff --no-color --cached" in calls[0][3]


def test_write_text_file_chunks_through_base64(monkeypatch) -> None:
    calls = []

    class Completed:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(mcp.subprocess, "run", fake_run)
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    executor.write_text_file("/workspace/cubesandbox-swe/full_script.sh", "hello", mode=0o755)

    joined = "\n".join(call[3] for call in calls)
    assert "mkdir -p -- /workspace/cubesandbox-swe" in joined
    assert "base64 -d >> /workspace/cubesandbox-swe/full_script.sh" in joined
    assert "chmod 755 -- /workspace/cubesandbox-swe/full_script.sh" in joined


def test_read_text_file_can_return_untruncated_output(monkeypatch) -> None:
    responses = [
        b"__CUBESANDBOX_SIZE_BEGIN__7__CUBESANDBOX_SIZE_END__",
        b"__CUBESANDBOX_B64_BEGIN__Y29udGVudA==__CUBESANDBOX_B64_END__",
    ]

    class Completed:
        returncode = 0
        stderr = b""

        @property
        def stdout(self):
            return responses.pop(0)

    monkeypatch.setattr(mcp.subprocess, "run", lambda *args, **kwargs: Completed())
    executor = mcp.CubeSandboxExecutor("sandbox-id", cubecli="/tmp/cubecli")

    assert executor.read_text_file("/workspace/cubesandbox-swe/stdout.log") == "content"
