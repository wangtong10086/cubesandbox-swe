#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "${ROOT_DIR}"

PYTHON_BIN=${PYTHON_BIN:-python}
DOCTOR_ARGS=${DOCTOR_ARGS:-}

"${PYTHON_BIN}" -m ruff check cubesandbox_swe tests
"${PYTHON_BIN}" -m pytest -q
"${PYTHON_BIN}" -m build

if [ "${SKIP_DOCTOR:-0}" != "1" ]; then
  "${PYTHON_BIN}" -m cubesandbox_swe.cli doctor ${DOCTOR_ARGS}
fi
