"""NBA LOCKBOX: conservative player scoring attribution audit.

Reads ESPN play-by-play and full-game player identities.
Does not alter SQLite data or publish verified quarter totals.
"""
import argparse
import csv
import json
import sqlite3
from pathlib import Path

FIELDS = [
    "game_id", "play_id", "period", "clock", "description",
    "event_type", "player_id", "player_name", "team_id",
    "reported_points", "status", "source",
]


def participants_with_ids(event):
    result = []
    for participant in event.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        athlete = participant.get("athlete")
        if isinstance(athlete, dict) and athlete.get("id") is not None:
            result.append(str(athlete["id"]))
    return list(dict.fromkeys(result))


def audit(db_path, out_path):
    with sqlite3.connect(db_path) as db:
        players = {
            (str(game_id), str(player_id)): (name, str(team_id))
            for game_id, player_id, name, team_id in db.execute(
                "SELECT game_id, player_id, player_name, team_id "
                "FROM player_boxscores"
            )
        }
        plays = db.execute(
            "SELECT game_id, play_id, period, clock, description, raw_json "
            "FROM pbp_events ORDER BY game_id, period, play_id"
        ).fetchall()

    results = []
    for game_id, play_id, period, clock, description, raw in plays:
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                continue
        except (ValueError, TypeError):
            continue

        if event.get("scoringPlay") is not True:
            continue

        kind = event.get("type") or {}
        event_type = kind.get("text", "") if isinstance(kind, dict) else ""
        points = event.get("scoreValue")
        ids = participants_with_ids(event)
        matching = [pid for pid in ids if (str(game_id), pid) in players]

        if not isinstance(points, int) or isinstance(points, bool) or points not in (1, 2, 3):
            status = "invalid_reported_points"
            chosen = ""
        elif len(matching) == 1 and len(ids) == 1:
            status = "player_identity_candidate"
            chosen = matching[0]
        elif not ids:
            status = "missing_player_id"
            chosen = ""
        elif not matching:
            status = "player_not_in_boxscore"
            chosen = ""
        else:
            status = "ambiguous_participants"
            chosen = ""

        name, team_id = players.get((str(game_id), chosen), ("", ""))

        results.append({
            "game_id": game_id,
            "play_id": play_id,
            "period": period,
            "clock": clock,
            "description": description,
            "event_type": event_type,
            "player_id": chosen,
            "player_name": name,
            "team_id": team_id,
            "reported_points": points,
            "status": status,
            "source": "ESPN play-by-play; player identity crosschecked against ESPN full-game boxscore",
        })

    if not results:
        raise ValueError("No scoring plays found; inspect ESPN source schema")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)

    from collections import Counter
    counts = Counter(row["status"] for row in results)
    print(f"Scoring events audited: {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"Report: {out_path}")
    print("NOTE: Candidates are not yet verified player quarter totals.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path, default=Path("data/nba_lockbox.sqlite")
    )
    parser.add_argument(
        "--out", type=Path,
        default=Path("exports/player_quarters/player_scoring_verification.csv")
    )
    args = parser.parse_args()
    audit(args.db, args.out)
