#!/usr/bin/env sh
# agentchattr - starts server (if not running) + Grok Build wrapper
cd "$(dirname "$0")/.."

# grok's installer drops the binary in $GROK_BIN_DIR (default ~/.grok/bin) and
# adds it to PATH via your shell profile. A launcher run from a non-login shell
# (or a session opened before the install) may not have sourced that, so
# best-effort prepend the install dir if grok lives there.
_grok_bin="${GROK_BIN_DIR:-$HOME/.grok/bin}"
if [ -x "$_grok_bin/grok" ]; then
    case ":$PATH:" in
        *":$_grok_bin:"*) ;;            # already on PATH
        *) PATH="$_grok_bin:$PATH" ;;
    esac
fi
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

# Pre-flight: check that grok CLI is installed
if ! command -v grok >/dev/null 2>&1; then
    echo ""
    echo "  Error: \"grok\" was not found on PATH."
    echo "  Install with: curl -fsSL https://x.ai/cli/install.sh | bash"
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

.venv/bin/python wrapper.py grok
