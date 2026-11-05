#!/usr/bin/env sh
# agentchattr - starts server (if not running) + Antigravity (agy) wrapper
cd "$(dirname "$0")/.."

# Pin agy's version - it self-updates and will otherwise drift out from under you
export AGY_CLI_DISABLE_AUTO_UPDATE=1

# agy adds itself to PATH via your shell profile at install time. A launcher run
# from a non-login shell (or a session opened before the install) may not have
# sourced that, so best-effort prepend the common install dirs if agy lives there.
for _agy_dir in "$HOME/.local/bin" "/usr/local/bin" "/opt/homebrew/bin" "$HOME/.agy/bin"; do
    if [ -x "$_agy_dir/agy" ]; then
        case ":$PATH:" in
            *":$_agy_dir:"*) ;;                 # already on PATH
            *) PATH="$_agy_dir:$PATH" ;;
        esac
    fi
done
export PATH

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

# Pre-flight: check that the agy CLI is installed
if ! command -v agy >/dev/null 2>&1; then
    echo ""
    echo "  Error: 'agy' (Antigravity CLI) was not found on PATH."
    echo "  Install it first, then try again."
    echo "  Note: run 'agy' once with no args to sign in before using it here."
    echo ""
    exit 1
fi

ensure_venv

if ! is_server_running; then
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

.venv/bin/python wrapper.py antigravity
