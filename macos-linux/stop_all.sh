#!/usr/bin/env sh
# agentchattr - kill server and all agent wrappers
cd "$(dirname "$0")/.."

echo "Stopping agentchattr..."

# Kill server (listens on 8300; MCP 8200/8201 run in same process)
if lsof -i :8300 -sTCP:LISTEN 2>/dev/null | grep -q .; then
    echo "  Killing server (port 8300)..."
    lsof -ti :8300 | xargs kill -9 2>/dev/null || true
fi

# Kill all agent wrappers
if pgrep -f "wrapper\.py" >/dev/null 2>&1; then
    echo "  Killing agent wrappers..."
    pkill -9 -f "wrapper\.py" 2>/dev/null || true
fi

# Brief pause for ports to release
sleep 1

echo "Done. Run start_all.sh to restart."
