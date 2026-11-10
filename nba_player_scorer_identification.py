"""Conservative ESPN scoring-player identification diagnostic.

Read-only SQLite input. CSV output is not certified player quarter totals.
"""
import argparse
import csv
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

FIELDS = [
    "game_id", "play_id", "period", "clock", "description",
    "event_type", "reported_points", "participant_count",
    "first_participant_id", "scorer_id", "scorer_name", "team_id",
    "description_name", "status", "source",
]


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("’", "'")
    # ESPN descriptions can write dotted initials ("L.J. Cryer") while
    # box scores write the same initials without dots ("LJ Cryer").
    # Remove dots immediately following single-letter initials ONLY.
    value = re.sub(r"\b([a-z])\.", r"\1", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def participant_ids(event):
    ids = []
    for item in event.get("participants") or []:
        if not isinstance(item, dict):
            continue
        athlete = item.get("athlete")
        if isinstance(athlete, dict) and athlete.get("id") is not None:
            ids.append(str(athlete["id"]))
    return ids


def extract_scorer_phrase(description):
    """Only use descriptions with an explicit scoring action."""
    text = str(description or "").strip()
    patterns = (
        r"^(.+?)\s+(?:makes|made|hits|hit)\s+",
        r"^(.+?)\s+(?:makes|made)\s+(?:free throw|technical free throw)",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def identify(game_id, description, ids, players):
    """Require a unique full-name match among event participants."""
    phrase = extract_scorer_phrase(description)
    if not ids:
        return "", phrase, "missing_participant_id"
    if len(set(ids)) != len(ids):
        return "", phrase, "duplicate_participant_id"
    if not phrase:
        return "", phrase, "unsupported_description"

    matching = []
    for pid in ids:
        player = players.get((str(game_id), pid))
        if player and normalize(player[0]) == normalize(phrase):
            matching.append(pid)

    if len(matching) != 1:
        return "", phrase, (
            "multiple_name_matches" if matching else "no_exact_name_match"
        )
    chosen = matching[0]
    return chosen, phrase, (
        "description_verified_first_participant"
        if chosen == ids[0]
        else "description_verified_other_participant"
    )


def run(db_path, output):
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path) as db:
        players = {
            (str(game), str(pid)): (str(name or ""), str(team or ""))
            for game, pid, name, team in db.execute(
                "SELECT game_id, player_id, player_name, team_id "
                "FROM player_boxscores"
            )
        }
        plays = db.execute(
            "SELECT game_id, play_id, period, clock, description, raw_json "
            "FROM pbp_events ORDER BY game_id, period, play_id"
        ).fetchall()

    rows = []
    seen = set()
    for game_id, play_id, period, clock, description, raw in plays:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("scoringPlay") is not True:
            continue

        key = (str(game_id), str(play_id))
        if key in seen:
            raise ValueError(f"Duplicate scoring play: {key}")
        seen.add(key)

        ids = participant_ids(event)
        points = event.get("scoreValue")
        kind = event.get("type")
        event_type = kind.get("text", "") if isinstance(kind, dict) else ""
        scorer, phrase, status = identify(game_id, description, ids, players)

        if type(points) is not int or points not in (1, 2, 3):
            scorer = ""
            status = "invalid_reported_points"

        name, team = players.get((str(game_id), scorer), ("", ""))
        rows.append({
            "game_id": game_id, "play_id": play_id,
            "period": period, "clock": clock,
            "description": description, "event_type": event_type,
            "reported_points": points,
            "participant_count": len(ids),
            "first_participant_id": ids[0] if ids else "",
            "scorer_id": scorer, "scorer_name": name,
            "team_id": team, "description_name": phrase,
            "status": status,
            "source": "ESPN PBP description and participant IDs; ESPN game boxscore player identity",
        })

    if not rows:
        raise ValueError("No ESPN scoring plays found")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["status"] for row in rows)
    print(f"Scoring plays: {len(rows)}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"Report: {output}")
    print("Diagnostic only: no player quarter totals certified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/nba_lockbox.sqlite"))
    parser.add_argument(
        "--out", type=Path,
        default=Path("exports/player_quarters/scorer_identification_audit.csv"),
    )
    args = parser.parse_args()
    run(args.db, args.out)
