"""Regression tests for the wrapper shutdown race (round 7).

Invariant under test: once shutdown begins, no new heartbeat/register may be
initiated; after all in-flight heartbeats and 409 recoveries complete, the
deregister must be the wrapper's LAST registry HTTP mutation (no ghost
re-registration after a clean shutdown).
"""

import io
import json
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wrapper import (HeartbeatSender, _activity_monitor_loop, _heartbeat_loop,  # noqa: E402
                     _shutdown_and_deregister)


def _wait_true(predicate, timeout=5.0, interval=0.02):
    """Event-style wait on an observable condition (no bare-sleep judgment)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


class GatedTransport:
    """Scripted urlopen replacement. Records every request; while the gate is
    armed, requests block until released — simulating an in-flight HTTP call
    racing the shutdown path."""

    def __init__(self, status=200, name="e2e-test"):
        self.status = status
        self.name = name
        self.calls = []  # (method, url) in order
        self.started = threading.Event()   # a gated request is now in flight
        self.release = threading.Event()   # test lets the response through
        self.gate_armed = False
        self._lock = threading.Lock()

    def arm_gate(self):
        self.gate_armed = True

    def __call__(self, req, timeout=5, **kwargs):
        with self._lock:
            self.calls.append((req.get_method(), req.full_url))
        if self.gate_armed:
            self.started.set()
            self.release.wait(10)
        if self.status == 200:
            return _FakeResponse({"ok": True, "name": self.name})
        raise urllib.error.HTTPError(req.full_url, self.status, "conflict",
                                     None, io.BytesIO(b"{}"))

    def count(self, needle):
        return sum(1 for _m, url in self.calls if needle in url)


def _make_sender():
    return HeartbeatSender(
        server_port=9999,
        get_identity=lambda: ("e2e-test", None),
        get_token=lambda: "tok",
        lease_id="lease-x", pid=1234, start_marker="m")


def _heartbeat_kwargs(sequence):
    return {"get_identity": lambda: ("e2e-test", None),
            "set_identity": lambda name: None,
            "recover": lambda: sequence.append("register"),
            "interval": 60.0}


class ShutdownRaceTests(unittest.TestCase):
    def test_inflight_heartbeat_completes_then_deregister_last(self):
        # Race (b) on the periodic thread: heartbeat blocked in flight while
        # the finally path runs; the late 200 response must NOT be acted on
        # and deregister must be the last (only) mutation after the beat.
        transport = GatedTransport(status=200)
        transport.arm_gate()
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        with mock.patch("urllib.request.urlopen", transport):
            hb = threading.Thread(target=_heartbeat_loop, args=(sender, shutdown),
                                  kwargs=_heartbeat_kwargs(sequence), daemon=True)
            hb.start()
            self.assertTrue(transport.started.wait(5))  # heartbeat in flight
            fin = threading.Thread(
                target=_shutdown_and_deregister,
                args=(shutdown, sender, [hb],
                      lambda: sequence.append("deregister")),
                kwargs={"join_timeout": 5.0}, daemon=True)
            fin.start()
            self.assertTrue(shutdown.wait(5))  # shutdown signaled mid-flight
            transport.release.set()
            fin.join(10)
            hb.join(10)
        self.assertFalse(fin.is_alive() or hb.is_alive())
        self.assertEqual(sequence, ["deregister"])
        self.assertEqual(transport.count("/api/heartbeat/"), 1)
        self.assertEqual(transport.count("/api/register"), 0)

    def test_409_received_but_recovery_skipped_after_shutdown(self):
        # Race (a): a 409 arrives while shutdown is being signaled — the
        # recovery (re-register) must never start; deregister stays last.
        transport = GatedTransport(status=409)
        transport.arm_gate()
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        with mock.patch("urllib.request.urlopen", transport):
            hb = threading.Thread(target=_heartbeat_loop, args=(sender, shutdown),
                                  kwargs=_heartbeat_kwargs(sequence), daemon=True)
            hb.start()
            self.assertTrue(transport.started.wait(5))  # 409 in flight
            fin = threading.Thread(
                target=_shutdown_and_deregister,
                args=(shutdown, sender, [hb],
                      lambda: sequence.append("deregister")),
                kwargs={"join_timeout": 5.0}, daemon=True)
            fin.start()
            self.assertTrue(shutdown.wait(5))
            transport.release.set()  # late 409 arrives after shutdown began
            fin.join(10)
            hb.join(10)
        self.assertFalse(fin.is_alive() or hb.is_alive())
        self.assertEqual(sequence, ["deregister"])  # no "register" ever
        self.assertEqual(transport.count("/api/register"), 0)

    def test_inflight_recovery_completes_before_deregister(self):
        # A 409 recovery already in flight when shutdown begins is joined to
        # completion; deregister comes strictly after it.
        transport = GatedTransport(status=409)  # no gate: 409 returns at once
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        recovery_started = threading.Event()
        recovery_release = threading.Event()

        def slow_recover():
            recovery_started.set()
            recovery_release.wait(10)
            sequence.append("register")

        kwargs = _heartbeat_kwargs(sequence)
        kwargs["recover"] = slow_recover
        with mock.patch("urllib.request.urlopen", transport):
            hb = threading.Thread(target=_heartbeat_loop, args=(sender, shutdown),
                                  kwargs=kwargs, daemon=True)
            hb.start()
            self.assertTrue(recovery_started.wait(5))  # recovery in flight
            fin = threading.Thread(
                target=_shutdown_and_deregister,
                args=(shutdown, sender, [hb],
                      lambda: sequence.append("deregister")),
                kwargs={"join_timeout": 5.0}, daemon=True)
            fin.start()
            self.assertTrue(shutdown.wait(5))
            recovery_release.set()
            fin.join(10)
            hb.join(10)
        self.assertFalse(fin.is_alive() or hb.is_alive())
        # In-flight recovery finished FIRST; deregister is last.
        self.assertEqual(sequence, ["register", "deregister"])
        self.assertEqual(transport.count("/api/heartbeat/"), 1)

    def test_activity_monitor_inflight_send_exits_before_deregister(self):
        # Race (b) on the activity sender: blocked inside send() when shutdown
        # begins; the loop must exit without further sends.
        transport = GatedTransport(status=200)
        transport.arm_gate()
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        with mock.patch("urllib.request.urlopen", transport):
            act = threading.Thread(
                target=_activity_monitor_loop,
                args=(lambda: (lambda: True), sender),
                kwargs={"should_run": lambda: not shutdown.is_set(),
                        "sleep_fn": shutdown.wait},
                daemon=True)
            act.start()
            self.assertTrue(transport.started.wait(5))  # activity send in flight
            fin = threading.Thread(
                target=_shutdown_and_deregister,
                args=(shutdown, sender, [act],
                      lambda: sequence.append("deregister")),
                kwargs={"join_timeout": 5.0}, daemon=True)
            fin.start()
            self.assertTrue(shutdown.wait(5))
            transport.release.set()
            fin.join(10)
            act.join(10)
        self.assertFalse(fin.is_alive() or act.is_alive())
        self.assertEqual(sequence, ["deregister"])
        self.assertEqual(transport.count("/api/heartbeat/"), 1)

    def test_deregister_skipped_when_threads_do_not_quiesce(self):
        # Quiescence cannot be confirmed (in-flight request outlives the join
        # timeout) -> deregister is SKIPPED rather than raced.
        transport = GatedTransport(status=200)
        transport.arm_gate()
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        with mock.patch("urllib.request.urlopen", transport):
            hb = threading.Thread(target=_heartbeat_loop, args=(sender, shutdown),
                                  kwargs=_heartbeat_kwargs(sequence), daemon=True)
            hb.start()
            self.assertTrue(transport.started.wait(5))
            done = _shutdown_and_deregister(
                shutdown, sender, [hb], lambda: sequence.append("deregister"),
                join_timeout=0.2)  # join blocks 0.2s — synchronization, not a judgment sleep
            self.assertFalse(done)
            self.assertEqual(sequence, [])  # deregister NOT sent
            transport.release.set()
            hb.join(10)
        self.assertFalse(hb.is_alive())

    def test_sleeping_thread_wakes_promptly_on_shutdown(self):
        # A thread parked in the inter-beat wait must wake immediately, so a
        # clean shutdown never waits out the heartbeat interval.
        transport = GatedTransport(status=200)  # first beat returns at once
        sender = _make_sender()
        shutdown = threading.Event()
        sequence = []
        with mock.patch("urllib.request.urlopen", transport):
            hb = threading.Thread(target=_heartbeat_loop, args=(sender, shutdown),
                                  kwargs=_heartbeat_kwargs(sequence), daemon=True)
            hb.start()
            self.assertTrue(_wait_true(lambda: transport.count("/api/heartbeat/") >= 1))
            t0 = time.monotonic()
            done = _shutdown_and_deregister(
                shutdown, sender, [hb], lambda: sequence.append("deregister"),
                join_timeout=5.0)
            elapsed = time.monotonic() - t0
        self.assertTrue(done)
        self.assertEqual(sequence, ["deregister"])
        self.assertFalse(hb.is_alive())
        self.assertLess(elapsed, 3.0)  # did NOT wait out the 60s interval


if __name__ == "__main__":
    unittest.main()
