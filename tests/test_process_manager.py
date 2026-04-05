import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from process_manager import ProcessManager


def test_launch_tracks_state():
    """Launching an agent creates a tracked entry with correct state."""
    pm = ProcessManager(data_dir=Path("./test_data"), server_port=8300)
    result = pm.launch(
        base="testbot",
        command=sys.executable,
        flags=[],
        extra_args=["-c", "import time; time.sleep(0.5); print('hello')"],
        cwd=".",
    )
    assert result["ok"] is True
    assert result["name"] == "testbot"
    assert result["pid"] > 0

    managed = pm.list_managed()
    assert len(managed) == 1
    assert managed[0]["name"] == "testbot"
    assert managed[0]["state"] in ("starting", "running")

    time.sleep(1.5)
    managed = pm.list_managed()
    assert managed[0]["state"] in ("crashed", "stopped")

    pm.shutdown()
    import shutil
    shutil.rmtree("./test_data", ignore_errors=True)


def test_launch_duplicate_base_gets_suffix():
    """Launching the same base twice assigns different names."""
    pm = ProcessManager(data_dir=Path("./test_data"), server_port=8300)
    r1 = pm.launch(base="bot", command=sys.executable,
                   flags=[], extra_args=["-c", "import time; time.sleep(2)"], cwd=".")
    r2 = pm.launch(base="bot", command=sys.executable,
                   flags=[], extra_args=["-c", "import time; time.sleep(2)"], cwd=".")
    assert r1["name"] == "bot"
    assert r2["name"] == "bot-2"
    assert len(pm.list_managed()) == 2
    pm.stop("bot")
    pm.stop("bot-2")
    pm.shutdown()
    import shutil
    shutil.rmtree("./test_data", ignore_errors=True)
