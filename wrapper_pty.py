"""Run a normal wrapped agent while termgrid owns the ConPTY input stream."""
import os
import subprocess
import time

from pty_bridge import BridgeClient


def run_agent(command, extra_args, cwd, env, queue_file, agent, no_restart, start_watcher,
              strip_env=None, pid_holder=None, session_name=None, inject_env=None,
              inject_delay=.3, enter_backend='console_input'):
    client = BridgeClient(int(os.environ['TERMGRID_BRIDGE_PORT']), os.environ['TERMGRID_BRIDGE_TOKEN'])
    child_env = {**env, **(inject_env or {})}
    child_env.pop('TERMGRID_BRIDGE_TOKEN', None)
    child_env.pop('TERMGRID_BRIDGE_PORT', None)
    start_watcher(client.inject)
    try:
        while True:
            process = subprocess.Popen([command] + extra_args, cwd=cwd, env=child_env)
            if pid_holder is not None:
                pid_holder[0] = process.pid
            process.wait()
            if pid_holder is not None:
                pid_holder[0] = None
            if no_restart:
                break
            time.sleep(3)
    finally:
        client.close()
