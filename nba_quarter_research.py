"""NBA LOCKBOX: conservative, research-only quarter-by-quarter PBP audit.

Uses the existing SQLite games/pbp_events tables. Produces event-level
quarter score margins and period summaries. Does NOT infer individual player
stats or minutes from descriptions; these require verified attribution.
"""
import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def clock_seconds(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value or value == '--':
        return None
    try:
        parts = value.split(':')
        if len(parts) == 2:
            return round(float(parts[0]) * 60 + float(parts[1]), 2)
        return round(float(parts[0]), 2)
    except (ValueError, TypeError):
        return None


def write_csv(path, records, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


def run(db_path, out_dir):
    if not db_path.is_file():
        raise FileNotFoundError(f'Missing research database: {db_path}')
    with sqlite3.connect(db_path) as con:
        games = {str(gid): (date, home, away) for gid, date, home, away in con.execute(
            'SELECT game_id, game_date, home_team, away_team FROM games')}
        events = con.execute('''
            SELECT game_id, play_id, period, clock, description,
                   home_score, away_score, raw_json
            FROM pbp_events
            ORDER BY game_id, period DESC, rowid DESC
        ''').fetchall()

    # PBP is stored with INSERT OR IGNORE; rowid preserves first import order,
    # but that is not guaranteed to be chronological. Order by period and clock
    # for score reconstruction, then resolve ties with rowid from the database.
    grouped = defaultdict(list)
    for gid, play_id, period, clock, description, home, away, raw in events:
        try:
            p = int(period)
        except (ValueError, TypeError):
            continue
        if p < 1:
            continue
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        grouped[str(gid)].append(dict(game_id=str(gid), play_id=str(play_id),
            period=p, clock=clock, seconds=clock_seconds(clock),
            description=description or '', home_score=home, away_score=away,
            event_type=(parsed.get('type') or {}).get('text') if isinstance(parsed.get('type'), dict) else None))

    summary, event_rows = [], []
    for gid, plays in grouped.items():
        if gid not in games:
            continue
        date, home_team, away_team = games[gid]
        # Only use score snapshots that are integers and never infer player credits.
        valid = []
        for e in plays:
            try:
                h, a = int(e['home_score']), int(e['away_score'])
                if h < 0 or a < 0:
                    continue
            except (ValueError, TypeError):
                continue
            if e['seconds'] is None:
                continue
            valid.append((e, h, a))
        # Sorting ties by original order from source import. Equal-clock events
        # can remain ambiguous; period score summary therefore flags validation.
        valid.sort(key=lambda x: (x[0]['period'], -x[0]['seconds']))
        per = defaultdict(list)
        for e, h, a in valid:
            per[e['period']].append((e, h, a))
            event_rows.append(dict(game_id=gid, game_date=date, period=e['period'],
                play_id=e['play_id'], clock=e['clock'], seconds_remaining=e['seconds'],
                home_score=h, away_score=a, home_margin=h-a,
                event_type=e['event_type'] or '', description=e['description'],
                source='ESPN play-by-play', player_attribution='not_verified'))
        prev_h = prev_a = 0
        for p in sorted(per):
            points = per[p]
            # Last source snapshot at minimum remaining time, unless multiple
            # snapshots at that time disagree: in that case mark ambiguous.
            min_sec = min(e['seconds'] for e, _, _ in points)
            ends = [(h, a) for e, h, a in points if e['seconds'] == min_sec]
            unique = set(ends)
            if len(unique) != 1:
                summary.append(dict(game_id=gid, game_date=date, home_team=home_team,
                    away_team=away_team, period=p, home_points='', away_points='',
                    home_end_score='', away_end_score='', end_margin='',
                    scored_events=len(points), status='ambiguous_end_clock', source='ESPN PBP'))
                continue
            end_h, end_a = ends[0]
            status = 'snapshot_only'
            if end_h >= prev_h and end_a >= prev_a:
                status = 'provisional_period_totals'
                hp, ap = end_h-prev_h, end_a-prev_a
            else:
                hp = ap = ''
                status = 'nonmonotonic_score'
            summary.append(dict(game_id=gid, game_date=date, home_team=home_team,
                away_team=away_team, period=p, home_points=hp, away_points=ap,
                home_end_score=end_h, away_end_score=end_a, end_margin=end_h-end_a,
                scored_events=len(points), status=status, source='ESPN PBP'))
            if status == 'provisional_period_totals':
                prev_h, prev_a = end_h, end_a
    if not summary:
        raise RuntimeError('No valid quarter score snapshots available')
    summary_columns = ['game_id','game_date','home_team','away_team','period',
        'home_points','away_points','home_end_score','away_end_score','end_margin',
        'scored_events','status','source']
    event_columns = ['game_id','game_date','period','play_id','clock',
        'seconds_remaining','home_score','away_score','home_margin','event_type',
        'description','source','player_attribution']
    write_csv(out_dir/'quarter_team_summary.csv', summary, summary_columns)
    write_csv(out_dir/'quarter_score_events.csv', event_rows, event_columns)
    print(f'Games: {len(grouped)}; quarter rows: {len(summary)}; score events: {len(event_rows)}')
    print('Player PTS/REB/AST and minutes intentionally NOT inferred from text.')
    print('Period totals provisional until checked against official quarter scores.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/nba_lockbox.sqlite'))
    parser.add_argument('--out-dir', type=Path, default=Path('exports/quarters'))
    args = parser.parse_args()
    run(args.db, args.out_dir)
