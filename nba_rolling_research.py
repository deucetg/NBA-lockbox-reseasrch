import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path("data/nba_lockbox.sqlite")
OUT = Path("exports")
WINDOWS = (3, 5, 8, 12, 20)
FIELDS = (
    "minutes", "pts", "reb", "ast",
    "fga", "three_pa", "fta", "turnovers"
)


def minutes(value):
    if value is None or str(value).strip() in ("", "--"):
        return None
    raw = str(value).strip()
    if ":" in raw:
        mm, ss = raw.split(":", 1)
        return int(mm) + int(ss) / 60
    return float(raw)


def avg(records, field):
    if not records or any(
        r[field] is None for r in records
    ):
        return None
    return round(
        sum(r[field] for r in records) / len(records), 2
    )


def season(game_date):
    year = int(game_date[:4])
    month = int(game_date[4:6])
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def export(path, rows, columns):
    with path.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not DB.exists():
        raise FileNotFoundError(
            "Missing research database"
        )

    with sqlite3.connect(DB) as con:
        rows = con.execute("""
            SELECT b.player_id, b.player_name,
                   b.team_id, g.game_date,
                   b.minutes, b.pts, b.reb,
                   b.ast, b.fga, b.three_pa,
                   b.fta, b.turnovers
            FROM player_boxscores b
            JOIN games g ON g.game_id = b.game_id
            ORDER BY b.player_id,
                     g.game_date DESC,
                     g.game_id DESC
        """).fetchall()

    players = defaultdict(list)

    for row in rows:
        pid, name, team, date, mins, pts, reb, \
            ast, fga, pa3, fta, tov = row

        values = (
            minutes(mins), pts, reb, ast,
            fga, pa3, fta, tov
        )
        record = dict(zip(FIELDS, values))
        record.update(
            name=name, team=team, date=date
        )
        players[(pid, season(date))].append(record)

    rolling = []
    trends = []

    for (pid, yr), games in players.items():
        latest = games[0]

        for count in WINDOWS:
            batch = games[:count]
            complete = len(batch) == count

            result = {
                "player_id": pid,
                "player_name": latest["name"],
                "team_id": latest["team"],
                "season": yr,
                "window": f"L{count}",
                "sample_n": len(batch),
                "complete": "yes" if complete else "no",
                "first_date": batch[-1]["date"],
                "last_date": batch[0]["date"],
                "source": "ESPN",
            }

            for field in FIELDS:
                result[f"{field}_avg"] = (
                    avg(batch, field)
                    if complete else None
                )

            rolling.append(result)

        available = {
            "player_id": pid,
            "player_name": latest["name"],
            "team_id": latest["team"],
            "season": yr,
            "window": "IMPORTED_SEASON",
            "sample_n": len(games),
            "complete": "not verified",
            "first_date": games[-1]["date"],
            "last_date": games[0]["date"],
            "source": "ESPN",
        }

        for field in FIELDS:
            available[f"{field}_avg"] = avg(
                games, field
            )

        rolling.append(available)

        for count in (3, 5):
            if len(games) < 2 * count:
                continue

            recent = games[:count]
            previous = games[count:2 * count]

            trend = {
                "player_id": pid,
                "player_name": latest["name"],
                "team_id": latest["team"],
                "season": yr,
                "comparison": (
                    f"L{count} vs previous {count}"
                ),
                "recent_start": recent[-1]["date"],
                "recent_end": recent[0]["date"],
                "previous_start": previous[-1]["date"],
                "previous_end": previous[0]["date"],
                "sample_each": count,
                "source": "ESPN",
            }

            for field in FIELDS:
                a = avg(recent, field)
                b = avg(previous, field)

                trend[f"{field}_delta"] = (
                    round(a - b, 2)
                    if a is not None and b is not None
                    else None
                )

            trends.append(trend)

    if not players:
        raise ValueError(
            "No player-game records available"
        )

    OUT.mkdir(exist_ok=True)

    header = [
        "player_id", "player_name", "team_id",
        "season", "window", "sample_n",
        "complete", "first_date", "last_date",
        "source"
    ]

    export(
        OUT / "rolling_form.csv",
        rolling,
        header + [
            f"{field}_avg" for field in FIELDS
        ]
    )

    trend_header = [
        "player_id", "player_name", "team_id",
        "season", "comparison", "recent_start",
        "recent_end", "previous_start",
        "previous_end", "sample_each", "source"
    ]

    export(
        OUT / "non_overlapping_trends.csv",
        trends,
        trend_header + [
            f"{field}_delta" for field in FIELDS
        ]
    )

    print(f"Players/seasons: {len(players)}")
    print(f"Rolling rows: {len(rolling)}")
    print(
        f"Non-overlapping trend rows: {len(trends)}"
    )
    print("Exports created in exports/")


if __name__ == "__main__":
    main()
