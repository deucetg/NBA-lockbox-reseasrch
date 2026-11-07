import json
import sqlite3
import time
from pathlib import Path

import requests

DB = Path("data/nba_lockbox.sqlite")
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"


def number(stats, name):
    value = stats.get(name)
    if value is None or value in ("", "--"):
        return None
    return int(value)


def shooting(stats, name):
    value = stats.get(name)
    if value is None:
        return None, None
    parts = str(value).split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid {name}: {value}")
    made, attempted = map(int, parts)
    if not 0 <= made <= attempted:
        raise ValueError(f"Invalid shooting totals: {value}")
    return made, attempted


def parse_players(game_id, summary):
    teams = summary.get("boxscore", {}).get("players", [])
    if len(teams) != 2:
        raise ValueError(f"{game_id}: Expected two teams")

    rows = []

    for team in teams:
        team_id = str(team["team"]["id"])

        for group in team.get("statistics", []):
            headers = group.get("names") or group.get("labels")

            if not headers:
                continue

            for entry in group.get("athletes", []):
                if entry.get("didNotPlay"):
                    continue

                values = entry.get("stats") or []
                if not values:
                    continue

                if len(headers) != len(values):
                    raise ValueError(
                        f"{game_id}: Mismatched stat columns"
                    )

                stats = dict(zip(headers, values))
                required = {"MIN", "FG", "3PT", "FT",
                            "REB", "AST", "PTS"}

                if not required.issubset(stats):
                    raise ValueError(
                        f"{game_id}: Missing required stats"
                    )

                athlete = entry.get("athlete", {})
                player_id = athlete.get("id")

                
                if not player_id:
                    print(
                        f"WARNING: Skipping player entry without ID "
                        f"in game {game_id}: "
                        f"{athlete.get('displayName', 'Unknown')}"
                    )
                    continue

                fg_m, fg_a = shooting(stats, "FG")
                tp_m, tp_a = shooting(stats, "3PT")
                ft_m, ft_a = shooting(stats, "FT")

                pts = number(stats, "PTS")
                reb = number(stats, "REB")
                ast = number(stats, "AST")

                if None in (pts, reb, ast):
                    raise ValueError("Missing core player stats")

                if pts != 2 * fg_m + tp_m + ft_m:
                    raise ValueError(
                        f"{game_id}: Point total mismatch"
                    )

                rows.append((
                    game_id,
                    str(player_id),
                    team_id,
                    athlete.get("displayName"),
                    str(stats["MIN"]),
                    pts, reb, ast,
                    fg_m, fg_a,
                    tp_m, tp_a,
                    ft_m, ft_a,
                    number(stats, "OREB"),
                    number(stats, "DREB"),
                    number(stats, "STL"),
                    number(stats, "BLK"),
                    number(stats, "TO"),
                    number(stats, "PF"),
                    json.dumps(stats),
                    "ESPN"
                ))

    if not rows:
        raise ValueError(f"{game_id}: No player records")

    return rows


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS player_boxscores (
            game_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            player_name TEXT,
            minutes TEXT,
            pts INTEGER,
            reb INTEGER,
            ast INTEGER,
            fgm INTEGER,
            fga INTEGER,
            three_pm INTEGER,
            three_pa INTEGER,
            ftm INTEGER,
            fta INTEGER,
            oreb INTEGER,
            dreb INTEGER,
            stl INTEGER,
            blk INTEGER,
            turnovers INTEGER,
            fouls INTEGER,
            raw_stats_json TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (game_id, player_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS boxscore_imports (
            game_id TEXT PRIMARY KEY,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()


def main():
    if not DB.exists():
        raise FileNotFoundError(
            "Run nba_collector.py first"
        )

    with sqlite3.connect(DB) as db:
        initialize(db)

        games = db.execute("""
            SELECT game_id FROM games
            WHERE game_id NOT IN (
                SELECT game_id FROM boxscore_imports
            )
            ORDER BY game_date, game_id
            LIMIT 30
        """).fetchall()

        print(f"Games awaiting box scores: {len(games)}")

        with requests.Session() as session:
            for (game_id,) in games:
                response = session.get(
                    URL,
                    params={"event": game_id},
                    timeout=30
                )
                response.raise_for_status()

                rows = parse_players(
                    game_id, response.json()
                )

                with db:
                    db.executemany("""
                        INSERT OR IGNORE INTO player_boxscores
                        VALUES (
                            ?,?,?,?,?,?,?,?,?,?,?,?,
                            ?,?,?,?,?,?,?,?,?,?
                        )
                    """, rows)

                    db.execute("""
                        INSERT OR IGNORE INTO boxscore_imports
                        (game_id) VALUES (?)
                    """, (game_id,))

                print(
                    f"Saved game {game_id}: "
                    f"{len(rows)} player rows"
                )
                time.sleep(1)

        count = db.execute("""
            SELECT COUNT(*) FROM player_boxscores
        """).fetchone()[0]

        print(f"Total stored player-game rows: {count}")


if __name__ == "__main__":
    main()
