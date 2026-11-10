"""NBA LOCKBOX: apply independently verified quarter scores without modifying ESPN PBP.

Input reconciliation is a fixed research snapshot. New games not in that snapshot
remain provisional and must not be described as verified.
"""
import argparse
import csv
from collections import Counter
from pathlib import Path


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(summary_path, reconciliation_path, output_path):
    summary = read_csv(summary_path)
    reconciliation = read_csv(reconciliation_path)
    required = {'game_id', 'period', 'corrected_home', 'corrected_away',
                'original_status', 'research_status', 'nba_com_crosscheck', 'nba_com_url'}
    if not reconciliation or not required.issubset(reconciliation[0]):
        raise ValueError('Reconciliation CSV missing required columns')

    index = {}
    for row in reconciliation:
        key = (row['game_id'].strip(), row['period'].strip())
        if key in index:
            raise ValueError(f'Duplicate reconciliation key: {key}')
        if not row['corrected_home'].isdigit() or not row['corrected_away'].isdigit():
            raise ValueError(f'Missing or invalid official quarter points: {key}')
        if row['original_status'] != 'verified':
            if row['nba_com_crosscheck'] not in ('confirmed_team_quarter_linescore', 'confirmed'):
                raise ValueError(f'Exception lacks NBA.com cross-check: {key}')
            if not row['nba_com_url'].startswith('https://www.nba.com/game/'):
                raise ValueError(f'Exception lacks NBA.com game URL: {key}')
        index[key] = row

    result, seen = [], set()
    for row in summary:
        key = (row['game_id'].strip(), row['period'].strip())
        if key in seen:
            raise ValueError(f'Duplicate source quarter: {key}')
        seen.add(key)
        out = dict(row)
        rec = index.get(key)
        out['original_pbp_home_points'] = row['home_points']
        out['original_pbp_away_points'] = row['away_points']
        out['original_pbp_status'] = row['status']
        if rec:
            out['home_points'] = rec['corrected_home']
            out['away_points'] = rec['corrected_away']
            out['status'] = 'verified_official_quarter_score'
            out['source'] = ('NBA.com official linescore (cross-checked)' if
                rec['nba_com_crosscheck'] in ('confirmed_team_quarter_linescore', 'confirmed')
                else 'ESPN official linescore')
            out['verification_source_url'] = rec['nba_com_url']
            out['reconciliation_status'] = rec['research_status']
        else:
            out['verification_source_url'] = ''
            out['reconciliation_status'] = 'not_in_reconciliation_snapshot'
        result.append(out)

    missing = set(index) - seen
    if missing:
        raise ValueError(f'{len(missing)} reconciliation records absent from generated research; examples: {sorted(missing)[:5]}')
    fields = list(summary[0]) + ['original_pbp_home_points', 'original_pbp_away_points',
        'original_pbp_status', 'verification_source_url', 'reconciliation_status']
    write_csv(output_path, result, fields)
    print(f'Reconciled {len(index)} quarter records; {len(result)-len(index)} new records remain provisional.')
    print('Status counts:', dict(Counter(r['reconciliation_status'] for r in result)))
    print('Original ESPN play-by-play and provisional quarter exports are unchanged.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--summary', type=Path, default=Path('exports/quarters/quarter_team_summary.csv'))
    parser.add_argument('--reconciliation', type=Path, default=Path('nba_quarter_reconciliation_complete.csv'))
    parser.add_argument('--out', type=Path, default=Path('exports/quarters/quarter_team_verified.csv'))
    args = parser.parse_args()
    run(args.summary, args.reconciliation, args.out)
