#!/usr/bin/env python3
"""Read-only NBA LOCKBOX V4 source probe. No database modifications."""
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

PLAYERS = {"LeBron James": "2544", "Stephen Curry": "201939", "Nikola Jokic": "203999"}
BASE = "https://stats.nba.com/stats/commonplayerinfo"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NBA-LOCKBOX-SourceProbe/1.0)",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}
results = []
for name, pid in PLAYERS.items():
    url = BASE + "?" + urllib.parse.urlencode({"LeagueID": "00", "PlayerID": pid})
    item = {"player": name, "nba_player_id": pid, "endpoint": BASE}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.load(response)
        sets = payload.get("resultSets") or payload.get("resultSet") or []
        if isinstance(sets, dict):
            sets = [sets]
        rows = next((x for x in sets if x.get("name") == "CommonPlayerInfo"), None)
        if not rows or not rows.get("rowSet"):
            raise ValueError("CommonPlayerInfo result missing")
        record = dict(zip(rows["headers"], rows["rowSet"][0]))
        item.update(status="ok", position=record.get("POSITION"), returned_player_id=record.get("PERSON_ID", record.get("PLAYER_ID")))
    except Exception as e:
        item.update(status="error", error_type=type(e).__name__, error=str(e)[:250])
    results.append(item)
report = {
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "purpose": "Test accessibility and detailed position values, not licensing or ESPN-ID matching",
    "results": results,
    "limitations": ["Does not prove permission for commercial reuse", "NBA IDs are not ESPN IDs", "Roster-listed position is not in-game defensive assignment"],
}
out = Path("research_exports/nba_v4_source_probe.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
