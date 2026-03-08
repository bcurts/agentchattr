#!/usr/bin/env sh
# agentchattr - starts server (if not running) + ALL agent wrappers in separate windows
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

is_server_running() {
    lsof -i :8300 -sTCP:LISTEN >/dev/null 2>&1 || \
    ss -tlnp 2>/dev/null | grep -q ':8300 '
}

# Get CLI agents from config (those with 'command', not type='api')
get_cli_agents() {
    .venv/bin/python -c "
import tomllib
with open('config.toml','rb') as f:
    c = tomllib.load(f)
for k, v in c.get('agents', {}).items():
    if v.get('command') and v.get('type') != 'api':
        print(k)
" 2>/dev/null || echo "claude codex gemini kimi"
}

ensure_venv

if ! is_server_running; then
    echo "Starting server..."
    if [ "$(uname -s)" = "Darwin" ]; then
        osascript -e "tell app \"Terminal\" to do script \"cd '$(pwd)' && .venv/bin/python run.py\"" > /dev/null 2>&1
    else
        if command -v gnome-terminal >/dev/null 2>&1; then
            gnome-terminal -- sh -c "cd '$(pwd)' && .venv/bin/python run.py; printf 'Press Enter to close... '; read _"
        elif command -v xterm >/dev/null 2>&1; then
            xterm -e sh -c "cd '$(pwd)' && .venv/bin/python run.py" &
        else
            .venv/bin/python run.py > data/server.log 2>&1 &
        fi
    fi

    i=0
    while [ "$i" -lt 30 ]; do
        if is_server_running; then
            break
        fi
        sleep 0.5
        i=$((i + 1))
    done
fi

echo "Server ready at http://localhost:8300"
echo "Launching agents in separate windows..."
PROJECT_DIR="$(pwd)"

for agent in $(get_cli_agents); do
    echo "  Starting $agent..."
    if [ "$(uname -s)" = "Darwin" ]; then
        osascript -e "tell app \"Terminal\" to do script \"cd '$PROJECT_DIR' && .venv/bin/python wrapper.py $agent\""
    else
        if command -v gnome-terminal >/dev/null 2>&1; then
            gnome-terminal -- sh -c "cd '$PROJECT_DIR' && .venv/bin/python wrapper.py $agent; printf 'Press Enter to close... '; read _"
        elif command -v xterm >/dev/null 2>&1; then
            xterm -e sh -c "cd '$PROJECT_DIR' && .venv/bin/python wrapper.py $agent" &
        else
            .venv/bin/python wrapper.py "$agent" &
        fi
    fi
    sleep 1
done

echo ""
echo "Done. Open http://localhost:8300 in your browser."
echo "Agent windows may take a moment to connect — check status pills in the header."
