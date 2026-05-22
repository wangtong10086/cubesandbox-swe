#!/usr/bin/env python3
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
RESULTS_PATH = str(BASE_DIR / "results" / "swe_infinite_images_50_results.json")
STATE_PATH = str(BASE_DIR / "results" / "cubesandbox_swe_templates.json")
CLI = "/usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli"
ADDRESS = "127.0.0.1"
PORT = "8089"
NODE = "192.168.34.1"
WORKERS = 2
POLL_INTERVAL = 10
JOB_TIMEOUT = 2400


state_lock = threading.Lock()


def run(cmd, timeout=120):
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def template_id_for(image):
    tag = image.rsplit(":", 1)[1]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", tag).strip("-").lower()
    slug = re.sub(r"-+", "-", slug)
    digest = hashlib.sha1(image.encode()).hexdigest()[:8]
    base = f"swe-{slug}"
    if len(base) > 95:
        base = base[:95].rstrip("-")
    if base == "swe-asottile-dead-cf792cdc-199":
        return base
    return f"{base}-{digest}"


def load_images():
    data = json.load(open(RESULTS_PATH))
    images = []
    for item in data:
        if item.get("status") == "ok":
            images.append(item["image"])
    return images


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except FileNotFoundError:
        return {}


def save_template_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, sort_keys=True)


def template_info(template_id):
    rc, out = run(
        [
            CLI,
            "--address",
            ADDRESS,
            "--port",
            PORT,
            "tpl",
            "info",
            "--template-id",
            template_id,
            "--json",
        ],
        timeout=60,
    )
    if rc != 0:
        return None, out
    try:
        return json.loads(out), out
    except json.JSONDecodeError:
        return None, out


def submit_template(image, template_id):
    cmd = [
        CLI,
        "--address",
        ADDRESS,
        "--port",
        PORT,
        "tpl",
        "create-from-image",
        "--image",
        image,
        "--template-id",
        template_id,
        "--writable-layer-size",
        "1Gi",
        "--cpu",
        "500",
        "--memory",
        "512",
        "--node",
        NODE,
        "--cmd",
        "/bin/sh",
        "--cmd",
        "-lc",
        "--arg",
        "sleep infinity",
        "--json",
    ]
    rc, out = run(cmd, timeout=120)
    if rc != 0:
        raise RuntimeError(out)
    payload = json.loads(out)
    ret = payload.get("ret") or {}
    if ret.get("ret_code") != 200:
        raise RuntimeError(out)
    return payload["job"]["job_id"]


def poll_job(job_id):
    deadline = time.monotonic() + JOB_TIMEOUT
    last = None
    while True:
        rc, out = run(
            [
                CLI,
                "--address",
                ADDRESS,
                "--port",
                PORT,
                "tpl",
                "status",
                "--job-id",
                job_id,
                "--json",
            ],
            timeout=60,
        )
        if rc != 0:
            last = {"status": "status_error", "raw": out}
        else:
            payload = json.loads(out)
            last = payload.get("job") or {}
            status = last.get("status")
            if status in {"READY", "SUCCESS", "FAILED", "CANCELED"}:
                return last
        if time.monotonic() > deadline:
            if last is None:
                last = {}
            last["status"] = "TIMEOUT"
            return last
        time.sleep(POLL_INTERVAL)


def prepare_one(image):
    template_id = template_id_for(image)
    with state_lock:
        state = load_state()
        entry = state.get(image, {})
        if entry.get("status") == "READY":
            return image, template_id, "READY", "cached"

    info, raw = template_info(template_id)
    if info and info.get("status") == "READY":
        with state_lock:
            state = load_state()
            state[image] = {
                "image": image,
                "template_id": template_id,
                "status": "READY",
                "source": "existing",
            }
            save_template_state(state)
        return image, template_id, "READY", "existing"

    job_id = submit_template(image, template_id)
    with state_lock:
        state = load_state()
        state[image] = {
            "image": image,
            "template_id": template_id,
            "status": "RUNNING",
            "job_id": job_id,
        }
        save_template_state(state)

    job = poll_job(job_id)
    status = job.get("status", "UNKNOWN")
    with state_lock:
        state = load_state()
        state[image] = {
            "image": image,
            "template_id": template_id,
            "status": status,
            "job_id": job_id,
            "job": job,
        }
        save_template_state(state)
    return image, template_id, status, job.get("error_message", "")


def main():
    images = load_images()
    print(f"images={len(images)} workers={WORKERS}", flush=True)
    failures = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(prepare_one, image): image for image in images}
        done = 0
        for fut in as_completed(futures):
            done += 1
            image = futures[fut]
            try:
                image, template_id, status, detail = fut.result()
            except Exception as exc:
                failures += 1
                template_id = template_id_for(image)
                status = "ERROR"
                detail = repr(exc)
                with state_lock:
                    state = load_state()
                    state[image] = {
                        "image": image,
                        "template_id": template_id,
                        "status": status,
                        "error": detail,
                    }
                    save_template_state(state)
            if status != "READY":
                failures += 1
            print(f"[{done:02d}/{len(images)}] {template_id} {status} {detail}", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
