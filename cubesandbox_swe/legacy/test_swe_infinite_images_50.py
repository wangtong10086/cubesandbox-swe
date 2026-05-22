#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REPO = "affinefoundation/swe_infinite_images"
TAGS_URL = (
    "https://hub.docker.com/v2/repositories/"
    f"{REPO}/tags?page_size=100&ordering=last_updated"
)
LIMIT = 50
MAX_SIZE_BYTES = 250 * 1024 * 1024
RUN_TIMEOUT_SEC = 30
PULL_TIMEOUT_SEC = 180
WORKERS = 2
RESULTS_PATH = str(Path(__file__).resolve().parents[2] / "results" / "swe_infinite_images_50_results.json")


def run(cmd, timeout):
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out)
    return proc.returncode, out, time.monotonic() - start


def fetch_tags():
    tags = []
    url = TAGS_URL
    while url and len(tags) < LIMIT:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.load(resp)
        for item in payload.get("results", []):
            size = item.get("full_size", 0)
            images = item.get("images") or []
            amd64 = any(
                image.get("os") == "linux" and image.get("architecture") == "amd64"
                for image in images
            )
            if amd64 and size <= MAX_SIZE_BYTES:
                tags.append(
                    {
                        "name": item["name"],
                        "size": size,
                        "last_updated": item.get("last_updated", ""),
                        "digest": item.get("digest", ""),
                    }
                )
            if len(tags) >= LIMIT:
                break
        url = payload.get("next")
    return tags


def test_image(tag):
    image = f"{REPO}:{tag['name']}"
    result = {
        **tag,
        "image": image,
        "pull_rc": None,
        "run_rc": None,
        "pull_seconds": None,
        "run_seconds": None,
        "status": "pending",
        "output": "",
    }

    rc, out, elapsed = run(
        [
            "sudo",
            "timeout",
            "-k",
            "10s",
            f"{PULL_TIMEOUT_SEC}s",
            "docker",
            "pull",
            image,
        ],
        PULL_TIMEOUT_SEC + 30,
    )
    result["pull_rc"] = rc
    result["pull_seconds"] = round(elapsed, 3)
    if rc != 0:
        result["status"] = "timeout" if rc == 124 else "pull_failed"
        result["output"] = out[-2000:]
        return result

    cmd = [
        "sudo",
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "/bin/sh",
        image,
        "-lc",
        "echo SWE_IMAGE_OK; uname -m; "
        "(python3 --version 2>/dev/null || python --version 2>/dev/null || true)",
    ]
    rc, out, elapsed = run(cmd, RUN_TIMEOUT_SEC)
    result["run_rc"] = rc
    result["run_seconds"] = round(elapsed, 3)
    result["output"] = out[-2000:]
    result["status"] = "ok" if rc == 0 and "SWE_IMAGE_OK" in out else "run_failed"
    return result


def main():
    tags = fetch_tags()
    print(
        f"selected={len(tags)} repo={REPO} max_size_mb={MAX_SIZE_BYTES / 1024 / 1024:.0f} workers={WORKERS}",
        flush=True,
    )
    results = []
    for idx, tag in enumerate(tags, 1):
        size_mb = tag["size"] / 1024 / 1024
        print(f"[queued {idx:02d}/{len(tags)}] {tag['name']} size={size_mb:.1f}MB", flush=True)

    def run_one(index_tag):
        idx, tag = index_tag
        try:
            result = test_image(tag)
        except subprocess.TimeoutExpired as exc:
            result = {
                **tag,
                "image": f"{REPO}:{tag['name']}",
                "status": "timeout",
                "pull_rc": None,
                "run_rc": None,
                "pull_seconds": None,
                "run_seconds": None,
                "output": str(exc),
            }
        except Exception as exc:
            result = {
                **tag,
                "image": f"{REPO}:{tag['name']}",
                "status": "error",
                "pull_rc": None,
                "run_rc": None,
                "pull_seconds": None,
                "run_seconds": None,
                "output": repr(exc),
            }
        return idx, result

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {
            executor.submit(run_one, (idx, tag)): (idx, tag)
            for idx, tag in enumerate(tags, 1)
        }
        for future in as_completed(future_map):
            idx, tag = future_map[future]
            try:
                _, result = future.result()
            except Exception as exc:
                result = {
                    **tag,
                    "image": f"{REPO}:{tag['name']}",
                    "status": "error",
                    "pull_rc": None,
                    "run_rc": None,
                    "pull_seconds": None,
                    "run_seconds": None,
                    "output": repr(exc),
                }
            results.append(result)
            with open(RESULTS_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(
                f"[done {len(results):02d}/{len(tags)} src={idx:02d}] {tag['name']} "
                f"status={result['status']} pull={result.get('pull_seconds')}s "
                f"run={result.get('run_seconds')}s",
                flush=True,
            )

    ok = sum(1 for item in results if item["status"] == "ok")
    failed = len(results) - ok
    print(f"summary ok={ok} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
