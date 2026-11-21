"""Audit ESPN participant roles on scoring plays.

Research-only: reads SQLite and writes diagnostic CSV files.
Does not infer scorers or modify the database.
"""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def role_details(participant):
    """Preserve potential role fields without assuming their meanings."""
    if not isinstance(participant, dict):
        return {"invalid_participant": as_text(participant)}

    athlete = participant.get("athlete")
    if not isinstance(athlete, dict):
        athlete = {}

    return {
        "athlete_id": as_text(athlete.get("id")),
        "athlete_name": as_text(
            athlete.get("displayName") or athlete.get("fullName")
        ),
        "participant_type": as_text(participant.get("type")),
        "participant_role": as_text(participant.get("role")),
        "participant_order": as_text(participant.get("order")),
        "participant_keys": ",".join(sorted(participant.keys())),
        "raw_participant": json.dumps(
            participant, ensure_ascii=False, sort_keys=True
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", type=Path, default=Path("data/nba_lockbox.sqlite")
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=Path("exports/player_quarters")
    )
    parser.add_argument(
        "--sample-limit", type=int, default=300
    )
    args = parser.parse_args()

    if not args.db.is_file():
        raise FileNotFoundError(args.db)
    if args.sample_limit < 1:
        raise ValueError("--sample-limit must be positive")

    summary = Counter()
    samples = []
    scoring_events = 0

    with sqlite3.connect(args.db) as db:
        rows = db.execute("""
            SELECT game_id, play_id, period, description, raw_json
            FROM pbp_events
            ORDER BY game_id, period, play_id
        """)

        for game_id, play_id, period, description, raw in rows:
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                summary["invalid_json"] += 1
                continue

            if not isinstance(event, dict):
                summary["invalid_json"] += 1
                continue

            if event.get("scoringPlay") is not True:
                continue

            scoring_events += 1
            participants = event.get("participants")
            if not isinstance(participants, list):
                participants = []

            summary["scoring_events"] += 1
            summary[
                "participant_count:" + str(len(participants))
            ] += 1

            event_type = event.get("type")
            if isinstance(event_type, dict):
                event_type = event_type.get("text", "")

            if not participants:
                summary["events_without_participants"] += 1

            for index, participant in enumerate(participants):
                details = role_details(participant)
                summary["participant_entries"] += 1

                for key in (
                    "participant_type",
                    "participant_role",
                    "participant_order",
                ):
                    value = details[key] or "<missing>"
                    summary[key + ":" + value] += 1

                summary[
                    "participant_keys:" + details["participant_keys"]
                ] += 1

                if details["athlete_id"]:
                    summary["participants_with_athlete_id"] += 1
                else:
                    summary["participants_without_athlete_id"] += 1

                # Prioritize multi-participant scoring plays.
                if len(participants) > 1 and len(samples) < args.sample_limit:
                    samples.append({
                        "game_id": game_id,
                        "play_id": play_id,
                        "period": period,
                        "description": description,
                        "event_type": as_text(event_type),
                        "score_value": as_text(event.get("scoreValue")),
                        "participant_count": len(participants),
                        "participant_index": index,
                        **details,
                    })

    if scoring_events == 0:
        raise ValueError("No scoring events found")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = args.out_dir / "participant_role_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "count"])
        for metric, count in sorted(summary.items()):
            writer.writerow([metric, count])

    sample_path = args.out_dir / "participant_role_samples.csv"
    sample_fields = [
        "game_id", "play_id", "period", "description",
        "event_type", "score_value", "participant_count",
        "participant_index", "athlete_id", "athlete_name",
        "participant_type", "participant_role",
        "participant_order", "participant_keys",
        "raw_participant",
    ]
    with sample_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=sample_fields)
        writer.writeheader()
        writer.writerows(samples)

    print(f"Scoring events examined: {scoring_events}")
    print(f"Participant entries examined: {summary['participant_entries']}")
    print(f"Multi-participant sample rows: {len(samples)}")
    print(f"Summary: {summary_path}")
    print(f"Samples: {sample_path}")
    print("No scorer attribution or quarter totals were inferred.")


if __name__ == "__main__":
    main()
