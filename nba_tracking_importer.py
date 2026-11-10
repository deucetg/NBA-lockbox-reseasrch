
"""NBA LOCKBOX: validate and normalize authorized NBA tracking CSV exports.

No scraping, network calls, database mutations, or fabricated game logs.
Season aggregates and player-game observations are stored separately.
"""
import argparse
import csv
import math
import re
from datetime import date
from pathlib import Path

METRICS = {
    'passing': {
        'potential_assists': ['POTENTIAL AST', 'POTENTIAL_AST', 'POTENTIAL ASSISTS'],
        'assists': ['AST', 'ASSISTS'],
        'passes_made': ['PASSES MADE', 'PASSES_MADE'],
        'passes_received': ['PASSES RECEIVED', 'PASSES_RECEIVED'],
        'assist_points_created': ['AST PTS CREATED', 'AST_PTS_CREATED'],
    },
    'rebounding': {
        'rebound_chances': ['REB CHANCES', 'REB_CHANCES', 'REBOUND CHANCES'],
        'rebounds': ['REB', 'REBOUNDS'],
        'rebound_chance_pct_source': ['REB CHANCE%', 'REB_CHANCE_PCT', 'REB CHANCE PCT'],
    },
    'touches': {
        'touches': ['TOUCHES'],
        'time_of_possession': ['TIME OF POSS', 'TIME_OF_POSS', 'TIME OF POSSESSION'],
        'avg_seconds_per_touch': ['AVG SEC PER TOUCH', 'AVG_SEC_PER_TOUCH'],
        'drives': ['DRIVES'],
    },
}
COMMON = {
    'player': ['PLAYER', 'PLAYER NAME', 'PLAYER_NAME'],
    'player_id': ['PLAYER_ID', 'PLAYER ID'],
    'team': ['TEAM', 'TEAM ABBREVIATION', 'TEAM_ABBREVIATION'],
    'games_played': ['GP', 'GAMES PLAYED'],
    'minutes': ['MIN', 'MINUTES'],
    'game_id': ['GAME_ID', 'GAME ID'],
    'game_date': ['GAME_DATE', 'GAME DATE', 'DATE'],
}


def norm(text):
    return re.sub(r'[^A-Z0-9]', '', str(text or '').upper())


def columns_for(headers, category):
    indexed = {}
    for h in headers:
        key = norm(h)
        if key in indexed:
            raise ValueError(f'Duplicate normalized header: {h}')
        indexed[key] = h
    return {
        name: next((indexed[norm(a)] for a in aliases if norm(a) in indexed), None)
        for name, aliases in {**COMMON, **METRICS[category]}.items()
    }


def numeric(raw, label, line):
    value = str(raw or '').strip().replace(',', '')
    if value in ('', '-', '--', 'N/A'):
        return ''
    try:
        n = float(value.removesuffix('%'))
    except ValueError as exc:
        raise ValueError(f'Invalid {label} on row {line}: {value!r}') from exc
    if not math.isfinite(n) or n < 0:
        raise ValueError(f'Invalid {label} on row {line}: {value!r}')
    return str(n)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--category', choices=sorted(METRICS), required=True)
    p.add_argument('--grain', choices=['season', 'game'], required=True)
    p.add_argument('--season', required=True, help='Example: 2026-27')
    p.add_argument('--season-type', default='Regular Season')
    p.add_argument('--source-url', required=True)
    args = p.parse_args()
    if not re.fullmatch(r'20\d\d-\d\d', args.season) or int(args.season[-2:]) != (int(args.season[:4]) + 1) % 100:
        p.error('Season must be a consecutive pair such as 2026-27')
    if not args.source_url.startswith('https://www.nba.com/stats/'):
        p.error('Provide the NBA.com stats page URL used for this export')
    if args.input.resolve() == args.out.resolve():
        p.error('Input and output must be different paths')
    if not args.input.is_file():
        p.error('Input CSV does not exist')

    with args.input.open(newline='', encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        cols = columns_for(reader.fieldnames or [], args.category)
        if not cols['player']:
            p.error('CSV is missing PLAYER column')
        found = [k for k in METRICS[args.category] if cols[k]]
        if not found:
            p.error('CSV contains no recognized tracking metrics for this category')
        if args.grain == 'game' and (not cols['game_id'] or not cols['game_date']):
            p.error('Game-grain imports require GAME_ID and GAME_DATE columns')
        records = list(reader)
    if not records:
        p.error('CSV has no data rows')

    fields = ['season', 'season_type', 'category', 'grain', 'player', 'player_id',
              'team', 'game_id', 'game_date', 'games_played', 'minutes',
              *METRICS[args.category], 'source_url']
    output = []
    seen = set()
    for line, rec in enumerate(records, start=2):
        if None in rec:
            raise ValueError(f'Extra CSV columns on row {line}')
        row = dict.fromkeys(fields, '')
        row.update(season=args.season, season_type=args.season_type,
                   category=args.category, grain=args.grain, source_url=args.source_url)
        for name in COMMON:
            if cols[name]:
                row[name] = str(rec.get(cols[name]) or '').strip()
        if not row['player']:
            raise ValueError(f'Missing player on row {line}')
        if args.grain == 'game':
            if not row['game_id'] or not row['game_date']:
                raise ValueError(f'Missing game identity on row {line}')
            try:
                if date.fromisoformat(row['game_date']).isoformat() != row['game_date']:
                    raise ValueError('Noncanonical date')
            except ValueError as exc:
                raise ValueError(f'Use YYYY-MM-DD for game date on row {line}') from exc
        else:
            row['game_id'] = row['game_date'] = ''
        for name in ['games_played', 'minutes', *METRICS[args.category]]:
            if cols[name]:
                row[name] = numeric(rec.get(cols[name]), name, line)
        identity = row['player_id'] or row['player'].casefold()
        key = (args.grain, args.category, args.season, args.season_type,
               row['game_id'], row['team'], identity)
        if key in seen:
            raise ValueError(f'Duplicate player observation on row {line}: {key}')
        seen.add(key)
        output.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + '.tmp')
    try:
        with tmp.open('w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(output)
        tmp.replace(args.out)
    finally:
        tmp.unlink(missing_ok=True)
    print(f'Imported {len(output)} {args.category} observations ({args.grain} grain)')
    print('Available metrics:', ', '.join(found))
    print('Missing metrics:', ', '.join(k for k in METRICS[args.category] if k not in found) or 'none')
    print('Output:', args.out)
    print('No tracking metrics were inferred from box scores or other grains.')


if __name__ == '__main__':
    main()
