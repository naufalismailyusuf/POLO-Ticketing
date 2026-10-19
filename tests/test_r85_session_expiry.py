"""R85 session policy regression tests; no application/SMB imports."""
import unittest
from auth_session_policy import (
    ABSOLUTE_TIMEOUT_SECONDS, IDLE_TIMEOUT_SECONDS,
    remaining_seconds, expiry_payload,
)


class SessionExpiryTests(unittest.TestCase):
    def setUp(self):
        self.now = 100000.0

    def session(self, started=None, active=None):
        return {
            "auth_started_at": self.now if started is None else started,
            "auth_last_active_at": self.now if active is None else active
        }

    def test_fresh_session(self):
        self.assertEqual(remaining_seconds(self.session(), self.now),
                         (IDLE_TIMEOUT_SECONDS, ABSOLUTE_TIMEOUT_SECONDS))

    def test_unauthenticated_or_legacy_expires(self):
        for candidate in ({}, {"auth_started_at": self.now}, {"auth_last_active_at":self.now}):
            self.assertEqual(remaining_seconds(candidate, self.now), (0,0))

    def test_idle_timeout_boundary(self):
        started = self.now - 2500
        active = self.now - IDLE_TIMEOUT_SECONDS
        idle, absolute = remaining_seconds(self.session(started, active), self.now)
        self.assertEqual(idle, 0)
        self.assertGreater(absolute, 0)

    def test_no_background_poll_renewal(self):
        data = self.session(self.now - IDLE_TIMEOUT_SECONDS + 1,
                            self.now - IDLE_TIMEOUT_SECONDS + 1)
        # Merely evaluating the session does not update the signed activity time.
        remaining_seconds(data, self.now)
        idle, _ = remaining_seconds(data, self.now + 2)
        self.assertEqual(idle, 0)
        self.assertEqual(data["auth_last_active_at"], self.now - IDLE_TIMEOUT_SECONDS + 1)

    def test_activity_extends_idle_but_not_absolute(self):
        started = self.now - (ABSOLUTE_TIMEOUT_SECONDS - 10)
        data = self.session(started, self.now - 2)
        idle, absolute = remaining_seconds(data, self.now)
        self.assertGreater(idle, 1000)
        self.assertEqual(absolute, 10)

    def test_absolute_timeout_cannot_be_extended(self):
        data = self.session(self.now-ABSOLUTE_TIMEOUT_SECONDS, self.now-1)
        idle, absolute = remaining_seconds(data, self.now)
        self.assertGreater(idle, 0)
        self.assertEqual(absolute, 0)

    def test_invalid_or_future_timestamps_fail_closed(self):
        bad = [
            self.session("invalid", self.now), self.session(float("nan"), self.now),
            self.session(self.now+60, self.now+60),
            self.session(self.now-10, self.now+60),
            self.session(self.now-5, self.now-50),
            self.session(0, self.now)
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual(remaining_seconds(value, self.now), (0,0))

    def test_expiry_json_payload(self):
        payload = expiry_payload(self.session(), self.now)
        self.assertEqual(payload, {"idle_seconds_remaining":1800,
                                   "absolute_seconds_remaining":28800})


if __name__ == "__main__":
    unittest.main()
