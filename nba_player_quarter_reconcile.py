"""NBA LOCKBOX player-quarter points reconciliation (read-only source).

Consumes the scorer-identification CSV and ESPN player_boxscores SQLite table.
Never promotes unresolved scoring events to verified totals.
"""
import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path('exports/player_quarters')
IDENTIFIED = {'description_verified_first_participant',
              'description_verified_other_participant'}
QUARTER_FIELDS = ['q1', 'q2', 'q3', 'q4', 'ot_points']
OUTPUT_FIELDS = ['game_id', 'player_id', 'player_name', 'team_id',
                 *QUARTER_FIELDS, 'pbp_total', 'boxscore_pts', 'difference',
                 'unresolved_game_events', 'unresolved_game_points', 'status', 'source']
EXCEPTION_FIELDS = ['game_id', 'play_id', 'period', 'clock', 'description',
                    'first_participant_id', 'reported_points', 'status']


def read_csv(path):
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def run(db_path, audit_path, output_dir):
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    events = read_csv(audit_path)
    if not events:
        raise ValueError('Scorer identification CSV is empty')

    with sqlite3.connect(db_path) as db:
        boxscores = {
            (str(game), str(pid)): (str(name or ''), str(team or ''), pts)
            for game, pid, name, team, pts in db.execute(
                'SELECT game_id, player_id, player_name, team_id, pts '
                'FROM player_boxscores'
            )
        }

    totals = defaultdict(lambda: [0, 0, 0, 0, 0])
    game_unresolved = Counter()
    game_unresolved_points = Counter()
    exceptions = []
    seen = set()
    for event in events:
        game = str(event['game_id'])
        play = str(event['play_id'])
        key = (game, play)
        if key in seen:
            raise ValueError(f'Duplicate scoring event: {key}')
        seen.add(key)
        try:
            period = int(event['period'])
            points = int(event['reported_points'])
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Invalid scoring event: {key}') from exc
        if period < 1 or points not in (1, 2, 3):
            raise ValueError(f'Invalid period/points: {key}')

        status = event['status']
        pid = str(event['scorer_id'])
        if status in IDENTIFIED:
            if not pid or (game, pid) not in boxscores:
                raise ValueError(f'Identified scorer missing boxscore: {key}')
            index = period - 1 if period <= 4 else 4
            totals[(game, pid)][index] += points
        else:
            if pid:
                raise ValueError(f'Unresolved event has scorer ID: {key}')
            game_unresolved[game] += 1
            game_unresolved_points[game] += points
            exceptions.append({k: event.get(k, '') for k in EXCEPTION_FIELDS})

    rows = []
    for (game, pid), (name, team, pts) in sorted(boxscores.items()):
        quarter = totals[(game, pid)]
        pbp = sum(quarter)
        if pts is None:
            status = 'missing_boxscore_points'
            difference = ''
            box_pts = ''
        else:
            box_pts = int(pts)
            difference = pbp - box_pts
            if game_unresolved[game]:
                status = 'game_has_unresolved_scoring'
            elif difference == 0:
                status = 'full_game_points_match'
            else:
                status = 'full_game_points_mismatch'
        rows.append(dict(zip(OUTPUT_FIELDS, [
            game, pid, name, team, *quarter, pbp, box_pts, difference,
            game_unresolved[game], game_unresolved_points[game], status,
            'ESPN scoring PBP candidate IDs compared with ESPN boxscore',
        ])))

    if not rows:
        raise ValueError('No boxscore players found')
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, fields, data in (
        ('player_quarter_reconciliation.csv', OUTPUT_FIELDS, rows),
        ('player_quarter_unresolved_events.csv', EXCEPTION_FIELDS, exceptions),
    ):
        with (output_dir / filename).open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)

    counts = Counter(row['status'] for row in rows)
    print(f'Scoring events processed: {len(events)}')
    print(f'Unresolved scoring events: {len(exceptions)}')
    print(f'Player-game boxscore rows: {len(rows)}')
    for status, count in sorted(counts.items()):
        print(f'{status}: {count}')
    print('CAUTION: Quarter totals are provisional until discrepancies are resolved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/nba_lockbox.sqlite'))
    parser.add_argument('--audit', type=Path,
                        default=BASE / 'scorer_identification_audit.csv')
    parser.add_argument('--out-dir', type=Path, default=BASE)
    args = parser.parse_args()
    run(args.db, args.audit, args.out_dir)
