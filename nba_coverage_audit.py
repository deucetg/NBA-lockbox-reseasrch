"""Read-only NBA LOCKBOX historical database coverage audit.

Run from the repository root:
    python nba_coverage_audit.py

Optional: python nba_coverage_audit.py --db data/nba_lockbox.sqlite --out exports
"""
import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


def season(date):
    d = datetime.strptime(str(date), "%Y%m%d")
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def audit(db_path, out):
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    out.mkdir(parents=True, exist_ok=True)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {"games", "pbp_events", "player_boxscores"}
        if not needed.issubset(tables):
            raise ValueError(f"Missing required tables: {sorted(needed-tables)}")
        games = db.execute("SELECT game_id, game_date, home_team, away_team FROM games ORDER BY game_date,game_id").fetchall()
        pbp = dict(db.execute("SELECT game_id,COUNT(*) FROM pbp_events GROUP BY game_id"))
        boxes = dict(db.execute("SELECT game_id,COUNT(*) FROM player_boxscores GROUP BY game_id"))
        duplicates = db.execute("SELECT COUNT(*) FROM (SELECT game_id,play_id FROM pbp_events GROUP BY game_id,play_id HAVING COUNT(*)>1)").fetchone()[0]
        player_dupes = db.execute("SELECT COUNT(*) FROM (SELECT game_id,player_id FROM player_boxscores GROUP BY game_id,player_id HAVING COUNT(*)>1)").fetchone()[0]
    by_season = Counter()
    rows = []
    for game_id, date, home, away in games:
        try:
            s = season(date)
        except (ValueError, TypeError):
            s = "INVALID_DATE"
        by_season[s] += 1
        rows.append({"game_id":game_id,"date":date,"season":s,"home_team":home,"away_team":away,"pbp_events":pbp.get(game_id,0),"player_rows":boxes.get(game_id,0),"missing_pbp":int(not pbp.get(game_id,0)),"missing_boxscore":int(not boxes.get(game_id,0))})
    columns = ["game_id","date","season","home_team","away_team","pbp_events","player_rows","missing_pbp","missing_boxscore"]
    with (out/"game_coverage.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)
    summary = {"earliest_game_date":games[0][1] if games else None,"latest_game_date":games[-1][1] if games else None,"total_games":len(games),"total_pbp_events":sum(pbp.values()),"total_player_boxscore_rows":sum(boxes.values()),"games_missing_pbp":sum(r["missing_pbp"] for r in rows),"games_missing_boxscores":sum(r["missing_boxscore"] for r in rows),"duplicate_pbp_keys":duplicates,"duplicate_player_game_keys":player_dupes,"games_by_season":dict(sorted(by_season.items())),"caveat":"Counts cover imported games only. Missing scheduled games and regular-season/playoff classifications cannot be verified without external schedule reconciliation."}
    (out/"coverage_summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))
    print(f"Reports: {out/'coverage_summary.json'}, {out/'game_coverage.csv'}")

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=Path("data/nba_lockbox.sqlite"))
    p.add_argument("--out",type=Path,default=Path("exports"))
    args=p.parse_args()
    audit(args.db,args.out)
