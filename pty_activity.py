"""Classify bounded PTY screen snapshots without reading a Windows console."""
from pattern_loader import PatternStore


def get_activity_checker(client, agent_name='unknown', trigger_flag=None, pattern_store=None):
    patterns = pattern_store or PatternStore(agent_name)
    previous = None
    active = False
    quiet = 0
    needs = False
    known = False

    def check():
        nonlocal previous, active, quiet, needs, known
        # A queued prompt is not proof that the agent processed it.
        if trigger_flag and trigger_flag[0]:
            trigger_flag[0] = False
        rows = client.screen()
        if rows is None:
            previous = None
            needs = False
            known = False
            quiet = 0
            return None
        try:
            state, _ = patterns.classify(rows)
        except (OSError, ValueError, TypeError, RuntimeError):
            previous = None
            needs = False
            known = False
            quiet = 0
            return None
        if state == 'unavailable':
            previous = None
            needs = False
            known = False
            quiet = 0
            return None
        current = tuple(rows)
        if state == 'needs_input':
            previous, active, quiet, needs, known = current, False, 0, True, True
            return False
        needs = False
        if state == 'working':
            previous, active, quiet, known = current, True, 0, True
            return True
        if state == 'idle':
            previous, active, quiet, known = current, False, 5, True
            return False
        if previous is None:
            previous = current
            return None
        # Count changed cells, not timer-only text or row count alone.
        changed = sum(sum(a != b for a, b in zip(old, new)) + abs(len(old) - len(new))
                      for old, new in zip(previous, current))
        changed += sum(len(row) for row in previous[len(current):])
        changed += sum(len(row) for row in current[len(previous):])
        previous = current
        if changed >= 10:
            active, quiet, known = True, 0, True
        else:
            quiet += 1
            if quiet >= 5:
                active, known = False, True
        return active if known else None

    check.needs_input = lambda: needs
    return check
