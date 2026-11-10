#!/usr/bin/env python3
"""NBA LOCKBOX — incremental official quarter-score verification.

Reads the reconciled quarter export; verifies only unverified records using
ESPN's official event summary. Writes a NEW CSV, never mutating source CSVs,
SQLite, raw play-by-play, or the manually audited reconciliation snapshot.

ESPN-confirmed values are official-source verified. Differences from PBP are
recorded explicitly. Missing/incomplete data stays provisional. NBA.com is
NOT silently assumed to have been cross-checked.
"""
import argparse
import csv
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
EXTRA = [
    "auto_verification_status", "auto_verification_source",
    "auto_verification_url", "auto_verified_at_utc",
    "auto_official_home_points", "auto_official_away_points",
    "auto_verification_note",
]
FINAL_STATES = {"post", "final"}


def integer(value):
    try:
        s = str(value).strip()
        if not s or "." in s:
            return None
        n = int(s)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def read_rows(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        required = {"game_id", "period", "home_points", "away_points", "status"}
        if not required.issubset(fields):
            raise ValueError(f"Missing input fields: {sorted(required - set(fields))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Input quarter CSV is empty")
    seen = set()
    for row in rows:
        key = (row["game_id"].strip(), row["period"].strip())
        if not key[0] or integer(key[1]) is None or integer(key[1]) < 1:
            raise ValueError(f"Invalid game/period key: {key}")
        if key in seen:
            raise ValueError(f"Duplicate quarter record: {key}")
        seen.add(key)
    return fields, rows


def fetch(game_id, retries, timeout):
    url = API + "?" + urlencode({"event": game_id})
    req = Request(url, headers={
        "User-Agent": "NBA-LOCKBOX-research/1.0",
        "Accept": "application/json",
    })
    error = "request_failed"
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.load(response), url, ""
        except (HTTPError, URLError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            error = type(exc).__name__
            if attempt + 1 < retries:
                time.sleep(min(2 * (attempt + 1), 8))
    return None, url, error


def official_periods(payload, game_id):
    """Return (period scores by home/away, error).

    Require a final game and matching ESPN event ID; never trust an
    in-progress scoreboard as an official final quarter score.
    """
    header = payload.get("header") or {}
    if str(header.get("id", "")) != str(game_id):
        return None, "event_id_mismatch"
    competitions = header.get("competitions") or []
    if len(competitions) != 1:
        return None, "missing_or_ambiguous_competition"
    competition = competitions[0]
    status = competition.get("status") or header.get("status") or {}
    state = ((status.get("type") or {}).get("state") or "").lower()
    completed = (status.get("type") or {}).get("completed")
    if state not in FINAL_STATES and completed is not True:
        return None, "game_not_final"
    competitors = competition.get("competitors") or []
    if len(competitors) != 2:
        return None, "missing_competitors"
    scores = {}
    for competitor in competitors:
        side = competitor.get("homeAway")
        if side not in ("home", "away") or side in scores:
            return None, "invalid_home_away"
        lines = competitor.get("linescores") or []
        if not lines:
            return None, "missing_linescores"
        periods = {}
        for idx, line in enumerate(lines, 1):
            period = integer(line.get("period", idx))
            points = integer(line.get("value", line.get("displayValue")))
            if period is None or period < 1 or points is None or period in periods:
                return None, "invalid_or_duplicate_linescore"
            periods[period] = points
        total = integer(competitor.get("score"))
        if total is None or sum(periods.values()) != total:
            return None, "linescore_total_disagrees_with_final_score"
        scores[side] = periods
    if set(scores) != {"home", "away"}:
        return None, "missing_home_away"
    if set(scores["home"]) != set(scores["away"]):
        return None, "periods_differ_between_teams"
    return scores, ""


def run(input_path, output_path, delay=0.4, retries=3, timeout=25):
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Output must differ from input; source must remain untouched")
    fields, rows = read_rows(input_path)
    pending = defaultdict(list)
    for row in rows:
        if row["status"] != "verified_official_quarter_score":
            pending[row["game_id"].strip()].append(row)
    print(f"Input records: {len(rows)}; pending games: {len(pending)}", flush=True)
    lookups = {}
    for idx, game_id in enumerate(pending, 1):
        payload, url, fetch_error = fetch(game_id, retries, timeout)
        scores, parse_error = (official_periods(payload, game_id)
                               if payload is not None else (None, fetch_error))
        lookups[game_id] = (scores, url, parse_error)
        if idx % 10 == 0:
            print(f"Fetched {idx}/{len(pending)} games", flush=True)
        if idx < len(pending):
            time.sleep(delay)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = []
    for source_row in rows:
        row = dict(source_row)
        row.update({field: "" for field in EXTRA})
        if row["status"] == "verified_official_quarter_score":
            row["auto_verification_status"] = "previously_verified_preserved"
            row["auto_verification_note"] = "Existing audited score and provenance unchanged"
            result.append(row)
            continue

        game_id = row["game_id"].strip()
        period = integer(row["period"])
        scores, url, error = lookups[game_id]
        row["auto_verification_url"] = url
        row["auto_verified_at_utc"] = now
        if scores is None:
            row["auto_verification_status"] = "unresolved"
            row["auto_verification_note"] = error
        elif period not in scores["home"] or period not in scores["away"]:
            row["auto_verification_status"] = "unresolved"
            row["auto_verification_note"] = "official_period_missing"
        else:
            home, away = scores["home"][period], scores["away"][period]
            row["auto_official_home_points"] = str(home)
            row["auto_official_away_points"] = str(away)
            row["auto_verification_source"] = "ESPN official final linescore"
            pbp_home = integer(row.get("original_pbp_home_points", row["home_points"]))
            pbp_away = integer(row.get("original_pbp_away_points", row["away_points"]))
            matches = pbp_home == home and pbp_away == away
            row["auto_verification_status"] = (
                "verified_espn_pbp_match" if matches else "verified_espn_pbp_mismatch"
            )
            row["auto_verification_note"] = (
                "PBP agrees with official linescore" if matches
                else "PBP differs or is unresolved; official linescore retained with discrepancy"
            )
            # Only a final, internally consistent ESPN official linescore
            # can promote a new record to verified.
            row["home_points"] = str(home)
            row["away_points"] = str(away)
            row["status"] = "verified_official_quarter_score"
            row["source"] = "ESPN official final linescore (auto-verified)"
            if "verification_source_url" in row:
                row["verification_source_url"] = url
            if "reconciliation_status" in row:
                row["reconciliation_status"] = row["auto_verification_status"]
        result.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = fields + [field for field in EXTRA if field not in fields]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result)
    counts = Counter(row["auto_verification_status"] for row in result)
    print("Auto verification results:", dict(counts))
    print(f"Wrote {len(result)} records to {output_path}")
    print("Source CSV, database, PBP events, and manual audit snapshot unchanged.")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path,
                        default=Path("exports/quarters/quarter_team_verified.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("exports/quarters/quarter_team_auto_verified.csv"))
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()
    if args.delay < 0 or args.retries < 1 or args.timeout < 1:
        parser.error("delay >= 0, retries >= 1, timeout >= 1 required")
    run(args.input, args.out, args.delay, args.retries, args.timeout)
