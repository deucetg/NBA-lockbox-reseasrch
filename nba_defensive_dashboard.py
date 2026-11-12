"""NBA LOCKBOX team matchup dashboard from verified player box scores.

Outputs research-only team ratings and opponent allowances. Ratings use an
estimated possession formula, not official NBA.com possession tracking.
"""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path("data/nba_lockbox.sqlite")
OUT = Path("research_exports")
WINDOWS = (3, 5, 8, 12, 20)
FIELDS = ("pts", "fgm", "fga", "three_pm", "three_pa", "ftm", "fta",
          "oreb", "dreb", "reb", "ast", "turnovers")
REQUIRED = ("pts", "fga", "fta", "oreb", "turnovers")
COUNT_FIELDS = ("pts", "fga", "three_pa", "three_pm", "fta", "reb",
                "oreb", "dreb", "ast", "turnovers")


def season(date):
    date = str(date)
    year, month = int(date[:4]), int(date[4:6])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def ratio(numerator, denominator, multiplier=100):
    return round(multiplier * numerator / denominator, 2) if denominator else None


def write_csv(filename, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with (OUT / filename).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(team, games, window, current_season):
    sample = games[:window]
    n = len(sample)
    base = {
        "team_id": team, "season": current_season, "window": f"L{window}",
        "sample_n": n, "complete": "yes" if n == window else "no",
        "first_date": sample[-1]["date"], "last_date": sample[0]["date"],
        "source": "NBA LOCKBOX ESPN player_boxscores",
        "rating_method": "estimated possessions: FGA - OREB + TOV + 0.44*FTA",
    }
    for prefix in ("team", "opp"):
        for key in COUNT_FIELDS:
            base[f"{prefix}_{key}_pg"] = round(
                sum(g[prefix][key] for g in sample) / n, 2
            )
        makes = sum(g[prefix]["fgm"] for g in sample)
        shots = sum(g[prefix]["fga"] for g in sample)
        threes = sum(g[prefix]["three_pm"] for g in sample)
        three_attempts = sum(g[prefix]["three_pa"] for g in sample)
        free_makes = sum(g[prefix]["ftm"] for g in sample)
        free_attempts = sum(g[prefix]["fta"] for g in sample)
        points = sum(g[prefix]["pts"] for g in sample)
        base[f"{prefix}_fg_pct"] = ratio(makes, shots)
        base[f"{prefix}_3p_pct"] = ratio(threes, three_attempts)
        base[f"{prefix}_ft_pct"] = ratio(free_makes, free_attempts)
        base[f"{prefix}_efg_pct"] = ratio(makes + 0.5 * threes, shots)
        base[f"{prefix}_ts_pct"] = ratio(
            points, 2 * (shots + 0.44 * free_attempts)
        )
    possessions = sum(g["possessions"] for g in sample)
    team_points = sum(g["team"]["pts"] for g in sample)
    opp_points = sum(g["opp"]["pts"] for g in sample)
    base["estimated_possessions_pg"] = round(possessions / n, 2)
    base["ortg_est"] = ratio(team_points, possessions)
    base["drtg_est"] = ratio(opp_points, possessions)
    base["net_rating_est"] = round(
        base["ortg_est"] - base["drtg_est"], 2
    ) if possessions else None
    # Actual pace requires minutes/OT adjustments and possession tracking.
    base["pace_official"] = ""
    base["opponent_by_position"] = "unavailable: no verified position assignments"
    return base


def main():
    if not DB.exists():
        raise FileNotFoundError(f"Missing database: {DB}")
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(player_boxscores)")}
        missing = set(FIELDS) - columns
        if missing:
            raise ValueError(f"Missing boxscore columns: {sorted(missing)}")
        records = db.execute("""
            SELECT b.game_id, g.game_date, b.team_id,
                   b.pts, b.fgm, b.fga, b.three_pm, b.three_pa,
                   b.ftm, b.fta, b.oreb, b.dreb, b.reb, b.ast,
                   b.turnovers
            FROM player_boxscores b
            JOIN games g ON g.game_id = b.game_id
            ORDER BY g.game_date DESC, b.game_id DESC
        """).fetchall()
    if not records:
        raise ValueError("No player boxscores available")

    latest = max(str(r[1]) for r in records if str(r[1]).isdigit()
                 and len(str(r[1])) == 8)
    current_season = season(latest)
    game_teams = defaultdict(lambda: defaultdict(list))
    for row in records:
        gid, date, team = row[:3]
        date = str(date)
        if len(date) != 8 or not date.isdigit() or season(date) != current_season:
            continue
        game_teams[(str(gid), date)][str(team)].append(dict(zip(FIELDS, row[3:])))

    by_team = defaultdict(list)
    rejected = []
    for (gid, date), teams in game_teams.items():
        if len(teams) != 2:
            rejected.append({"game_id": gid, "date": date,
                             "reason": "not exactly two team IDs"})
            continue
        totals = {}
        invalid = False
        for team, players in teams.items():
            # Null counting stats indicate incomplete team data: reject game.
            if any(p[field] is None for p in players for field in FIELDS):
                invalid = True
                break
            totals[team] = {field: sum(p[field] for p in players) for field in FIELDS}
            if any(totals[team][field] < 0 for field in REQUIRED):
                invalid = True
        if invalid:
            rejected.append({"game_id": gid, "date": date,
                             "reason": "null or invalid team boxscore stat"})
            continue
        a, b = list(totals)
        possessions = {}
        for team in (a, b):
            t = totals[team]
            possessions[team] = (
                t["fga"] - t["oreb"] + t["turnovers"] + 0.44 * t["fta"]
            )
        estimated = (possessions[a] + possessions[b]) / 2
        if estimated <= 0:
            rejected.append({"game_id": gid, "date": date,
                             "reason": "nonpositive possessions"})
            continue
        for team, opponent in ((a, b), (b, a)):
            by_team[team].append({
                "game_id": gid, "date": date, "opponent_id": opponent,
                "team": totals[team], "opp": totals[opponent],
                "possessions": estimated,
            })

    dashboard = []
    matchups = []
    for team, games in sorted(by_team.items()):
        games.sort(key=lambda g: (g["date"], g["game_id"]), reverse=True)
        for window in WINDOWS:
            if not games:
                continue
            dashboard.append(summarize(team, games, window, current_season))
        for game in games[:20]:
            matchups.append({
                "team_id": team, "opponent_id": game["opponent_id"],
                "game_id": game["game_id"], "game_date": game["date"],
                "team_points": game["team"]["pts"],
                "opp_points": game["opp"]["pts"],
                "opp_fga": game["opp"]["fga"],
                "opp_three_pa": game["opp"]["three_pa"],
                "opp_reb": game["opp"]["reb"],
                "opp_ast": game["opp"]["ast"],
                "estimated_possessions": round(game["possessions"], 2),
            })
    if not dashboard:
        raise ValueError("No complete two-team games available")

    write_csv("team_defensive_dashboard.csv", dashboard)
    write_csv("team_opponent_game_logs.csv", matchups)
    write_csv("team_defensive_exclusions.csv", rejected if rejected else [{
        "game_id": "", "date": "", "reason": "none"
    }])
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "season": current_season, "latest_game_date": latest,
        "eligible_teams": len(by_team),
        "eligible_games": len(game_teams) - len(rejected),
        "excluded_games": len(rejected),
        "dashboard_rows": len(dashboard),
        "notes": [
            "Research only. No odds or tickets.",
            "ORTG/DRTG use estimated possessions, not official NBA.com ratings.",
            "Official pace, defensive scheme and opponent by position unavailable.",
            "No official team ratings should be inferred from these estimates.",
            "All windows include sample_n and complete labels.",
        ],
    }
    (OUT / "team_defensive_status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
