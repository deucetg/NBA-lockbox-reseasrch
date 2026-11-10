"""NBA LOCKBOX research-only threshold reliability report."""
import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

METRICS = {'pts': [10, 15, 20, 25, 30], 'reb': [4, 6, 8, 10, 12],
           'ast': [4, 6, 8, 10, 12], 'three_pm': [1, 2, 3, 4, 5]}
WINDOWS = [3, 5, 8, 12, 20]

def season(date):
    date = str(date).replace('-', '')
    year, month = int(date[:4]), int(date[4:6])
    start = year if month >= 7 else year - 1
    return f'{start}-{str(start + 1)[-2:]}'

def run(db_path, output_path):
    with sqlite3.connect(db_path) as db:
        rows = db.execute('''
            SELECT b.player_id, b.player_name, b.team_id, g.game_id,
                   g.game_date, b.minutes, b.pts, b.reb, b.ast, b.three_pm
            FROM player_boxscores b JOIN games g ON b.game_id = g.game_id
            ORDER BY b.player_id, g.game_date DESC, g.game_id DESC
        ''').fetchall()
    groups, seen = defaultdict(list), set()
    for pid, name, team, gid, date, mins, pts, reb, ast, threes in rows:
        key = (str(pid), str(gid))
        if key in seen:
            raise ValueError(f'Duplicate player/game: {key}')
        seen.add(key)
        if mins is None or str(mins).strip() in ('', '--', '0', '0:00'):
            continue
        groups[(str(pid), season(date))].append({
            'name': name, 'team': team, 'date': str(date),
            'pts': pts, 'reb': reb, 'ast': ast, 'three_pm': threes})
    results = []
    for (pid, yr), games in groups.items():
        for window in WINDOWS:
            sample = games[:window]
            for metric, thresholds in METRICS.items():
                observed = [g[metric] for g in sample if g[metric] is not None]
                for threshold in thresholds:
                    n = len(observed)
                    hits = sum(v >= threshold for v in observed)
                    results.append({
                        'player_id': pid, 'player_name': games[0]['name'],
                        'team_id': games[0]['team'], 'season': yr,
                        'window': f'L{window}', 'requested_n': window,
                        'sample_n': n, 'complete': 'yes' if n == window else 'no',
                        'metric': metric, 'threshold': threshold,
                        'hits': hits, 'hit_rate': round(hits / n, 4) if n else '',
                        'average': round(sum(observed) / n, 2) if n else '',
                        'latest_game_date': games[0]['date'],
                        'earliest_sample_date': sample[-1]['date'],
                        'source': 'ESPN boxscores', 'status': 'research_only'})
    if not results:
        raise ValueError('No eligible player-game data found')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f'Exported {len(results)} threshold research rows to {output_path}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/nba_lockbox.sqlite'))
    parser.add_argument('--out', type=Path, default=Path('exports/threshold_reliability.csv'))
    args = parser.parse_args()
    run(args.db, args.out)
