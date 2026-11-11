"""NBA LOCKBOX research-only rolling prop hit-rate calculator."""
import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

WINDOWS = (3, 5, 8, 20)
THRESHOLDS = {
    "PTS": range(5, 51, 5), "REB": range(2, 21, 2),
    "AST": range(2, 17, 2), "3PM": range(1, 9),
    "PRA": range(10, 71, 5), "PR": range(10, 61, 5),
    "PA": range(10, 61, 5), "RA": range(5, 36, 5),
}

def season(date):
    year, month = int(date[:4]), int(date[4:6])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[-2:]}"

def main(db_path, out_path, season_filter=None):
    with sqlite3.connect(db_path) as db:
        rows = db.execute("""
            SELECT b.game_id, b.player_id, b.player_name, b.team_id,
                   g.game_date, b.minutes, b.pts, b.reb, b.ast, b.three_pm
            FROM player_boxscores b JOIN games g ON g.game_id=b.game_id
            WHERE b.pts IS NOT NULL AND b.reb IS NOT NULL
              AND b.ast IS NOT NULL AND b.three_pm IS NOT NULL
            ORDER BY b.player_id, g.game_date DESC, b.game_id DESC
        """).fetchall()
    grouped = defaultdict(list)
    for gid, pid, name, team, date, minutes, pts, reb, ast, threes in rows:
        date = str(date)
        if len(date) != 8 or not date.isdigit():
            continue
        yr = season(date)
        if season_filter and yr != season_filter:
            continue
        try:
            mins = (lambda a: int(a[0])+int(a[1])/60)(
                str(minutes).split(":")) if ":" in str(minutes) else float(minutes)
        except (ValueError, TypeError):
            continue
        if mins <= 0:
            continue
        stats = {"PTS":pts, "REB":reb, "AST":ast, "3PM":threes,
                 "PRA":pts+reb+ast, "PR":pts+reb,
                 "PA":pts+ast, "RA":reb+ast}
        grouped[(pid,yr)].append((gid,name,team,date,mins,stats))
    output = []
    for (pid,yr), games in grouped.items():
        unique = list({g[0]:g for g in games}.values())
        unique.sort(key=lambda g:(g[3],str(g[0])), reverse=True)
        for market, thresholds in THRESHOLDS.items():
            for threshold in thresholds:
                for window in WINDOWS:
                    sample = unique[:window]
                    n = len(sample)
                    hits = sum(g[5][market] >= threshold for g in sample)
                    output.append({
                        "player_id":pid, "player_name":unique[0][1],
                        "team_id":unique[0][2], "season":yr,
                        "market":market, "threshold":threshold,
                        "window":f"L{window}", "sample_n":n,
                        "window_complete":"yes" if n==window else "no",
                        "hits":hits, "hit_rate_pct":round(100*hits/n,1),
                        "avg_stat":round(sum(g[5][market] for g in sample)/n,2),
                        "avg_minutes":round(sum(g[4] for g in sample)/n,2),
                        "first_date":sample[-1][3], "last_date":sample[0][3],
                        "source":"ESPN player_boxscores"})
    if not output:
        raise ValueError("No eligible player-game records found")
    out_path.parent.mkdir(parents=True,exist_ok=True)
    with out_path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    print(f"Exported {len(output)} rows to {out_path}")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",type=Path,default=Path("data/nba_lockbox.sqlite"))
    parser.add_argument("--out",type=Path,default=Path("exports/prop_hit_rates.csv"))
    parser.add_argument("--season",default=None)
    args=parser.parse_args()
    main(args.db,args.out,args.season)
