"""NBA LOCKBOX: reusable, conservative cross-source game identity mapper.

Reads ESPN games from SQLite. Optionally matches an independently collected
NBA.com schedule CSV with columns nba_game_id,game_date,home_team,away_team.
Never changes SQLite or guesses a match from a team name alone.
"""
import argparse
import csv
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FIELDS = ['espn_game_id', 'game_date', 'home_team', 'away_team',
          'nba_game_id', 'mapping_status', 'candidate_count', 'evidence']

ALIASES = {
    'la clippers': 'los angeles clippers',
    'los angeles clippers': 'los angeles clippers',
    'ny knicks': 'new york knicks',
    'new york knicks': 'new york knicks',
    'golden state warriors': 'golden state warriors',
}


def canonical_team(name):
    s = unicodedata.normalize('NFKD', str(name or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-z0-9]+', ' ', s.casefold()).strip()
    return ALIASES.get(s, s)


def canonical_date(value):
    s = str(value or '').strip()
    for fmt in ('%Y%m%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    raise ValueError(f'Invalid date: {value!r}')


def read_nba_schedule(path):
    if path is None:
        return []
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'nba_game_id', 'game_date', 'home_team', 'away_team'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'NBA schedule must include: {sorted(required)}')
        result = []
        for row in reader:
            if not row['nba_game_id'].strip():
                raise ValueError('NBA schedule contains blank nba_game_id')
            result.append(row)
        return result


def run(db_path, out_path, nba_schedule_path=None):
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path) as db:
        games = db.execute('SELECT game_id, game_date, home_team, away_team '
                           'FROM games ORDER BY game_date, game_id').fetchall()
    if not games:
        raise ValueError('No games in database')

    nba_index = defaultdict(list)
    for row in read_nba_schedule(nba_schedule_path):
        key = (canonical_date(row['game_date']), canonical_team(row['home_team']),
               canonical_team(row['away_team']))
        nba_index[key].append(row['nba_game_id'].strip())

    output = []
    for gid, date, home, away in games:
        iso = canonical_date(date)
        if not home or not away:
            matches, status, evidence = [], 'missing_espn_teams', 'ESPN games table'
        elif nba_schedule_path is None:
            matches, status, evidence = [], 'nba_schedule_not_supplied', 'ESPN games table only'
        else:
            key = (iso, canonical_team(home), canonical_team(away))
            matches = sorted(set(nba_index.get(key, [])))
            status = ('unique_schedule_match' if len(matches) == 1 else
                      'ambiguous_schedule_match' if matches else 'no_schedule_match')
            evidence = 'Exact date + canonical home/away team match; NBA schedule CSV'
        output.append(dict(zip(FIELDS, [str(gid), iso, home or '', away or '',
                                        matches[0] if len(matches) == 1 else '',
                                        status, len(matches), evidence])))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    print(f'ESPN games: {len(output)}')
    for status in sorted(set(r['mapping_status'] for r in output)):
        print(f'{status}: {sum(r["mapping_status"] == status for r in output)}')
    for gid in ('401810760', '401909867'):
        row = next((r for r in output if r['espn_game_id'] == gid), None)
        print(f'Investigation {gid}: {row if row else "not in games table"}')
    print(f'Report: {out_path}')
    print('Schedule matches are candidates; event-level verification is separate.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/nba_lockbox.sqlite'))
    parser.add_argument('--out', type=Path, default=Path('exports/player_quarters/game_identity_map.csv'))
    parser.add_argument('--nba-schedule', type=Path, default=None,
                        help='Optional NBA.com schedule CSV (never inferred or invented)')
    args = parser.parse_args()
    run(args.db, args.out, args.nba_schedule)
