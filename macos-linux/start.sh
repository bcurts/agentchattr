#!/usr/bin/env sh
# Mehub - starts the server only
cd "$(dirname "$0")/.."

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Python 3 is required but was not found on PATH."
    exit 1
fi

ensure_venv() {
    if [ -d ".venv" ] && [ ! -x ".venv/bin/python" ]; then
        echo "Recreating .venv for this platform..."
        rm -rf .venv
    fi

    if [ ! -x ".venv/bin/python" ]; then
        echo "Creating virtual environment..."
        "$PYTHON_BIN" -m venv .venv || {
            echo "Error: failed to create .venv with $PYTHON_BIN."
            exit 1
        }
        .venv/bin/python -m pip install -q -r requirements.txt || {
            echo "Error: failed to install Python dependencies."
            exit 1
        }
    fi
}

ensure_venv

is_server_running() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -i :8300 -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | grep -q ':8300 '
        return $?
    fi
    return 1
}

if is_server_running; then
    echo "Mehub is already running on http://127.0.0.1:8300"
    echo "Stop the existing process first if you want to restart it."
    exit 0
fi

build_web_ui() {
    if ! command -v npm >/dev/null 2>&1; then
        echo "Error: npm is required to build the Mehub web UI."
        exit 1
    fi

    if [ ! -x "web/node_modules/.bin/tsc" ]; then
        echo "Installing web UI dependencies..."
        if [ -f "web/package-lock.json" ]; then
            (cd web && npm ci) || {
                echo "Error: failed to install web UI dependencies."
                exit 1
            }
        else
            (cd web && npm install) || {
                echo "Error: failed to install web UI dependencies."
                exit 1
            }
        fi
    fi

    echo "Building Mehub web UI..."
    (cd web && npm run build) || {
        echo "Error: failed to build the Mehub web UI."
        exit 1
    }
}

build_web_ui

.venv/bin/python run.py
code=$?
echo ""
echo "=== Server exited with code $code ==="
exit "$code"
