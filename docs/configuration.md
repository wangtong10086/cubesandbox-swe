# Configuration

Copy `.env.example` to `.env` for local runs.

## Required Runtime Values

- `CUBE_API_URL`: CubeSandbox API endpoint, usually `http://127.0.0.1:3000`.
- `CUBE_PROXY_NODE_IP`: proxy node IP used by the local CubeSandbox SDK.
- `OPENAI_BASE_URL` and `OPENAI_API_KEY`: OpenAI-compatible endpoint for Codex.
- `OPENAI_MODEL`: model name supported by that endpoint.

`CHUTES_BASE_URL` and `CHUTES_API_KEY` are supported aliases for existing local
scripts.

## Codex Settings

- `SWE_INFINITE_CODEX_WIRE_API`: defaults to `responses`.
- `SWE_INFINITE_CODEX_REASONING_EFFORT`: defaults to `medium` in examples.
- `SWE_INFINITE_CODEX_HTTP_PROXY`: optional proxy for the host Codex process.
  The CubeSandbox task sandbox does not receive the model key or proxy
  settings. When unset, solve runs infer the proxy from the host `HTTPS_PROXY`
  or `HTTP_PROXY` before clearing proxy variables for local CubeSandbox API
  calls.
- `--model-preflight-timeout`: Codex/model preflight timeout in seconds.
- `--skip-model-preflight`: bypasses the Codex preflight when the provider has
  already been checked.

## Local Services

Start the WSL-oriented local CubeSandbox services with:

```sh
scripts/cubesandbox-wsl-start.sh
```

The startup helper also creates `/data/cube-shim/snapshot`, which the local
CubeSandbox runtime checks as the host-level snapshot capability flag, and
seeds Cubemaster metrics for the configured node IP.

Check health with the CubeSandbox one-click quickcheck:

```sh
sudo /usr/local/services/cubetoolbox/scripts/one-click/quickcheck.sh
```

Machine-local proxy, DNS, and service config drafts are not committed by
default. Keep publishable examples under `configs/*.example`.

## Secret Boundaries

Model credentials are loaded from `.env` into the Codex process environment.
They are not written into the CubeSandbox task sandbox, not copied into `/app`,
and not passed to verifier scripts. Trajectory and log writers redact known
API key values before persisting Codex output.

Before publishing run artifacts, scan generated outputs for exact `.env` values
and publish only the intended redacted artifacts. See [Validation](validation.md)
for the scan pattern used by local validation.
