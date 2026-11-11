
"""Export compact, research-only NBA game logs, rolling form, and prop hit rates."""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path('data/nba_lockbox.sqlite')
OUT = Path('research_exports')
WINDOWS = (3, 5, 8, 12, 20)
THRESHOLDS = {
    'PTS': range(5, 51, 5),
    'REB': range(2, 21, 2),
    'AST': range(2, 17, 2),
    '3PM': range(1, 9),
    'PRA': range(10, 71, 5),
    'PR': range(10, 61, 5),
    'PA': range(10, 61, 5),
    'RA': range(5, 36, 5)
}

def season(d):
    y, m = int(d[:4]), int(d[4:6])
    y = y if m >= 7 else y - 1
    return f'{y}-{str(y+1)[-2:]}'

def minutes(s):
    if s is None or str(s).strip() in ('', '--'):
        return None
    s = str(s).strip()
    try:
        if ':' in s:
            a, b = s.split(':', 1)
            return int(a) + int(b) / 60
        return float(s)
    except (ValueError, TypeError):
        return None

def write(name, rows, fields):
    OUT.mkdir(exist_ok=True)
    with (OUT / name).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def main():
    if not DB.exists():
        raise FileNotFoundError(DB)

    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as db:
        rows = db.execute("""
            SELECT b.game_id, b.player_id, b.player_name,
                   b.team_id, g.game_date, b.minutes,
                   b.pts, b.reb, b.ast, b.three_pm,
                   b.fga, b.three_pa, b.fta, b.turnovers
            FROM player_boxscores b
            JOIN games g ON g.game_id = b.game_id
            ORDER BY g.game_date DESC, b.game_id DESC
        """).fetchall()

        game_count = db.execute(
            'SELECT COUNT(*) FROM games'
        ).fetchone()[0]

        box_count = db.execute(
            'SELECT COUNT(*) FROM player_boxscores'
        ).fetchone()[0]

    valid_dates = [
        str(r[4]) for r in rows
        if len(str(r[4])) == 8 and str(r[4]).isdigit()
    ]

    if not valid_dates:
        raise ValueError('No valid game dates')

    latest = max(valid_dates)
    active_season = season(latest)
    grouped = defaultdict(dict)

    for gid, pid, name, team, date, mins, pts, reb, ast, pm3, fga, pa3, fta, tov in rows:
        date = str(date)

        if len(date) != 8 or not date.isdigit():
            continue
        if season(date) != active_season:
            continue

        m = minutes(mins)
        if m is None or m <= 0:
            continue
        if any(x is None for x in (pts, reb, ast, pm3)):
            continue

        grouped[pid][str(gid)] = {
            'game_id': gid,
            'player_id': pid,
            'player_name': name,
            'team_id': team,
            'game_date': date,
            'minutes': round(m, 2),
            'pts': pts,
            'reb': reb,
            'ast': ast,
            'three_pm': pm3,
            'fga': fga,
            'three_pa': pa3,
            'fta': fta,
            'turnovers': tov,
            'PRA': pts + reb + ast,
            'PR': pts + reb,
            'PA': pts + ast,
            'RA': reb + ast
        }

    logs = []
    roll = []
    hits = []

    for pid, bygame in grouped.items():
        games = sorted(
            bygame.values(),
            key=lambda x: (x['game_date'], str(x['game_id'])),
            reverse=True
        )

        logs.extend(games[:20])

        for n in WINDOWS:
            batch = games[:n]
            if not batch:
                continue

            base = {
                'player_id': pid,
                'player_name': games[0]['player_name'],
                'team_id': games[0]['team_id'],
                'season': active_season,
                'window': f'L{n}',
                'sample_n': len(batch),
                'complete': 'yes' if len(batch) == n else 'no',
                'first_date': batch[-1]['game_date'],
                'last_date': batch[0]['game_date']
            }

            rr = dict(base)

            for field in (
                'minutes', 'pts', 'reb', 'ast', 'three_pm',
                'fga', 'three_pa', 'fta', 'turnovers',
                'PRA', 'PR', 'PA', 'RA'
            ):
                vals = [g[field] for g in batch]
                rr[field + '_avg'] = (
                    round(sum(vals) / len(vals), 2)
                    if len(batch) == n and all(v is not None for v in vals)
                    else ''
                )

            roll.append(rr)

            for market, thresholds in THRESHOLDS.items():
                for threshold in thresholds:
                    key = {
                        'PTS': 'pts',
                        'REB': 'reb',
                        'AST': 'ast',
                        '3PM': 'three_pm'
                    }.get(market, market)

                    vals = [g[key] for g in batch]
                    count = sum(v >= threshold for v in vals)

                    hits.append(dict(
                        base,
                        market=market,
                        threshold=threshold,
                        hits=count,
                        hit_rate_pct=round(
                            100 * count / len(vals), 1
                        ),
                        avg_stat=round(
                            sum(vals) / len(vals), 2
                        )
                    ))

    if not logs:
        raise ValueError('No eligible player game logs')

    write('player_last20.csv', logs, list(logs[0]))
    write('rolling_form.csv', roll, list(roll[0]))
    write('prop_hit_rates.csv', hits, list(hits[0]))

    status = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'latest_game_date': latest,
        'season': active_season,
        'database_games': game_count,
        'database_boxscore_rows': box_count,
        'eligible_players': len(grouped),
        'exported_player_game_rows': len(logs),
        'hit_rate_rows': len(hits),
        'source': 'ESPN player_boxscores via NBA LOCKBOX SQLite',
        'limitations': (
            'Current database season only; '
            'missing/incomplete windows labeled; '
            'not NBA.com tracking; no sportsbook odds.'
        )
    }

    (OUT / 'research_status.json').write_text(
        json.dumps(status, indent=2) + '\n',
        encoding='utf-8'
    )

    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
