import asyncio
import datetime
import json
import sys
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schedules import ScheduleStore


def _store():
    tmp = tempfile.mkdtemp()
    return ScheduleStore(str(Path(tmp) / "schedules.json"))


class OneShotIsNotADailyRuleTests(unittest.TestCase):
    """A message scheduled once must not be recorded as a daily recurrence.

    The UI has no one-shot wire format, so it encodes the send time as the
    recurring spec "daily at HH:MM". The server then stored daily_at on a
    record whose one_shot flag was True, and the schedules bar rendered that
    field verbatim -- so a one-time message was labelled "daily at 15:00".
    """

    def test_one_shot_with_an_explicit_send_time_is_not_stored_as_a_daily_rule(self):
        store = _store()

        s = store.create(
            prompt="ping",
            targets=["claude"],
            daily_at="15:00",
            one_shot=True,
            send_at=time.time() + 3600,
        )

        self.assertTrue(s["one_shot"])
        self.assertIsNone(
            s["daily_at"],
            "a one-shot carried a daily_at, which the UI renders as 'daily at ...'",
        )

    def test_one_shot_still_fires_at_the_requested_moment(self):
        store = _store()
        send_at = time.time() + 3600

        s = store.create(
            prompt="ping",
            targets=["claude"],
            daily_at="15:00",
            one_shot=True,
            send_at=send_at,
        )

        self.assertAlmostEqual(s["next_run"], send_at, places=3)

    def test_a_genuine_daily_schedule_keeps_its_daily_rule(self):
        """Control: the fix must not strip daily_at from real recurrences."""
        store = _store()

        s = store.create(
            prompt="standup",
            targets=["claude"],
            daily_at="09:00",
            one_shot=False,
        )

        self.assertFalse(s["one_shot"])
        self.assertEqual(s["daily_at"], "09:00")


class PastSendTimeIsRejectedTests(unittest.TestCase):
    """Scheduling into the past must be refused, not silently fired.

    Nothing validated send_at, so a past timestamp became next_run, run_due()
    reported it immediately, and the runner delivered it on its next 30s tick
    while the UI countdown rendered blank.
    """

    def test_create_rejects_a_send_time_in_the_past(self):
        store = _store()

        with self.assertRaises(ValueError):
            store.create(
                prompt="ping",
                targets=["claude"],
                daily_at="09:00",
                one_shot=True,
                send_at=time.time() - 7200,
            )

    def test_a_rejected_schedule_is_not_persisted(self):
        store = _store()

        with self.assertRaises(ValueError):
            store.create(
                prompt="ping",
                targets=["claude"],
                one_shot=True,
                send_at=time.time() - 60,
            )

        self.assertEqual(store.list_all(), [])

    def test_a_rejected_schedule_never_becomes_due(self):
        store = _store()

        try:
            store.create(
                prompt="ping",
                targets=["claude"],
                one_shot=True,
                send_at=time.time() - 7200,
            )
        except ValueError:
            pass

        self.assertEqual(store.run_due(), [])

    def test_epoch_zero_is_a_past_time_not_an_absent_one(self):
        """send_at=0 is 1970, not "unset".

        Guards the `send_at is not None` test against being weakened back to a
        truthiness check, which would let 0 fall through to the recurring path.
        """
        store = _store()

        with self.assertRaises(ValueError):
            store.create(prompt="ping", targets=["claude"], one_shot=True, send_at=0)

        self.assertEqual(store.list_all(), [])

    def test_a_send_time_that_is_not_a_finite_number_is_refused(self):
        """NaN defeats every comparison, so `send_at <= now` silently passes.

        The record then reaches the JSON store, which writes bare NaN -- not
        legal JSON -- and every later read of the schedules file fails.
        """
        store = _store()

        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    store.create(
                        prompt="ping", targets=["claude"], one_shot=True, send_at=bad
                    )

        self.assertEqual(store.list_all(), [])

    def test_an_integer_too_large_for_a_float_is_refused_by_the_store_itself(self):
        """The endpoint catches this first, so only a direct caller gets here.

        Without its own guard the store raises OverflowError rather than the
        ValueError every caller is told to expect.
        """
        store = _store()

        with self.assertRaises(ValueError):
            store.create(
                prompt="ping", targets=["claude"], one_shot=True,
                send_at=int("9" * 400),
            )

        self.assertEqual(store.list_all(), [])

    def test_create_accepts_a_send_time_in_the_future(self):
        """Control: only past times are refused, not the whole one-shot path."""
        store = _store()

        s = store.create(
            prompt="ping",
            targets=["claude"],
            one_shot=True,
            send_at=time.time() + 1800,
        )

        self.assertEqual(len(store.list_all()), 1)
        self.assertEqual(store.run_due(), [], "a future schedule fired early")
        self.assertGreater(s["next_run"], time.time())


class _FakeRequest:
    """create_schedule() only ever awaits request.json()."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class CreateEndpointTests(unittest.TestCase):
    def setUp(self):
        import app

        self.app = app
        self._saved = app.schedules
        app.schedules = _store()
        self.addCleanup(lambda: setattr(app, "schedules", self._saved))

    def _post(self, **body):
        payload = {"prompt": "ping", "targets": ["claude"]}
        payload.update(body)
        return asyncio.run(self.app.create_schedule(_FakeRequest(payload)))

    def test_a_past_send_time_is_refused_with_400_not_a_server_error(self):
        past = datetime.datetime.now() - datetime.timedelta(hours=2)

        response = self._post(
            spec=f"daily at {past.strftime('%H:%M')}",
            one_shot=True,
            send_at_date=past.date().isoformat(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_a_future_send_time_is_still_accepted(self):
        future = datetime.datetime.now() + datetime.timedelta(hours=2)

        response = self._post(
            spec=f"daily at {future.strftime('%H:%M')}",
            one_shot=True,
            send_at_date=future.date().isoformat(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.app.schedules.list_all()), 1)


class RelativeSendAtTests(unittest.TestCase):
    """'Send in 2h 30m' -- the client computes the moment and posts it.

    The absolute date+time path cannot express "right after my credits reset",
    which is the case Max asked for.
    """

    def setUp(self):
        import app

        self.app = app
        self._saved = app.schedules
        app.schedules = _store()
        self.addCleanup(lambda: setattr(app, "schedules", self._saved))

    def _post(self, **body):
        payload = {"prompt": "ping", "targets": ["claude"]}
        payload.update(body)
        return asyncio.run(self.app.create_schedule(_FakeRequest(payload)))

    def test_an_explicit_send_at_epoch_is_honoured(self):
        target = time.time() + 9000

        response = self._post(spec="in 2h 30m", one_shot=True, send_at=target)

        self.assertEqual(response.status_code, 200)
        stored = self.app.schedules.list_all()[0]
        self.assertAlmostEqual(stored["next_run"], target, places=3)

    def test_a_relative_schedule_is_a_one_shot_not_a_daily_rule(self):
        response = self._post(
            spec="in 2h 30m", one_shot=True, send_at=time.time() + 9000
        )

        self.assertEqual(response.status_code, 200)
        stored = self.app.schedules.list_all()[0]
        self.assertTrue(stored["one_shot"])
        self.assertIsNone(stored["daily_at"])

    def test_an_explicit_send_at_in_the_past_is_refused(self):
        response = self._post(spec="in 2h 30m", one_shot=True, send_at=time.time() - 60)

        self.assertEqual(response.status_code, 400)
        # Assert WHY it was refused: a bare 400 also passes when the spec is
        # simply unparseable, which would prove nothing about the time check.
        self.assertIn("already passed", json.loads(response.body)["error"])
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_a_non_finite_send_at_is_refused_before_it_reaches_the_store(self):
        for bad in ("NaN", "Infinity", float("nan"), float("inf")):
            with self.subTest(value=bad):
                response = self._post(spec="in 2h", one_shot=True, send_at=bad)

                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_a_send_at_without_one_shot_cannot_smuggle_in_a_bad_spec(self):
        """An unparseable spec must still 400, not become a daily repeat.

        A send_at is only meaningful for a one-shot, so a recurring request
        ignores it and the spec has to stand on its own.
        """
        response = self._post(spec="banana", send_at=time.time() + 3600)

        self.assertEqual(response.status_code, 400)
        self.assertIn("banana", json.loads(response.body)["error"])
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_a_recurring_request_still_succeeds_when_a_stray_send_at_rides_along(self):
        """Compatibility: this request returned 200 before send_at existed."""
        response = self._post(spec="every 2h", send_at=time.time() + 3600)

        self.assertEqual(response.status_code, 200)
        stored = self.app.schedules.list_all()[0]
        self.assertFalse(stored["one_shot"])
        self.assertEqual(stored["interval_seconds"], 7200)

    def test_an_integer_too_large_for_a_float_is_refused_not_a_server_error(self):
        """JSON ints are arbitrary precision; float() raises OverflowError."""
        response = self._post(spec="in 2h", one_shot=True, send_at=int("9" * 400))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_a_send_time_beyond_any_plausible_date_is_refused(self):
        """1e300 is finite, never fires, and sticks in the list forever."""
        response = self._post(spec="in 2h", one_shot=True, send_at=1e300)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.schedules.list_all(), [])

    def test_an_unparseable_spec_is_still_refused_when_no_send_at_is_given(self):
        """Control: relaxing the spec check must not disable it generally."""
        response = self._post(spec="banana")

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
