# Stride fitness tracker

A local, single-user workout journal. Log a date, duration, and exercises with sets and reps. The dashboard shows recent workouts and a Monday–Sunday summary. Data stays in SQLite across restarts.

## Run

```powershell
python app.py --port 8000 --database data/fitness.sqlite3
```

Open `http://127.0.0.1:8000`. Stop the app with Ctrl+C. A new database file is created automatically when needed. The server listens only on `127.0.0.1`.

## Test

```powershell
python -m unittest discover -s tests -p "test_*.py"
```
