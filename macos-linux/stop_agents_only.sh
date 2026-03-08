#!/usr/bin/env sh
# Kill agent wrappers only (does not kill the server)
# Match "wrapper.py" in command line (e.g. "python wrapper.py claude")
pkill -9 -f "wrapper.py" 2>/dev/null || true
