"""
Tests for zkteco_attendance.zkteco_attendance.zk_client.check_device_reachable
Run with: bench run-tests --app zkteco_attendance
"""

import socket
import unittest
from unittest.mock import patch, MagicMock

import frappe


class TestCheckDeviceReachable(unittest.TestCase):

    def _device(self, ip="192.0.2.55", port=4370, name="Test-ZK-Device"):
        """A lightweight stand-in for a Biometric Device doc."""
        device = MagicMock()
        device.device_ip = ip
        device.port = port
        device.device_name = name
        device.name = name
        return device

    def test_reachable_when_tcp_connect_succeeds(self):
        """A successful TCP connect means the device is reachable."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value = MagicMock()
            ok, err = check_device_reachable(self._device())

        self.assertTrue(ok)
        self.assertEqual(err, "")
        mock_conn.assert_called_once_with(("192.0.2.55", 4370), timeout=3)

    def test_timeout_returns_unreachable(self):
        """Connection timeout (device offline / not pingable) → not reachable."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        with patch("socket.create_connection", side_effect=socket.timeout):
            ok, err = check_device_reachable(self._device())

        self.assertFalse(ok)
        self.assertIn("cannot be reached", err)

    def test_connection_refused_returns_unreachable(self):
        """Connection refused → not reachable with a port hint."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            ok, err = check_device_reachable(self._device())

        self.assertFalse(ok)
        self.assertIn("cannot be reached", err)

    def test_unreachable_host_returns_unreachable(self):
        """OSError (no route to host, network down) → not reachable."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        with patch("socket.create_connection", side_effect=OSError("No route to host")):
            ok, err = check_device_reachable(self._device())

        self.assertFalse(ok)
        self.assertIn("cannot be reached", err)

    def test_missing_ip_returns_unreachable(self):
        """A device without an IP can never be reachable."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        ok, err = check_device_reachable(self._device(ip=""))
        self.assertFalse(ok)
        self.assertIn("no IP address", err)

    def test_unexpected_exception_returns_unreachable(self):
        """Any unexpected probe error must not raise — return not reachable."""
        from zkteco_attendance.zkteco_attendance.zk_client import check_device_reachable

        with patch("socket.create_connection", side_effect=RuntimeError("boom")):
            ok, err = check_device_reachable(self._device())

        self.assertFalse(ok)
        self.assertIn("cannot be reached", err)

    def test_start_pull_checkins_throws_when_unreachable(self):
        """
        start_pull_checkins must fail fast with a clear message when the
        device cannot be reached — the job must NOT be queued and the UI
        must never be left hanging on \"Starting...\".
        """
        import zkteco_attendance.zkteco_attendance.api.endpoints as endpoints

        with patch("frappe.get_doc") as mock_get_doc, \
             patch("zkteco_attendance.zkteco_attendance.zk_client.check_device_reachable") as mock_probe, \
             patch("frappe.only_for"), \
             patch("frappe.enqueue") as mock_enqueue:

            mock_get_doc.return_value = self._device()
            mock_get_doc.return_value.enable = 1
            mock_probe.return_value = (False, "Device cannot be reached. Check that it is powered on and connected to the network, then try again.")

            with self.assertRaises(frappe.ValidationError):
                endpoints.start_pull_checkins("Test-ZK-Device", run_id="run-test-1")

            # The background job must NOT have been queued.
            mock_enqueue.assert_not_called()

    def test_start_pull_checkins_queues_when_reachable(self):
        """When the probe passes, the pull job is queued as before."""
        import zkteco_attendance.zkteco_attendance.api.endpoints as endpoints

        with patch("frappe.get_doc") as mock_get_doc, \
             patch("zkteco_attendance.zkteco_attendance.zk_client.check_device_reachable") as mock_probe, \
             patch("frappe.only_for"), \
             patch("frappe.enqueue") as mock_enqueue, \
             patch("zkteco_attendance.zkteco_attendance.sync_engine._emit_progress"):

            mock_get_doc.return_value = self._device()
            mock_get_doc.return_value.enable = 1
            mock_probe.return_value = (True, "")

            result = endpoints.start_pull_checkins("Test-ZK-Device", run_id="run-test-2")

            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "queued")
            mock_enqueue.assert_called_once()

    def test_run_sync_job_translates_connection_error(self):
        """Background-job failures on connection errors get a friendly message."""
        from zkteco_attendance.zkteco_attendance import sync_engine

        with patch.object(sync_engine, "sync_device",
                          side_effect=frappe.ValidationError("Cannot connect to device Test-ZK-Device at 192.0.2.55:4370. Error: timed out")), \
             patch.object(sync_engine, "_get_cache"), \
             patch.object(sync_engine.frappe, "log_error"):

            payload = sync_engine.run_sync_job("Test-ZK-Device", run_id="run-test-3")

        self.assertFalse(payload["success"])
        self.assertIn("Device cannot be reached", payload["error"])
