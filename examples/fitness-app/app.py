"""Local fitness tracker, using only the Python standard library."""

import argparse
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_BODY_BYTES = 64 * 1024


@contextmanager
def connect(database):
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database(database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_date TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sets INTEGER NOT NULL CHECK (sets > 0),
                reps INTEGER NOT NULL CHECK (reps > 0)
            );
            CREATE INDEX IF NOT EXISTS workouts_date_idx ON workouts(workout_date);
            CREATE INDEX IF NOT EXISTS exercises_workout_idx ON exercises(workout_id);
            """
        )


def validate_workout(payload):
    if not isinstance(payload, dict):
        raise ValueError("Workout must be an object.")
    raw_date = payload.get("date")
    if not isinstance(raw_date, str):
        raise ValueError("Choose a workout date.")
    try:
        workout_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError("Use a valid date in YYYY-MM-DD format.") from exc
    if workout_date.isoformat() != raw_date:
        raise ValueError("Use a valid date in YYYY-MM-DD format.")
    duration = payload.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 1440:
        raise ValueError("Duration must be between 1 and 1,440 minutes.")
    exercises = payload.get("exercises")
    if not isinstance(exercises, list) or not 1 <= len(exercises) <= 30:
        raise ValueError("Add between 1 and 30 exercises.")
    cleaned = []
    for exercise in exercises:
        if not isinstance(exercise, dict):
            raise ValueError("Each exercise needs a name, sets, and reps.")
        name = exercise.get("name")
        sets = exercise.get("sets")
        reps = exercise.get("reps")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("Exercise names must be 1–80 characters long.")
        if any(isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 1000 for number in (sets, reps)):
            raise ValueError("Sets and reps must each be between 1 and 1,000.")
        cleaned.append({"name": name.strip(), "sets": sets, "reps": reps})
    return {"date": raw_date, "duration_minutes": duration, "exercises": cleaned}


def list_workouts(database):
    with connect(database) as connection:
        rows = connection.execute(
            "SELECT id, workout_date, duration_minutes FROM workouts "
            "ORDER BY workout_date DESC, id DESC LIMIT 30"
        ).fetchall()
        workouts = [
            {"id": row["id"], "date": row["workout_date"],
             "duration_minutes": row["duration_minutes"], "exercises": []}
            for row in rows
        ]
        if workouts:
            ids = [workout["id"] for workout in workouts]
            placeholders = ",".join("?" for _ in ids)
            exercise_rows = connection.execute(
                f"SELECT workout_id, name, sets, reps FROM exercises "
                f"WHERE workout_id IN ({placeholders}) ORDER BY id", ids
            ).fetchall()
            by_id = {workout["id"]: workout for workout in workouts}
            for row in exercise_rows:
                by_id[row["workout_id"]]["exercises"].append(
                    {"name": row["name"], "sets": row["sets"], "reps": row["reps"]}
                )
        return workouts


def weekly_summary(database, week_start):
    week_end = week_start + timedelta(days=6)
    next_week = week_end + timedelta(days=1)
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT w.id, w.workout_date, w.duration_minutes,
                   COALESCE(SUM(e.sets), 0) AS sets,
                   COALESCE(SUM(e.sets * e.reps), 0) AS reps
            FROM workouts w LEFT JOIN exercises e ON e.workout_id = w.id
            WHERE w.workout_date >= ? AND w.workout_date < ?
            GROUP BY w.id
            """,
            (week_start.isoformat(), next_week.isoformat()),
        ).fetchall()
    daily = [
        {"date": (week_start + timedelta(days=day)).isoformat(), "workouts": 0, "minutes": 0}
        for day in range(7)
    ]
    for row in rows:
        day = (date.fromisoformat(row["workout_date"]) - week_start).days
        daily[day]["workouts"] += 1
        daily[day]["minutes"] += row["duration_minutes"]
    return {
        "week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
        "workouts": len(rows),
        "minutes": sum(row["duration_minutes"] for row in rows),
        "sets": sum(row["sets"] for row in rows),
        "reps": sum(row["reps"] for row in rows),
        "daily": daily,
    }


def make_handler(database):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            data = (json.dumps(body).encode("utf-8") if content_type.startswith("application/json") else body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path in ("/", "/static/app.js", "/static/styles.css"):
                path = STATIC / ("index.html" if parsed.path == "/" else parsed.path.removeprefix("/static/"))
                content_type = {
                    ".html": "text/html; charset=utf-8",
                    ".js": "text/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                }[path.suffix]
                return self.respond(200, path.read_bytes(), content_type)
            if parsed.path == "/api/workouts":
                return self.respond(200, {"workouts": list_workouts(database)})
            if parsed.path == "/api/summary":
                value = parse_qs(parsed.query).get("week", [None])[0]
                try:
                    chosen = date.fromisoformat(value) if value else date.today()
                except ValueError:
                    return self.respond(400, {"error": "Week must be a valid date in YYYY-MM-DD format."})
                start = chosen - timedelta(days=chosen.weekday())
                return self.respond(200, weekly_summary(database, start))
            return self.respond(404, {"error": "Not found."})

        def do_POST(self):
            if urlsplit(self.path).path != "/api/workouts":
                return self.respond(404, {"error": "Not found."})
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError("Workout request is too large or empty.")
                payload = json.loads(self.rfile.read(length))
                workout = validate_workout(payload)
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                return self.respond(400, {"error": str(exc)})
            with connect(database) as connection:
                cursor = connection.execute(
                    "INSERT INTO workouts(workout_date, duration_minutes) VALUES (?, ?)",
                    (workout["date"], workout["duration_minutes"]),
                )
                connection.executemany(
                    "INSERT INTO exercises(workout_id, name, sets, reps) VALUES (?, ?, ?, ?)",
                    [(cursor.lastrowid, item["name"], item["sets"], item["reps"])
                     for item in workout["exercises"]],
                )
            return self.respond(201, {"id": cursor.lastrowid})

        def do_DELETE(self):
            path = urlsplit(self.path).path
            if not path.startswith("/api/workouts/") or not path.removeprefix("/api/workouts/").isdigit():
                return self.respond(404, {"error": "Not found."})
            workout_id = int(path.removeprefix("/api/workouts/"))
            with connect(database) as connection:
                deleted = connection.execute("DELETE FROM workouts WHERE id = ?", (workout_id,)).rowcount
            if not deleted:
                return self.respond(404, {"error": "Workout not found."})
            return self.respond(200, {"deleted": True})

    return Handler


def create_server(port, database):
    init_database(database)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(database))


def main():
    parser = argparse.ArgumentParser(description="Run the local fitness tracker")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", default=str(ROOT / "data" / "fitness.sqlite3"))
    args = parser.parse_args()
    server = create_server(args.port, args.database)
    print(f"Fitness tracker at http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
