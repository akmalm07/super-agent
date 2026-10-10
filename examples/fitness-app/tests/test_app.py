"""HTTP and SQLite integration tests for the local tracker."""

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from app import create_server


class FitnessTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "new" / "fitness.sqlite3")
        self.start_server()

    def tearDown(self):
        self.stop_server()
        self.tempdir.cleanup()

    def start_server(self):
        self.server = create_server(0, self.database)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type")
            return response.status, (json.loads(raw) if content_type.startswith("application/json") else raw)
        finally:
            connection.close()

    def test_fresh_database_and_frontend(self):
        self.assertTrue(Path(self.database).is_file())
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Log a workout", page)
        status, script = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/workouts", script)
        status, _ = self.request("GET", "/static/styles.css")
        self.assertEqual(status, 200)
        status, result = self.request("GET", "/api/workouts")
        self.assertEqual((status, result), (200, {"workouts": []}))

    def test_save_summary_and_restart_persistence(self):
        payload = {
            "date": "2026-10-05", "duration_minutes": 45,
            "exercises": [
                {"name": " Squats ", "sets": 3, "reps": 10},
                {"name": "Rows", "sets": 2, "reps": 8},
            ],
        }
        status, created = self.request("POST", "/api/workouts", payload)
        self.assertEqual(status, 201)
        self.assertGreater(created["id"], 0)
        self.request("POST", "/api/workouts", {
            "date": "2026-10-11", "duration_minutes": 30,
            "exercises": [{"name": "Run", "sets": 1, "reps": 1}],
        })
        self.request("POST", "/api/workouts", {
            "date": "2026-10-12", "duration_minutes": 60,
            "exercises": [{"name": "Walk", "sets": 1, "reps": 1}],
        })
        status, summary = self.request("GET", "/api/summary?week=2026-10-07")
        self.assertEqual(status, 200)
        self.assertEqual((summary["week_start"], summary["week_end"]), ("2026-10-05", "2026-10-11"))
        self.assertEqual((summary["workouts"], summary["minutes"], summary["sets"], summary["reps"]), (2, 75, 6, 47))
        self.assertEqual(summary["daily"][0]["minutes"], 45)
        self.assertEqual(summary["daily"][6]["minutes"], 30)

        self.stop_server()
        self.start_server()
        status, result = self.request("GET", "/api/workouts")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["workouts"]), 3)
        saved = next(item for item in result["workouts"] if item["id"] == created["id"])
        self.assertEqual(saved["exercises"], [
            {"name": "Squats", "sets": 3, "reps": 10},
            {"name": "Rows", "sets": 2, "reps": 8},
        ])

    def test_invalid_workouts_are_rejected_without_partial_saves(self):
        base = {"date": "2026-10-10", "duration_minutes": 40,
                "exercises": [{"name": "Push-ups", "sets": 3, "reps": 12}]}
        for change in (
            {"date": "2026-02-30"},
            {"duration_minutes": 0},
            {"duration_minutes": True},
            {"exercises": []},
            {"exercises": [{"name": " ", "sets": 2, "reps": 5}]},
            {"exercises": [{"name": "Squats", "sets": 2, "reps": 0}]},
        ):
            with self.subTest(change=change):
                status, result = self.request("POST", "/api/workouts", {**base, **change})
                self.assertEqual(status, 400)
                self.assertIn("error", result)
        self.assertEqual(self.request("GET", "/api/workouts")[1]["workouts"], [])

    def test_delete_removes_workout_and_exercises(self):
        payload = {"date": "2026-10-10", "duration_minutes": 20,
                   "exercises": [{"name": "Lunges", "sets": 2, "reps": 12}]}
        workout_id = self.request("POST", "/api/workouts", payload)[1]["id"]
        self.assertEqual(self.request("DELETE", f"/api/workouts/{workout_id}")[0], 200)
        self.assertEqual(self.request("DELETE", f"/api/workouts/{workout_id}")[0], 404)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM exercises").fetchone()[0], 0)
        finally:
            connection.close()
        self.assertEqual(self.request("GET", "/api/summary?week=2026-10-10")[1]["workouts"], 0)


if __name__ == "__main__":
    unittest.main()
