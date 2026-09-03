"""Desktop-host single-instance replacement tests (2026-09)."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend import APP_INSTANCE_ID  # noqa: E402
import backend.host as host  # noqa: E402


def current_status(pid=123):
    return {"ok": True, "app_instance": APP_INSTANCE_ID, "pid": pid}


def legacy_status():
    return {
        "ok": True, "db": {"replays": 1, "maps": 1},
        "replay_dir": {}, "maps_dir": {},
        "ai": {"provider": "x"}, "chro": {"available": False},
        "config": {"replay_dir": "x", "custom_levels_dir": "y"},
    }


class TestSaberLabStatusIdentity(unittest.TestCase):
    def test_current_and_legacy_status_recognized(self):
        self.assertTrue(host._looks_like_saberlab_status(current_status()))
        self.assertTrue(host._looks_like_saberlab_status(legacy_status()))

    def test_unrelated_health_response_rejected(self):
        self.assertFalse(host._looks_like_saberlab_status({"ok": True}))
        self.assertFalse(host._looks_like_saberlab_status("saberlab"))


class TestReplaceExistingInstances(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(port=6980)

    def _probe_only_6980(self, status):
        return lambda port: status if port == 6980 else None

    def test_matching_status_and_tcp_owner_is_terminated(self):
        with mock.patch.object(host, "_probe_saberlab",
                               side_effect=self._probe_only_6980(current_status())), \
             mock.patch.object(host, "_listener_pid", return_value=123), \
             mock.patch.object(host, "_terminate_process", return_value=True) as terminate:
            replaced = host.replace_existing_instances(self.cfg)
        self.assertEqual(replaced, [(6980, 123)])
        terminate.assert_called_once_with(123)

    def test_reported_pid_mismatch_is_never_terminated(self):
        with mock.patch.object(host, "_probe_saberlab",
                               side_effect=self._probe_only_6980(current_status(999))), \
             mock.patch.object(host, "_listener_pid", return_value=123), \
             mock.patch.object(host, "_terminate_process") as terminate:
            replaced = host.replace_existing_instances(self.cfg)
        self.assertEqual(replaced, [])
        terminate.assert_not_called()

    def test_unrelated_port_occupant_is_never_terminated(self):
        with mock.patch.object(host, "_probe_saberlab", return_value=None), \
             mock.patch.object(host, "_listener_pid", return_value=123), \
             mock.patch.object(host, "_terminate_process") as terminate:
            self.assertEqual(host.replace_existing_instances(self.cfg), [])
        terminate.assert_not_called()

    def test_default_range_is_scanned_when_config_port_changes(self):
        self.cfg.port = 7100
        ports = host._candidate_ports(self.cfg)
        self.assertIn(6980, ports)
        self.assertIn(7100, ports)

    def test_legacy_status_uses_verified_tcp_owner(self):
        with mock.patch.object(host, "_probe_saberlab",
                               side_effect=self._probe_only_6980(legacy_status())), \
             mock.patch.object(host, "_listener_pid", return_value=321), \
             mock.patch.object(host, "_terminate_process", return_value=True):
            self.assertEqual(host.replace_existing_instances(self.cfg), [(6980, 321)])

    def test_failed_termination_aborts_instead_of_creating_duplicate(self):
        with mock.patch.object(host, "_probe_saberlab",
                               side_effect=self._probe_only_6980(current_status())), \
             mock.patch.object(host, "_listener_pid", return_value=123), \
             mock.patch.object(host, "_terminate_process", return_value=False):
            with self.assertRaises(RuntimeError):
                host.replace_existing_instances(self.cfg)


if __name__ == "__main__":
    unittest.main()
