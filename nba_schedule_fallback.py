
"""Validate a sourced NBA schedule CSV for NBA LOCKBOX game matching.

Does not fetch protected endpoints, alter SQLite, or certify player statistics.
Only use schedule data obtained through an authorized source.
"""
import argparse
import csv
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

FIELDS = ('nba_game_id', 'game_date', 'home_team', 'away_team')


def validate(input_path, output_path, source_url):
    parsed = urlparse(source_url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Provide an HTTPS source URL documenting the schedule origin')
    if input_path.resolve() == output_path.resolve():
        raise ValueError('Input and output paths must differ')
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    rows_by_id = {}
    with input_path.open('r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(FIELDS).issubset(reader.fieldnames):
            raise ValueError(f'Required CSV columns: {", ".join(FIELDS)}')
        for number, record in enumerate(reader, start=2):
            if None in record:
                raise ValueError(f'Extra unnamed columns on row {number}')
            row = {field: str(record.get(field) or '').strip() for field in FIELDS}
            game_id = row['nba_game_id']
            if not re.fullmatch(r'\d{10}', game_id):
                raise ValueError(f'Invalid 10-digit NBA game ID on row {number}')
            try:
                if date.fromisoformat(row['game_date']).isoformat() != row['game_date']:
                    raise ValueError('Noncanonical date')
            except ValueError as exc:
                raise ValueError(f'Invalid YYYY-MM-DD game date on row {number}') from exc
            if not row['home_team'] or not row['away_team']:
                raise ValueError(f'Missing team on row {number}')
            if row['home_team'].casefold() == row['away_team'].casefold():
                raise ValueError(f'Identical home and away teams on row {number}')
            if game_id in rows_by_id:
                if rows_by_id[game_id] != row:
                    raise ValueError(f'Conflicting game ID {game_id}')
                raise ValueError(f'Duplicate game ID {game_id}')
            rows_by_id[game_id] = row

    if not rows_by_id:
        raise ValueError('Schedule contains no games')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + '.tmp')
    try:
        with temp_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows_by_id.values(), key=lambda r: (r['game_date'], r['nba_game_id'])))
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    print(f'Validated NBA schedule rows: {len(rows_by_id)}')
    print(f'Source declared: {source_url}')
    print(f'Validated CSV: {output_path}')
    print('WARNING: Source URL is recorded, not independently authenticated.')
    print('Game identity candidates only; no player statistics certified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--out', type=Path, default=Path('exports/player_quarters/nba_validated_schedule.csv'))
    args = parser.parse_args()
    validate(args.input, args.out, args.source_url)
