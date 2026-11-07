import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DB = Path("data/nba_lockbox.sqlite")
DATE = (
    sys.argv[1]
    if len(sys.argv) > 1
    else (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
)

# Dates must use YYYYMMDD.
datetime.strptime(DATE, "%Y%m%d")


def fetch(session, endpoint, params):
    response = session.get(
        f"{BASE}/{endpoint}",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected ESPN response")
    return data


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS games (
            game_id TEXT PRIMARY KEY,
            game_date TEXT NOT NULL,
            home_team TEXT,
            away_team TEXT,
            source TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS pbp_events (
            game_id TEXT NOT NULL,
            play_id TEXT NOT NULL,
            period INTEGER,
            clock TEXT,
            description TEXT,
            home_score INTEGER,
            away_score INTEGER,
            raw_json TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (game_id, play_id)
        )
    """)
    db.commit()


def run():
    DB.parent.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session, sqlite3.connect(DB) as db:
        initialize(db)

        scoreboard = fetch(session, "scoreboard", {"dates": DATE})
        events = scoreboard.get("events", [])
        if not isinstance(events, list):
            raise ValueError("Missing scoreboard events list")

        completed = 0
        imported = 0

        for event in events:
            if not event.get("status", {}).get("type", {}).get("completed"):
                continue

            completed += 1
            game_id = str(event["id"])
            summary = fetch(session, "summary", {"event": game_id})
            plays = summary.get("plays")

            if not isinstance(plays, list) or not plays:
                print(f"Missing play-by-play: {game_id}")
                continue

            if any(play.get("id") is None for play in plays):
                print(f"Missing play IDs: {game_id}")
                continue

            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            teams = {
                team.get("homeAway"): team.get("team", {}).get("displayName")
                for team in competitors
            }

            with db:
                db.execute("""
                    INSERT OR IGNORE INTO games
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    game_id,
                    DATE,
                    teams.get("home"),
                    teams.get("away"),
                    "ESPN",
                ))

                for play in plays:
                    period = play.get("period") or {}
                    clock = play.get("clock") or {}

                    db.execute("""
                        INSERT OR IGNORE INTO pbp_events
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        game_id,
                        str(play["id"]),
                        period.get("number"),
                        clock.get("displayValue"),
                        play.get("text"),
                        play.get("homeScore"),
                        play.get("awayScore"),
                        json.dumps(play),
                        "ESPN",
                    ))

            imported += 1
            print(f"Saved {game_id}: {len(plays)} source events")
            time.sleep(1)

        if completed and not imported:
            raise RuntimeError("No completed games could be imported")

        print(f"Date: {DATE}")
        print(f"Completed games found: {completed}")
        print(f"Games processed: {imported}")
        print(f"Database: {DB}")


if __name__ == "__main__":
    run()
