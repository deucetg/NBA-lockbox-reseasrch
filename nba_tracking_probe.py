"""NBA LOCKBOX: safe NBA.com tracking connectivity probe (research only).

Fetches the Passing and Rebounding player tracking tables for the last 5 games.
Does NOT mutate the SQLite database, guess IDs, or commit data to the repository.
Only non-empty, validated NBA-ID-matched rows are exported.  The workflow
fails visibly if NBA.com blocks the GitHub-hosted runner.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

URL = "https://stats.nba.com/stats/leaguedashptstats"
OUT = Path("research_exports")
REQUEST_TIMEOUT = 35
LAST_N_GAMES = 5


def season_for(date):
    start_year = date.year if date.month >= 7 else date.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def params(kind, season):
    if kind not in ("Passing", "Rebounding"):
        raise ValueError(f"Unsupported tracking category: {kind}")
    return {
        "College": "", "Conference": "", "Country": "", "DateFrom": "",
        "DateTo": "", "Division": "", "DraftPick": "", "DraftYear": "",
        "GameScope": "", "Height": "", "LastNGames": LAST_N_GAMES,
        "LeagueID": "00", "Location": "", "Month": 0,
        "OpponentTeamID": 0, "Outcome": "", "PORound": "",
        "PerMode": "PerGame", "PlayerExperience": "", "PlayerOrTeam": "Player",
        "PlayerPosition": "", "PtMeasureType": kind, "Season": season,
        "SeasonSegment": "", "SeasonType": "Regular Season",
        "StarterBench": "", "TeamID": 0, "VsConference": "",
        "VsDivision": "", "Weight": "",
    }


def fetch(kind, season):
    query = urlencode(params(kind, season))
    req = Request(URL + "?" + query, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nba.com",
        "Referer": "https://www.nba.com/",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    })
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(12_000_000)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"{kind} request failed: {type(exc).__name__}: {exc}") from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{kind} response was not valid JSON (blocked/HTML response?)") from exc
    return parse_result(data, kind)


def parse_result(payload, kind):
    if not isinstance(payload, dict):
        raise ValueError(f"{kind}: unexpected response type")
    sets = payload.get("resultSets") or payload.get("resultSet")
    if isinstance(sets, dict):
        sets = [sets]
    if not isinstance(sets, list) or not sets:
        raise ValueError(f"{kind}: no result sets returned")
    chosen = next((x for x in sets if x.get("name") == "LeagueDashPtStats"), sets[0])
    headers = chosen.get("headers")
    rows = chosen.get("rowSet")
    if not isinstance(headers, list) or not isinstance(rows, list) or not rows:
        raise ValueError(f"{kind}: no tracking rows (NBA.com may be blocking access)")
    required = ("PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN")
    required += (("POTENTIAL_AST", "AST", "PASSES_MADE") if kind == "Passing"
                 else ("REB_CHANCES", "REB"))
    missing = sorted(set(required) - set(headers))
    if missing:
        raise ValueError(f"{kind}: missing required fields: {missing}; available={headers[:35]}")
    result = {}
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"{kind}: incompatible row width")
        record = dict(zip(headers, row))
        pid = str(record["PLAYER_ID"])
        if not pid or pid == "None" or pid in result:
            raise ValueError(f"{kind}: missing/duplicated NBA player ID {pid}")
        result[pid] = record
    return result


def intersect(passing, rebounding, season):
    # Match strictly by NBA PLAYER_ID, NOT by name or the ESPN player IDs
    # already used elsewhere in NBA LOCKBOX.
    output = []
    for pid in sorted(passing.keys() & rebounding.keys(), key=int):
        a, b = passing[pid], rebounding[pid]
        if str(a["TEAM_ID"]) != str(b["TEAM_ID"]):
            continue  # team ID mismatch; exclude ambiguous trades/roster situations
        output.append({
            "season": season, "window": f"L{LAST_N_GAMES}",
            "nba_player_id": pid,
            "player_name": a["PLAYER_NAME"],
            "nba_team_id": a["TEAM_ID"],
            "nba_team_abbreviation": a.get("TEAM_ABBREVIATION", ""),
            "passing_gp": a["GP"], "rebounding_gp": b["GP"],
            "passing_min_per_game": a["MIN"],
            "rebounding_min_per_game": b["MIN"],
            "potential_assists_per_game": a["POTENTIAL_AST"],
            "assists_per_game": a["AST"],
            "passes_made_per_game": a["PASSES_MADE"],
            "rebound_chances_per_game": b["REB_CHANCES"],
            "rebounds_per_game": b["REB"],
            "rebound_chance_pct_reported": b.get("REB_CHANCE_PCT", ""),
            "provider": "NBA.com tracking (stats.nba.com)",
        })
    if not output:
        raise ValueError("Passing/Rebounding results have no shared verified NBA player IDs")
    return output


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    season = season_for(datetime.now(timezone.utc))
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": season, "window": "L5", "success": False,
        "source": "NBA.com LeagueDashPtStats (Passing, Rebounding)",
        "disclaimer": "Connectivity pilot, NOT an activated nightly tracking importer; no guessed ESPN crosswalk",
    }
    try:
        print(f"Testing NBA.com Passing for {season}, L5", flush=True)
        passing = fetch("Passing", season)
        time.sleep(2)
        print(f"Testing NBA.com Rebounding for {season}, L5", flush=True)
        rebounding = fetch("Rebounding", season)
        joined = intersect(passing, rebounding, season)
        csvpath = OUT / "nba_tracking_probe_l5.csv"
        with csvpath.open("w", newline="", encoding="utf-8") as file:
            w = csv.DictWriter(file, fieldnames=list(joined[0]))
            w.writeheader()
            w.writerows(joined)
        status.update(success=True, passing_players=len(passing),
                      rebounding_players=len(rebounding), matched_players=len(joined),
                      sample_csv=str(csvpath),
                      caveats=["NBA player IDs have NOT been mapped to ESPN boxscore IDs",
                               "PerGame averages are source-reported, not reconstructed per-game events",
                               "NBA tracking coverage may omit some games"])
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    (OUT / "nba_tracking_probe_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, indent=2), flush=True)
    return 0 if status["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
