#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
AFFINE_EXP_DIR="${AFFINE_EXP_DIR:-results/hint_eval_full/affine50_20260523T174013Z}"
TARGET_RESULTS="${TARGET_RESULTS:-200}"
POLL_SECONDS="${POLL_SECONDS:-300}"
CODEX_BIN="${CODEX_BIN:-codex}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
CODEX_RESUME_SELECTOR="${CODEX_RESUME_SELECTOR:---last}"
QWEN32_MODEL="${QWEN32_MODEL:-Qwen/Qwen3-32B}"
WAKE_MODE="${WAKE_MODE:-codex-resume}"
WRITE_TTY_NOTIFICATION="${WRITE_TTY_NOTIFICATION:-1}"
DRY_RUN=0
ONCE=0

usage() {
  cat <<'EOF'
Usage: scripts/watch_affine_then_qwen32.sh [--once] [--dry-run]

Monitor the current Affine hint-eval experiment. Once the Affine plan1 pipeline
is complete, write a Codex wakeup prompt and send it to the most recent
interactive Codex session with `codex exec resume --last`.

Environment overrides:
  ROOT                  workspace root
  AFFINE_EXP_DIR        Affine experiment directory
  TARGET_RESULTS        expected rollout result count
  POLL_SECONDS          polling interval
  PYTHON_BIN            Python interpreter for status checks
  QWEN32_MODEL          next student model name
  CODEX_RESUME_SELECTOR selector for `codex exec resume` (default: --last)
  WAKE_MODE             codex-resume or file-only
  WRITE_TTY_NOTIFICATION 1 to also print a short notice to the Codex TTY
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --once)
      ONCE=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

cd "$ROOT"

STATE_DIR="${STATE_DIR:-${AFFINE_EXP_DIR}/wakeup_qwen32}"
mkdir -p "$STATE_DIR"

LOG_FILE="${LOG_FILE:-${STATE_DIR}/watch.log}"
STATE_FILE="${STATE_FILE:-${STATE_DIR}/status.json}"
PROMPT_FILE="${PROMPT_FILE:-${STATE_DIR}/codex_resume_prompt.md}"
SENTINEL="${SENTINEL:-${STATE_DIR}/wakeup.sent}"
LOCK_DIR="${LOCK_DIR:-${STATE_DIR}/watch.lock}"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE"
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "${LOCK_DIR}/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi
  local old_pid
  old_pid="$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    log "another watcher appears to be running pid=${old_pid} lock=${LOCK_DIR}"
    exit 0
  fi
  log "removing_stale_lock lock=${LOCK_DIR} pid=${old_pid:-unknown}"
  rm -rf "$LOCK_DIR"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "${LOCK_DIR}/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi
  log "another watcher appears to be running lock=${LOCK_DIR}"
  exit 0
}

write_status() {
  "$PYTHON_BIN" - "$AFFINE_EXP_DIR" "$TARGET_RESULTS" "$STATE_FILE" <<'PY'
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

exp = pathlib.Path(sys.argv[1])
target = int(sys.argv[2])
state_file = pathlib.Path(sys.argv[3])
runs = exp / "student_rollouts" / "runs"

counts = collections.Counter()
errors = []
for path in sorted(runs.glob("*/*/result.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        counts["invalid_json"] += 1
        errors.append({"path": str(path), "error": str(exc)})
        continue
    counts[str(payload.get("status"))] += 1
    if payload.get("status") == "error":
        errors.append({"path": str(path), "error": payload.get("error")})

result_count = sum(counts.values())

pid_path = exp / "plan1_pipeline.pid"
pipeline_pid = None
pipeline_running = False
if pid_path.exists():
    try:
        pipeline_pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pipeline_pid, 0)
        pipeline_running = True
    except Exception:
        pipeline_running = False

plan_logs = sorted(exp.glob("plan1_pipeline_*.log"), key=lambda p: p.stat().st_mtime)
latest_plan_log = plan_logs[-1] if plan_logs else None
plan_log_tail = ""
plan_complete_marker = False
if latest_plan_log and latest_plan_log.exists():
    text = latest_plan_log.read_text(encoding="utf-8", errors="replace")
    plan_complete_marker = "plan1_pipeline_complete" in text
    plan_log_tail = "\n".join(text.splitlines()[-20:])

validation_path = exp / "plan1_validation.json"
validation = None
validation_ok = False
if validation_path.exists():
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        validation_ok = (
            validation.get("result_json") == target
            and validation.get("online_results.jsonl") == target
            and validation.get("probes.jsonl", 0) > 0
            and validation.get("scores.affine.jsonl") == validation.get("probes.jsonl")
            and not validation.get("bad_score_condition_rows")
        )
    except Exception as exc:
        validation = {"parse_error": str(exc)}

final_report = exp / "final_report.md"
final_report_exists = final_report.exists() and final_report.stat().st_size > 0
complete = bool(plan_complete_marker and validation_ok and final_report_exists)

collector_running = subprocess.run(
    ["pgrep", "-f", "collect_cubesandbox_codex_swe50"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
).returncode == 0

if complete:
    state = "complete"
elif pipeline_running or collector_running:
    state = "running"
elif result_count >= target and not validation_ok:
    state = "postprocess_incomplete"
else:
    state = "waiting"

summary = {
    "schema_version": "affine_qwen32_watcher_status_v1",
    "timestamp_unix": time.time(),
    "affine_exp_dir": str(exp),
    "target_results": target,
    "result_count": result_count,
    "status_counts": dict(counts),
    "error_samples": errors[:10],
    "pipeline_pid": pipeline_pid,
    "pipeline_running": pipeline_running,
    "collector_running": collector_running,
    "latest_plan_log": str(latest_plan_log) if latest_plan_log else None,
    "plan_complete_marker": plan_complete_marker,
    "validation_path": str(validation_path),
    "validation_ok": validation_ok,
    "validation": validation,
    "final_report": str(final_report),
    "final_report_exists": final_report_exists,
    "state": state,
    "plan_log_tail": plan_log_tail,
}
state_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"{state} result_json={result_count}/{target} statuses={dict(counts)}")
PY
}

state_value() {
  "$PYTHON_BIN" - "$STATE_FILE" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1], encoding="utf-8").read()).get("state", "unknown"))
PY
}

write_prompt() {
  local timestamp
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local qwen32_exp_hint
  qwen32_exp_hint="results/hint_eval_full/qwen32_${timestamp}"
  cat > "$PROMPT_FILE" <<EOF
Affine plan1 experiment has completed. Continue from ${ROOT}.

Goal:
Switch the student model to ${QWEN32_MODEL} and run the same hint-eval/SWE setup used for the just-finished Affine run. Keep the experiment configuration unchanged except for the student model and the new output directory.

Use these completed Affine artifacts as the handoff point:
- Affine experiment: ${AFFINE_EXP_DIR}
- Validation: ${AFFINE_EXP_DIR}/plan1_validation.json
- Report: ${AFFINE_EXP_DIR}/final_report.md
- Watcher status: ${STATE_FILE}

Required constraints:
- Do not overwrite Affine outputs.
- Do not duplicate fields in .env. Use existing QWEN_* fields if they have already been updated for Qwen3 32B; otherwise use explicit environment overrides for this run.
- Preserve the same task set, seed manifest, 200 rollout target, 4 reps per task, reasoning effort, wire API, timeouts, retry settings, strict model-failure classification, prefix collection, support, on-policy probe building, scoring, ablation, prefix-group comparison, and report structure.
- Create a new experiment directory, for example ${qwen32_exp_hint}.
- Before starting the long run, verify that the local OpenAI-compatible endpoint is actually serving ${QWEN32_MODEL}; if not, update the running endpoint/proxy/deployment path as needed, then run a provider check.
- Once the Qwen3 32B run can proceed, start it in the background and write PID/log files just like the Affine run.

Suggested first actions:
1. Inspect ${AFFINE_EXP_DIR}/run_plan1_offline_pipeline.sh and ${AFFINE_EXP_DIR}/student_rollouts/background_collect_loop.sh.
2. Generate Qwen3 32B equivalents under a new qwen32 experiment directory with only model/output paths changed.
3. Confirm endpoint/model readiness, then launch rollout collection and its offline pipeline.
EOF
  log "wrote_prompt path=${PROMPT_FILE}"
}

notify_tty() {
  [ "$WRITE_TTY_NOTIFICATION" = "1" ] || return 0
  local tty_path
  tty_path="$(
    ps -eo pid,tty,cmd \
      | awk '$2 != "?" && /codex resume/ {print "/dev/" $2; exit}'
  )"
  if [ -n "$tty_path" ] && [ -w "$tty_path" ]; then
    {
      printf '\a\n'
      printf '[hint-eval] Affine complete; queued Qwen3 32B handoff via %s\n' "$PROMPT_FILE"
    } > "$tty_path" || true
    log "wrote_tty_notification tty=${tty_path}"
  else
    log "tty_notification_skipped"
  fi
}

wake_codex() {
  if [ -e "$SENTINEL" ]; then
    log "wakeup_already_sent sentinel=${SENTINEL}"
    return 0
  fi

  write_prompt
  printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$SENTINEL"
  notify_tty

  if [ "$DRY_RUN" = "1" ] || [ "$WAKE_MODE" = "file-only" ]; then
    log "wakeup_file_only dry_run=${DRY_RUN} wake_mode=${WAKE_MODE}"
    return 0
  fi

  if [ "$WAKE_MODE" != "codex-resume" ]; then
    log "unknown_wake_mode=${WAKE_MODE}; prompt left at ${PROMPT_FILE}"
    return 1
  fi

  if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
    log "codex_binary_missing bin=${CODEX_BIN}; prompt left at ${PROMPT_FILE}"
    return 1
  fi

  local timestamp codex_log codex_last runner
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  codex_log="${STATE_DIR}/codex_resume_${timestamp}.jsonl"
  codex_last="${STATE_DIR}/codex_resume_${timestamp}.last_message.txt"
  runner="${STATE_DIR}/run_codex_resume_${timestamp}.sh"

  cat > "$runner" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$ROOT"
read -r -a selector <<< "$CODEX_RESUME_SELECTOR"
"$CODEX_BIN" exec resume \\
  --dangerously-bypass-approvals-and-sandbox \\
  --skip-git-repo-check \\
  --json \\
  -o "$codex_last" \\
  "\${selector[@]}" \\
  - < "$PROMPT_FILE" > "$codex_log" 2>&1
EOF
  chmod +x "$runner"
  setsid -f bash "$runner"
  sleep 2
  local pid
  pid="$(pgrep -f "$runner" | head -1 || true)"
  if [ -n "$pid" ]; then
    printf '%s\n' "$pid" > "${STATE_DIR}/codex_resume.pid"
    log "codex_resume_started pid=${pid} log=${codex_log} last_message=${codex_last}"
  else
    log "codex_resume_started log=${codex_log} last_message=${codex_last}"
  fi
}

main() {
  acquire_lock
  log "watch_start affine_exp_dir=${AFFINE_EXP_DIR} target=${TARGET_RESULTS} poll_seconds=${POLL_SECONDS} qwen32_model=${QWEN32_MODEL}"
  while true; do
    local status_line state
    status_line="$(write_status)"
    state="$(state_value)"
    log "${status_line}"
    if [ "$state" = "complete" ]; then
      wake_codex
      log "watch_complete"
      exit 0
    fi
    if [ "$ONCE" = "1" ]; then
      log "watch_once_exit state=${state}"
      exit 0
    fi
    sleep "$POLL_SECONDS"
  done
}

main
