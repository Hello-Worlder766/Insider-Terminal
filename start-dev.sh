#!/usr/bin/env bash
# Start the dev server with sensible defaults.
# Usage: ./start-dev.sh [PORT]
set -euo pipefail

# Activate the venv if present
if [ -f "venv311/bin/activate" ]; then
  # shellcheck source=/dev/null
  source venv311/bin/activate
fi

# Accept optional first argument as PORT, else read PORT env var, else default 5000
PORT_ARG=${1:-${PORT:-5000}}

# Require the API key to be set
if [ -z "${DASHBOARD_PRIVATE_KEY:-}" ] && [ -z "${DASHBOARD_API_KEY:-}" ]; then
  echo "ERROR: Please set DASHBOARD_PRIVATE_KEY or DASHBOARD_API_KEY in your environment or .env"
  exit 1
fi

# Run the app; it will auto-select an available port if needed
PORT="$PORT_ARG" venv311/bin/python -c "from app import app; app.run()"
