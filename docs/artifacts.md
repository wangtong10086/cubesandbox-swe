# Artifacts

Full SWE trajectory collections can be large. This repository keeps source,
schemas, manifests, and small examples in Git. Full artifacts should be
published separately.

## Local Directories

- `results/`: JSON results, patches, task metadata, manifests, and trajectory collections.
- `swe-e2e-runs/`: per-run control directories, prompts, Codex JSONL output, and raw logs.
- `logs/`: local service setup logs.

These directories are ignored by Git.

## Manifest

Update the source-controlled artifact manifest with:

```sh
cubesandbox-swe artifacts summarize --output artifacts/manifest.json
```

Before publishing a release, fill `external_uri` with the GitHub Release,
object-store, or dataset URL that contains the complete run outputs.

## Record Shapes

- `result.json`: one solve/verify run summary.
- `trajectory.json`: full task metadata, prompts, Codex JSONL events, patches, verifier result, and logs.
- `rollout_bucket.json`: Affine rollout-bucket compatible record with SWE details under `extra`.

Known API key values are redacted before trajectory files are written.

Verifier result files also record sandbox lifecycle fields. For a successful
pause/reconnect verification, expect `state_after_save=paused` and
`state_after_restore=running`.
