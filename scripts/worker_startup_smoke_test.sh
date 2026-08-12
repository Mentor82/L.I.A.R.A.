#!/bin/bash
#
# Worker startup smoke test for ll_ol_fallback inference chain (WSL/Linux)
#
# Demonstrates:
# 1. InferenceGateway with ll_ol_fallback provider
# 2. Which backend is actually active (llama_cpp vs ollama)
# 3. Fallback behavior when primary is unavailable
#
# Usage (in WSL or Linux):
#     bash scripts/worker_startup_smoke_test.sh
#     ./scripts/worker_startup_smoke_test.sh
#

set -e

# Get the repository root
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Activate virtual environment if it exists
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
fi

# Run the Python smoke test
echo "Running worker startup smoke test..."
python3 -m scripts.worker_startup_smoke_test
