"""NBA LOCKBOX: conservative quarter-level ESPN PBP score research.

Exports period end snapshots and period point totals only where both current
and previous period boundaries are unambiguous. No player stat attribution.
"""
import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def seconds_left(value):
    if value is None or str(value).strip() in ('', '--'):
        return None
    try:
        parts = str(value).strip().split(':')
        return float(parts[0]) * 60 + float(parts[1]) if len(parts) == 2 else float(parts[0])
    except (ValueError, TypeError):
        return None


def export(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(db_path, out_dir):
    with sqlite3.connect(db_path) as db:
        games = {str(gid): (date, home, away) for gid, date, home, away in db.execute(
            'SELECT game_id, game_date, home_team, away_team FROM games')}
        plays = db.execute('''SELECT game_id, play_id, period, clock, description,
            home_score, away_score, raw_json FROM pbp_events''').fetchall()

    grouped = defaultdict(lambda: defaultdict(list))
    for gid, play_id, period, clock, description, home, away, raw in plays:
        try:
            p, h, a = int(period), int(home), int(away)
        except (ValueError, TypeError):
            continue
        t = seconds_left(clock)
        if p < 1 or t is None or min(h, a) < 0:
            continue
        try:
            obj = json.loads(raw) if raw else {}
            event_type = obj.get('type', {})
            event_type = event_type.get('text', '') if isinstance(event_type, dict) else ''
        except (ValueError, TypeError):
            event_type = ''
        grouped[str(gid)][p].append((t, str(play_id), clock, description or '', h, a, event_type))

    summary, events = [], []
    for gid, periods in grouped.items():
        if gid not in games:
            continue
        date, home_team, away_team = games[gid]
        previous = (0, 0)
        previous_period = 0
        for p in sorted(periods):
            rows = sorted(periods[p], key=lambda x: -x[0])
            for t, play_id, clock, desc, h, a, event_type in rows:
                events.append(dict(game_id=gid, game_date=date, period=p,
                    play_id=play_id, clock=clock, seconds_remaining=t,
                    home_score=h, away_score=a, home_margin=h-a,
                    event_type=event_type, description=desc,
                    source='ESPN play-by-play', player_attribution='not_verified'))
            min_t = min(r[0] for r in rows)
            ending = {(r[4], r[5]) for r in rows if r[0] == min_t}
            if len(ending) != 1:
                end = None
                hp = ap = ''
                status = 'ambiguous_end_clock'
            else:
                end = next(iter(ending))
                if previous is None or p != previous_period + 1:
                    hp = ap = ''
                    status = 'missing_prior_period_boundary'
                elif end[0] < previous[0] or end[1] < previous[1]:
                    hp = ap = ''
                    status = 'nonmonotonic_score'
                else:
                    hp, ap = end[0] - previous[0], end[1] - previous[1]
                    status = 'provisional_period_totals'
            summary.append(dict(game_id=gid, game_date=date, home_team=home_team,
                away_team=away_team, period=p, home_points=hp, away_points=ap,
                home_end_score=end[0] if end else '',
                away_end_score=end[1] if end else '',
                end_margin=end[0]-end[1] if end else '',
                scored_events=len(rows), status=status, source='ESPN PBP'))
            previous, previous_period = end, p

    if not summary:
        raise ValueError('No valid ESPN score snapshots')
    export(out_dir / 'quarter_team_summary.csv', summary,
        ['game_id','game_date','home_team','away_team','period','home_points',
         'away_points','home_end_score','away_end_score','end_margin',
         'scored_events','status','source'])
    export(out_dir / 'quarter_score_events.csv', events,
        ['game_id','game_date','period','play_id','clock','seconds_remaining',
         'home_score','away_score','home_margin','event_type','description',
         'source','player_attribution'])
    print(f'Exported {len(summary)} period summaries and {len(events)} events')
    print('Quarter points remain provisional pending official box score comparison.')
    print('Player-level stats and minutes are not inferred.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/nba_lockbox.sqlite'))
    parser.add_argument('--out-dir', type=Path, default=Path('exports/quarters'))
    args = parser.parse_args()
    run(args.db, args.out_dir)
