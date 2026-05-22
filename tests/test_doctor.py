from __future__ import annotations

import json
from pathlib import Path

from cubesandbox_swe import doctor


def test_ready_templates_counts_ready_items(tmp_path: Path) -> None:
    state_path = tmp_path / "templates.json"
    state_path.write_text(
        json.dumps(
            {
                "image-a": {"status": "READY", "template_id": "template-a"},
                "image-b": {"status": "FAILED", "template_id": "template-b"},
            }
        ),
        encoding="utf-8",
    )

    check = doctor.check_ready_templates(state_path)

    assert check.status == "ok"
    assert check.detail == "1 READY templates"


def test_first_ready_template_is_deterministic(tmp_path: Path) -> None:
    state_path = tmp_path / "templates.json"
    state_path.write_text(
        json.dumps(
            {
                "image-b": {"status": "READY", "template_id": "template-b"},
                "image-a": {"status": "READY", "template_id": "template-a"},
            }
        ),
        encoding="utf-8",
    )

    assert doctor.first_ready_template(state_path) == ("image-a", "template-a")


def test_build_report_without_runtime_marks_missing_env_as_warning(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    for relative in [
        "pyproject.toml",
        "README.md",
        "docs",
        "third_party/CubeSandbox",
        "third_party/affinetes",
    ]:
        path = root / relative
        if "." in path.name:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        else:
            path.mkdir(parents=True)
    state_path = root / "results" / "cubesandbox_swe_templates.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"image": {"status": "READY", "template_id": "template"}}), encoding="utf-8")
    monkeypatch.setattr(doctor, "BASE_DIR", root)

    args = doctor.parse_args(["--state-path", str(state_path)])
    report = doctor.build_report(args)

    assert report["status"] == "ok"
    env_checks = [check for check in report["checks"] if check["name"] == "env file"]
    assert env_checks == [{"name": "env file", "status": "warn", "detail": f"missing: {root / '.env'}"}]


def test_model_flag_without_value_runs_preflight(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "templates.json"
    state_path.write_text(json.dumps({"image": {"status": "READY", "template_id": "template"}}), encoding="utf-8")
    monkeypatch.setattr(doctor, "check_required_paths", lambda: [])
    monkeypatch.setattr(doctor, "check_model_preflight", lambda args: doctor.Check("model preflight", "ok", args.model))

    args = doctor.parse_args(["--state-path", str(state_path), "--model"])
    report = doctor.build_report(args)

    assert report["status"] == "ok"
    assert report["checks"][-1] == {"name": "model preflight", "status": "ok", "detail": ""}


def test_main_writes_json_report(monkeypatch, tmp_path: Path, capsys) -> None:
    report = {"status": "ok", "checks": [{"name": "sample", "status": "ok", "detail": ""}]}
    monkeypatch.setattr(doctor, "build_report", lambda args: report)
    out_path = tmp_path / "doctor.json"

    assert doctor.main(["--json", str(out_path)]) == 0

    assert json.loads(out_path.read_text(encoding="utf-8")) == report
    assert "[OK] sample" in capsys.readouterr().out
