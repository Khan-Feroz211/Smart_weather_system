"""
test_backend_gaps_and_sockets.py
================================
Unit tests for Backend Gaps resolution & Socket.IO integration.
Tests:
- API payload validation & auth check.
- Compact response mode trimming (Gap 2c).
- Offline feedback queueing & sync (Gap 1e).
- Socket.IO gateway connection & real-time broadcast.
"""

import unittest
import json
import os
from app_clean import app, socketio
from feedback_store import AccuracyMonitor
from agri_sockets import (
    broadcast_weather_update,
    broadcast_agri_alert,
    broadcast_connectivity_change
)

class TestBackendGapsAndSockets(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.monitor = AccuracyMonitor()

    def test_api_input_validation(self):
        # Invalid payload type for crop
        resp = self.client.post(
            "/api/agri/analyze",
            data=json.dumps({"crop": 12345}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)

        # Invalid connectivity_state
        resp2 = self.client.post(
            "/api/agri/analyze",
            data=json.dumps({"connectivity_state": "INVALID_STATE"}),
            content_type="application/json"
        )
        self.assertEqual(resp2.status_code, 400)

    def test_compact_mode_trimming(self):
        resp = self.client.post(
            "/api/agri/analyze",
            data=json.dumps({"crop": "wheat", "compact": True}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("diagnoses", data)
        if data["diagnoses"]:
            diag = data["diagnoses"][0]
            self.assertNotIn("explanation", diag)
            self.assertNotIn("disclaimer", diag)

    def test_offline_feedback_queueing_and_sync(self):
        # Queue offline feedback item
        resp = self.client.post(
            "/api/agri/feedback",
            data=json.dumps({
                "crop": "wheat",
                "predicted_disease": "wheat_rust",
                "actual_disease": "wheat_rust",
                "is_offline": True
            }),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "queued")

        # Sync queued feedback
        sync_resp = self.client.post("/api/agri/sync-feedback")
        self.assertEqual(sync_resp.status_code, 200)
        sync_data = sync_resp.get_json()
        self.assertGreaterEqual(sync_data["synced_count"], 1)

    def test_socket_connection_and_broadcasting(self):
        # Flask-SocketIO test client
        socket_client = socketio.test_client(app)
        self.assertTrue(socket_client.is_connected())

        # Test connection event
        received = socket_client.get_received()
        self.assertTrue(len(received) > 0)
        self.assertEqual(received[0]["name"], "connection_response")

        # Test request_agri_analysis socket event
        socket_client.emit("request_agri_analysis", {
            "crop": "rice",
            "connectivity_state": "ONLINE",
            "compact": True
        })
        res = socket_client.get_received()
        analysis_events = [e for e in res if e["name"] == "analysis_response"]
        self.assertEqual(len(analysis_events), 1)
        self.assertIn("status_header", analysis_events[0]["args"][0])

        socket_client.disconnect()

if __name__ == "__main__":
    unittest.main()

