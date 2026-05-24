"""MCP tools that execute task operations inside a CubeSandbox sandbox.

The Codex process stays on the host so it can reach the model endpoint, while
all repository reads, commands, and patch application happen through these
CubeSandbox-backed tools against ``/app`` in the task sandbox.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import PurePosixPath
import re
import shlex
import subprocess
import sys
from typing import Any


DEFAULT_CUBECLI = "/usr/local/services/cubetoolbox/Cubelet/bin/cubecli"
DEFAULT_WORKDIR = "/app"
DEFAULT_TIMEOUT = 120
MAX_TEXT_BYTES = 64 * 1024
CUBECLI_EXIT_RE = re.compile(r"(?:\n)?cubecli run fail: exec failed with exit code (\d+)\n?$")
WRITE_CHUNK_BYTES = 48 * 1024
READ_CHUNK_BYTES = 4 * 1024
CODEX_PATCH_BEGIN = "*** Begin Patch"
CODEX_PATCH_END = "*** End Patch"


class CubeSandboxRuntimeError(RuntimeError):
    """Raised when a CubeSandbox-backed tool cannot complete."""


def normalize_app_path(path: str, workdir: str = DEFAULT_WORKDIR) -> str:
    """Return a lexical absolute path under ``workdir`` or raise."""
    if not path:
        raise ValueError("path is required")
    base = PurePosixPath(workdir)
    raw = PurePosixPath(path)
    candidate = raw if raw.is_absolute() else base / raw

    parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "/"}:
            continue
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    normalized = PurePosixPath("/") / PurePosixPath(*parts)
    if normalized != base and base not in normalized.parents:
        raise ValueError(f"path must stay under {workdir}: {path}")
    return str(normalized)


def normalize_absolute_path(path: str) -> str:
    """Return a lexical absolute path or raise."""
    if not path:
        raise ValueError("path is required")
    raw = PurePosixPath(path)
    if not raw.is_absolute():
        raise ValueError(f"path must be absolute: {path}")
    parts: list[str] = []
    for part in raw.parts:
        if part in {"", "/"}:
            continue
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return str(PurePosixPath("/") / PurePosixPath(*parts))


def truncate_text(text: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="replace") + "\n<truncated>\n"


def clean_pty_output(data: bytes) -> str:
    cleaned = (
        data.replace(b"^@", b"")
        .replace(b"\x00", b"")
        .replace(b"\r\r\n", b"\n")
        .replace(b"\r\n", b"\n")
        .replace(b"\r", b"\n")
    )
    return cleaned.decode("utf-8", errors="replace")


def codex_patch_to_git_diff(patch: str) -> str:
    """Convert Codex ``*** Begin Patch`` update/add hunks into a git diff."""
    if not is_codex_patch(patch):
        return patch
    lines, index = codex_patch_body(patch)

    out: list[str] = []
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == CODEX_PATCH_END:
            break
        if stripped == "*** End of File":
            index += 1
            continue

        if line.startswith("*** Update File: "):
            path = line.split(":", 1)[1].strip()
            if not path:
                raise CubeSandboxRuntimeError("update file path is empty")
            out.extend([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"])
            index += 1
            while index < len(lines):
                current = lines[index]
                current_stripped = current.strip()
                if current.startswith("*** "):
                    if current_stripped == "*** End of File":
                        index += 1
                        continue
                    break
                out.append(current)
                index += 1
            continue

        if line.startswith("*** Add File: "):
            path = line.split(":", 1)[1].strip()
            if not path:
                raise CubeSandboxRuntimeError("add file path is empty")
            body: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                if current.startswith("*** "):
                    break
                if not current.startswith("+"):
                    raise CubeSandboxRuntimeError(f"add file line must start with '+': {current[:80]}")
                body.append(current)
                index += 1
            out.extend(
                [
                    f"diff --git a/{path} b/{path}",
                    "new file mode 100644",
                    "--- /dev/null",
                    f"+++ b/{path}",
                    f"@@ -0,0 +1,{len(body)} @@",
                    *body,
                ]
            )
            continue

        if line.startswith("*** Delete File: ") or line.startswith("*** Move to: "):
            raise CubeSandboxRuntimeError(f"unsupported Codex patch operation: {line}")

        raise CubeSandboxRuntimeError(f"unsupported Codex patch line: {line[:80]}")

    if not out:
        raise CubeSandboxRuntimeError("Codex patch did not contain any file hunks")
    return "\n".join(out).rstrip("\n") + "\n"


def is_codex_patch(patch: str) -> bool:
    for raw in patch.splitlines():
        line = raw.strip()
        if not line:
            continue
        return (
            line == CODEX_PATCH_BEGIN
            or raw.startswith("*** Update File: ")
            or raw.startswith("*** Add File: ")
        )
    return False


def codex_patch_body(patch: str) -> tuple[list[str], int]:
    lines = patch.splitlines()
    if not lines:
        raise CubeSandboxRuntimeError("patch is empty")
    if lines[0].strip() == CODEX_PATCH_BEGIN:
        return lines, 1
    return lines, 0


def parse_codex_patch(patch: str) -> list[dict[str, Any]]:
    lines, index = codex_patch_body(patch)
    operations: list[dict[str, Any]] = []
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == CODEX_PATCH_END:
            break
        if stripped == "*** End of File":
            index += 1
            continue

        if line.startswith("*** Update File: "):
            path = line.split(":", 1)[1].strip()
            if not path:
                raise CubeSandboxRuntimeError("update file path is empty")
            hunks: list[list[str]] = []
            hunk: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                current_stripped = current.strip()
                if current.startswith("*** "):
                    if current_stripped == "*** End of File":
                        index += 1
                        continue
                    break
                if current.startswith("@@"):
                    if hunk:
                        hunks.append(hunk)
                    hunk = []
                else:
                    hunk.append(current)
                index += 1
            if hunk:
                hunks.append(hunk)
            if not hunks:
                raise CubeSandboxRuntimeError(f"update file has no hunks: {path}")
            operations.append({"op": "update", "path": path, "hunks": hunks})
            continue

        if line.startswith("*** Add File: "):
            path = line.split(":", 1)[1].strip()
            if not path:
                raise CubeSandboxRuntimeError("add file path is empty")
            body: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                if current.startswith("*** "):
                    break
                if not current.startswith("+"):
                    raise CubeSandboxRuntimeError(f"add file line must start with '+': {current[:80]}")
                body.append(current[1:])
                index += 1
            operations.append({"op": "add", "path": path, "body": body})
            continue

        if line.startswith("*** Delete File: ") or line.startswith("*** Move to: "):
            raise CubeSandboxRuntimeError(f"unsupported Codex patch operation: {line}")
        raise CubeSandboxRuntimeError(f"unsupported Codex patch line: {line[:80]}")
    if not operations:
        raise CubeSandboxRuntimeError("Codex patch did not contain any file operations")
    return operations


def apply_codex_update_hunks(text: str, hunks: list[list[str]], path: str) -> str:
    had_final_newline = text.endswith("\n")
    lines = text.splitlines()
    for hunk in hunks:
        old_lines: list[str] = []
        new_lines: list[str] = []
        for raw in hunk:
            if raw == r"\ No newline at end of file":
                continue
            if not raw:
                raise CubeSandboxRuntimeError(f"malformed empty patch line in {path}")
            marker = raw[0]
            content = raw[1:]
            if marker == " ":
                old_lines.append(content)
                new_lines.append(content)
            elif marker == "-":
                old_lines.append(content)
            elif marker == "+":
                new_lines.append(content)
            else:
                raise CubeSandboxRuntimeError(f"malformed patch line in {path}: {raw[:80]}")
        if not old_lines:
            raise CubeSandboxRuntimeError(f"update hunk has no removable/context lines: {path}")
        variants = [(old_lines, new_lines)]
        if old_lines and old_lines[-1] == "":
            trimmed_new = new_lines[:-1] if new_lines and new_lines[-1] == "" else new_lines
            variants.append((old_lines[:-1], trimmed_new))
        for candidate_old, candidate_new in variants:
            for start in range(0, len(lines) - len(candidate_old) + 1):
                if lines[start : start + len(candidate_old)] == candidate_old:
                    lines[start : start + len(candidate_old)] = candidate_new
                    break
            else:
                continue
            break
        else:
            preview = "\\n".join(old_lines[:5])
            raise CubeSandboxRuntimeError(f"update hunk did not match {path}: {preview[:300]}")
    return "\n".join(lines) + ("\n" if had_final_newline else "")


def parse_unified_diff_patch(patch: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    path: str | None = None
    hunks: list[list[str]] = []
    hunk: list[str] | None = None

    def flush() -> None:
        nonlocal path, hunks, hunk
        if hunk is not None:
            hunks.append(hunk)
            hunk = None
        if path and hunks:
            operations.append({"op": "update", "path": path, "hunks": hunks})
        path = None
        hunks = []

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush()
            continue
        if line.startswith("+++ "):
            candidate = line[4:].strip()
            if candidate != "/dev/null":
                path = candidate[2:] if candidate.startswith("b/") else candidate
            continue
        if line.startswith("@@"):
            if hunk is not None:
                hunks.append(hunk)
            hunk = []
            continue
        if hunk is not None:
            if line == r"\ No newline at end of file":
                hunk.append(line)
            elif line.startswith((" ", "+", "-")):
                hunk.append(line)
            elif line == "":
                hunk.append(" ")
            else:
                raise CubeSandboxRuntimeError(f"malformed unified diff line: {line[:80]}")
    flush()
    if not operations:
        raise CubeSandboxRuntimeError("unified diff did not contain update hunks")
    return operations


class CubeSandboxExecutor:
    """Small command wrapper around ``cubecli exec``."""

    def __init__(
        self,
        sandbox_id: str,
        *,
        cubecli: str = DEFAULT_CUBECLI,
        workdir: str = DEFAULT_WORKDIR,
        default_timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if not sandbox_id:
            raise ValueError("sandbox_id is required")
        self.sandbox_id = sandbox_id
        self.cubecli = cubecli
        self.workdir = workdir
        self.default_timeout = default_timeout

    @classmethod
    def from_env(cls) -> "CubeSandboxExecutor":
        return cls(
            os.environ.get("CUBESANDBOX_SANDBOX_ID", ""),
            cubecli=os.environ.get("CUBESANDBOX_CUBECLI", DEFAULT_CUBECLI),
            workdir=os.environ.get("CUBESANDBOX_WORKDIR", DEFAULT_WORKDIR),
            default_timeout=int(os.environ.get("CUBESANDBOX_COMMAND_TIMEOUT", str(DEFAULT_TIMEOUT))),
        )

    def run(self, command: str, *, timeout: int | None = None, max_bytes: int | None = MAX_TEXT_BYTES) -> dict[str, Any]:
        if not command.strip():
            raise CubeSandboxRuntimeError("command is required")
        wrapped = (
            "export GIT_PAGER=cat PAGER=cat TERM=dumb NO_COLOR=1; "
            f"cd {shlex.quote(self.workdir)} && {command}"
        )
        cubecli_cmd = shlex.join(
            [
                "sudo",
                self.cubecli,
                "exec",
                "-i",
                "-t",
                self.sandbox_id,
                "/bin/bash",
                "-lc",
                wrapped,
            ]
        )
        proc = subprocess.run(
            [
                "script",
                "-q",
                "-c",
                cubecli_cmd,
                "/dev/null",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout or self.default_timeout,
        )
        stdout = clean_pty_output(proc.stdout)
        stderr = clean_pty_output(proc.stderr)
        exit_code = proc.returncode
        match = CUBECLI_EXIT_RE.search(stdout)
        if match:
            exit_code = int(match.group(1))
            stdout = stdout[: match.start()]
            if not stderr:
                stderr = f"cubecli exec failed with exit code {exit_code}\n"
        return {
            "exit_code": exit_code,
            "stdout": truncate_text(stdout, max_bytes) if max_bytes is not None else stdout,
            "stderr": truncate_text(stderr, max_bytes) if max_bytes is not None else stderr,
        }

    def read_file(self, path: str, *, max_bytes: int = MAX_TEXT_BYTES) -> dict[str, Any]:
        safe_path = normalize_app_path(path, self.workdir)
        limit = max(1, min(int(max_bytes), MAX_TEXT_BYTES))
        command = f"LC_ALL=C head -c {limit} -- {shlex.quote(safe_path)}"
        result = self.run(command, timeout=min(self.default_timeout, 30))
        result["path"] = safe_path
        return result

    def apply_patch(self, patch: str) -> dict[str, Any]:
        if not patch.strip():
            raise CubeSandboxRuntimeError("patch is empty")
        if is_codex_patch(patch):
            try:
                return self.apply_codex_patch(patch)
            except CubeSandboxRuntimeError as exc:
                return {"exit_code": 1, "stdout": "", "stderr": str(exc)}
        patch = codex_patch_to_git_diff(patch)
        payload = base64.b64encode(patch.encode("utf-8")).decode("ascii")
        command = (
            "tmp=$(mktemp /tmp/cubesandbox-patch.XXXXXX.diff) && "
            f"printf %s {shlex.quote(payload)} | base64 -d > \"$tmp\" && "
            "git apply --recount --whitespace=fix \"$tmp\"; "
            "rc=$?; rm -f \"$tmp\"; exit \"$rc\""
        )
        result = self.run(command, timeout=self.default_timeout)
        if result["exit_code"] == 0:
            return result
        try:
            return self.apply_unified_diff_patch(patch)
        except CubeSandboxRuntimeError as exc:
            result["stderr"] = (result.get("stderr") or "") + f"unified diff fallback failed: {exc}\n"
            return result

    def apply_codex_patch(self, patch: str) -> dict[str, Any]:
        touched: list[str] = []
        for operation in parse_codex_patch(patch):
            safe_path = normalize_app_path(str(operation["path"]), self.workdir)
            if operation["op"] == "add":
                text = "\n".join(operation["body"]) + "\n"
                self.write_text_file(safe_path, text)
                touched.append(safe_path)
            elif operation["op"] == "update":
                original = self.read_text_file(safe_path)
                updated = apply_codex_update_hunks(original, operation["hunks"], safe_path)
                self.write_text_file(safe_path, updated)
                touched.append(safe_path)
            else:
                raise CubeSandboxRuntimeError(f"unsupported Codex patch operation: {operation['op']}")
        return {
            "exit_code": 0,
            "stdout": "applied Codex patch to " + ", ".join(touched) + "\n",
            "stderr": "",
        }

    def apply_unified_diff_patch(self, patch: str) -> dict[str, Any]:
        touched: list[str] = []
        for operation in parse_unified_diff_patch(patch):
            if operation["op"] != "update":
                raise CubeSandboxRuntimeError(f"unsupported unified diff operation: {operation['op']}")
            safe_path = normalize_app_path(str(operation["path"]), self.workdir)
            original = self.read_text_file(safe_path)
            updated = apply_codex_update_hunks(original, operation["hunks"], safe_path)
            self.write_text_file(safe_path, updated)
            touched.append(safe_path)
        return {
            "exit_code": 0,
            "stdout": "applied unified diff fallback to " + ", ".join(touched) + "\n",
            "stderr": "",
        }


    def write_text_file(self, path: str, text: str, *, mode: int = 0o644) -> None:
        safe_path = normalize_absolute_path(path)
        parent = str(PurePosixPath(safe_path).parent)
        init = f"mkdir -p -- {shlex.quote(parent)} && : > {shlex.quote(safe_path)}"
        result = self.run(init, timeout=min(self.default_timeout, 30))
        if result["exit_code"] != 0:
            raise CubeSandboxRuntimeError(result["stderr"] or result["stdout"])

        data = text.encode("utf-8")
        for start in range(0, len(data), WRITE_CHUNK_BYTES):
            payload = base64.b64encode(data[start : start + WRITE_CHUNK_BYTES]).decode("ascii")
            command = f"printf %s {shlex.quote(payload)} | base64 -d >> {shlex.quote(safe_path)}"
            result = self.run(command, timeout=min(self.default_timeout, 30))
            if result["exit_code"] != 0:
                raise CubeSandboxRuntimeError(result["stderr"] or result["stdout"])

        chmod = self.run(f"chmod {mode:o} -- {shlex.quote(safe_path)}", timeout=min(self.default_timeout, 30))
        if chmod["exit_code"] != 0:
            raise CubeSandboxRuntimeError(chmod["stderr"] or chmod["stdout"])

    def read_text_file(self, path: str, *, max_bytes: int | None = None) -> str:
        safe_path = normalize_absolute_path(path)
        if max_bytes is None:
            size_command = (
                "printf __CUBESANDBOX_SIZE_BEGIN__; "
                f"wc -c < {shlex.quote(safe_path)}; "
                "printf __CUBESANDBOX_SIZE_END__"
            )
            size_result = self.run(size_command, timeout=min(self.default_timeout, 30), max_bytes=None)
            if size_result["exit_code"] != 0:
                raise CubeSandboxRuntimeError(size_result["stderr"] or size_result["stdout"])
            size_match = re.search(
                r"__CUBESANDBOX_SIZE_BEGIN__\s*(\d+)\s*__CUBESANDBOX_SIZE_END__",
                str(size_result["stdout"]),
                re.S,
            )
            if not size_match:
                size_match = re.search(r"__CUBESANDBOX_SIZE_BEGIN__\s*(\d+)", str(size_result["stdout"]), re.S)
            if not size_match:
                detail = str(size_result["stdout"] or size_result["stderr"])[-500:]
                raise CubeSandboxRuntimeError(f"could not determine file size for {safe_path}: {detail}")
            size = int(size_match.group(1))

            data = bytearray()
            for offset in range(0, size, READ_CHUNK_BYTES):
                count = min(READ_CHUNK_BYTES, size - offset)
                command = (
                    "printf __CUBESANDBOX_B64_BEGIN__; "
                    f"dd if={shlex.quote(safe_path)} bs=1 skip={offset} count={count} 2>/dev/null "
                    "| base64 | tr -d '\\n'; "
                    "printf __CUBESANDBOX_B64_END__"
                )
                result = self.run(command, timeout=min(self.default_timeout, 60), max_bytes=None)
                if result["exit_code"] != 0:
                    raise CubeSandboxRuntimeError(result["stderr"] or result["stdout"])
                output = str(result["stdout"])
                match = re.search(r"__CUBESANDBOX_B64_BEGIN__(.*?)__CUBESANDBOX_B64_END__", output, re.S)
                if match:
                    payload = match.group(1).strip()
                else:
                    marker = "__CUBESANDBOX_B64_BEGIN__"
                    marker_index = output.find(marker)
                    if marker_index < 0:
                        detail = output[-500:] or str(result["stderr"])[-500:]
                        raise CubeSandboxRuntimeError(
                            f"could not read chunk from {safe_path} at offset {offset}: {detail}"
                        )
                    payload = output[marker_index + len(marker) :]
                payload = re.sub(r"[^A-Za-z0-9+/=]", "", payload)
                data.extend(base64.b64decode(payload))
            return data.decode("utf-8", errors="replace")

        limit = max(1, int(max_bytes))
        command = f"LC_ALL=C head -c {limit} -- {shlex.quote(safe_path)}"
        result = self.run(command, timeout=min(self.default_timeout, 60), max_bytes=None)
        if result["exit_code"] != 0:
            raise CubeSandboxRuntimeError(result["stderr"] or result["stdout"])
        return str(result["stdout"])

    def diff(self, diff_filter: str) -> dict[str, Any]:
        command = f"git add -A && git --no-pager diff --no-ext-diff --no-color --cached -- {diff_filter}"
        return self.run(command, timeout=min(self.default_timeout, 60))


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "cube_run": {
        "description": "Run a shell command inside the CubeSandbox task sandbox under /app.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 1800},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "cube_read_file": {
        "description": "Read a UTF-8 text file from /app inside the CubeSandbox task sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_TEXT_BYTES},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "cube_apply_patch": {
        "description": "Apply a git patch to the /app repository inside the CubeSandbox task sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
            "additionalProperties": False,
        },
    },
    "cube_diff": {
        "description": "Return the staged git diff for source files in the CubeSandbox task sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


def call_tool(executor: CubeSandboxExecutor, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    diff_filter = os.environ.get(
        "CUBESANDBOX_DIFF_FILTER",
        "'*.js' '*.ts' '*.jsx' '*.tsx' '*.py' '*.java' '*.go' "
        "'*.c' '*.cpp' '*.h' '*.rs' '*.rb' '*.php' '*.cs' "
        "'*.swift' '*.kt' '*.scala' '*.vue' '*.svelte' "
        "'*.yaml' '*.yml' '*.toml' '*.json'",
    )
    if name == "cube_run":
        return executor.run(str(arguments.get("command", "")), timeout=int(arguments.get("timeout") or executor.default_timeout))
    if name == "cube_read_file":
        return executor.read_file(str(arguments.get("path", "")), max_bytes=int(arguments.get("max_bytes") or MAX_TEXT_BYTES))
    if name == "cube_apply_patch":
        return executor.apply_patch(str(arguments.get("patch", "")))
    if name == "cube_diff":
        return executor.diff(diff_filter)
    raise CubeSandboxRuntimeError(f"unknown tool: {name}")


class StdioJsonRpcServer:
    """Minimal MCP-compatible JSON-RPC stdio server."""

    def __init__(self, executor: CubeSandboxExecutor) -> None:
        self.executor = executor
        self.framed: bool | None = None

    def read_message(self) -> dict[str, Any] | None:
        first = sys.stdin.buffer.readline()
        if not first:
            return None
        if first.startswith(b"Content-Length:"):
            self.framed = True
            length = int(first.split(b":", 1)[1].strip())
            while True:
                header = sys.stdin.buffer.readline()
                if header in {b"\r\n", b"\n", b""}:
                    break
                if header.lower().startswith(b"content-length:"):
                    length = int(header.split(b":", 1)[1].strip())
            body = sys.stdin.buffer.read(length)
            return json.loads(body.decode("utf-8"))
        self.framed = False
        return json.loads(first.decode("utf-8"))

    def write_message(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if self.framed:
            sys.stdout.buffer.write(b"Content-Length: " + str(len(data)).encode("ascii") + b"\r\n\r\n" + data)
        else:
            sys.stdout.buffer.write(data + b"\n")
        sys.stdout.buffer.flush()

    def result(self, request_id: Any, result: dict[str, Any]) -> None:
        self.write_message({"jsonrpc": "2.0", "id": request_id, "result": result})

    def error(self, request_id: Any, code: int, message: str) -> None:
        self.write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def serve(self) -> int:
        while True:
            message = self.read_message()
            if message is None:
                return 0
            if "id" not in message:
                continue
            request_id = message["id"]
            method = message.get("method")
            try:
                if method == "initialize":
                    params = message.get("params") or {}
                    self.result(
                        request_id,
                        {
                            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "cubesandbox-swe-runtime", "version": "0.1.0"},
                        },
                    )
                elif method == "ping":
                    self.result(request_id, {})
                elif method == "tools/list":
                    tools = [{"name": name, **schema} for name, schema in TOOL_SCHEMAS.items()]
                    self.result(request_id, {"tools": tools})
                elif method == "tools/call":
                    params = message.get("params") or {}
                    output = call_tool(self.executor, str(params.get("name", "")), params.get("arguments") or {})
                    self.result(
                        request_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(output, indent=2, ensure_ascii=False),
                                }
                            ],
                            "isError": bool(output.get("exit_code")),
                        },
                    )
                else:
                    self.error(request_id, -32601, f"method not found: {method}")
            except Exception as exc:  # noqa: BLE001 - MCP tools should return tool errors, not crash.
                self.result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                        "isError": True,
                    },
                )


def main() -> int:
    return StdioJsonRpcServer(CubeSandboxExecutor.from_env()).serve()


if __name__ == "__main__":
    raise SystemExit(main())
