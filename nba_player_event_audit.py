
"""Audit ESPN player attribution in stored NBA play-by-play events.

Research-only. Does not infer player stats or modify the database.
"""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/nba_lockbox.sqlite"))
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("exports/player_quarters/player_event_audit.csv"),
    )
    args = parser.parse_args()

    if not args.db.is_file():
        raise FileNotFoundError(args.db)

    counts = Counter()

    with sqlite3.connect(args.db) as db:
        rows = db.execute("""
            SELECT game_id, period, raw_json
            FROM pbp_events
        """)

        for game_id, period, raw in rows:
            counts["total_events"] += 1

            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                counts["invalid_json"] += 1
                continue

            if not isinstance(event, dict):
                counts["invalid_json"] += 1
                continue

            participants = event.get("participants")
            if isinstance(participants, list) and participants:
                counts["events_with_participants"] += 1
                if any(
                    isinstance(p, dict)
                    and isinstance(p.get("athlete"), dict)
                    and p["athlete"].get("id")
                    for p in participants
                ):
                    counts["events_with_athlete_ids"] += 1
                else:
                    counts["participants_without_athlete_ids"] += 1
            else:
                counts["events_without_participants"] += 1

            if period is None:
                counts["missing_period"] += 1

            event_type = event.get("type")
            if isinstance(event_type, dict):
                name = str(event_type.get("text") or "unknown")
            else:
                name = "unknown"

            counts[f"type:{name}"] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)

    with args.out.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "count"])
        for metric, count in sorted(counts.items()):
            writer.writerow([metric, count])

    for metric, count in sorted(counts.items()):
        print(f"{metric}: {count}")

    print(f"Audit saved: {args.out}")


if __name__ == "__main__":
    main()
