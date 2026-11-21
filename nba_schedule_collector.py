"""NBA LOCKBOX official NBA schedule collector (read-only, fail-closed).

Produces a CSV accepted by nba_game_identity_mapper.py. The NBA CDN's
scheduleLeagueV2 feed may cover only the current season; absence is not a
negative verification of historical games. No SQLite writes are performed.
"""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_URL = 'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json'
FIELDS = ['nba_game_id', 'game_date', 'home_team', 'away_team']


def team_name(team):
    if not isinstance(team, dict):
        return ''
    city = str(team.get('teamCity') or '').strip()
    name = str(team.get('teamName') or '').strip()
    if city and name:
        return f'{city} {name}'
    return str(team.get('teamName') or '').strip()


def parse_date(game, date_group):
    # NBA schedule supplies Eastern-local game date; do not convert UTC tipoff
    # across midnight, which can change the scheduled basketball date.
    raw = (game.get('gameDateEst') or date_group.get('gameDate') or
           game.get('gameDate') or '')
    raw = str(raw).strip()[:10]
    try:
        return datetime.strptime(raw, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError(f'Unrecognized NBA schedule game date: {raw!r}') from exc


def extract(payload):
    schedule = payload.get('leagueSchedule') if isinstance(payload, dict) else None
    groups = schedule.get('gameDates') if isinstance(schedule, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValueError('Unexpected NBA schedule schema: missing leagueSchedule.gameDates')
    rows = {}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('Invalid NBA gameDates entry')
        games = group.get('games')
        if not isinstance(games, list):
            raise ValueError('Invalid NBA games list')
        for game in games:
            if not isinstance(game, dict):
                raise ValueError('Invalid NBA game')
            gid = str(game.get('gameId') or '').strip()
            if not gid:
                raise ValueError('NBA game missing gameId')
            row = dict(zip(FIELDS, [
                gid, parse_date(game, group),
                team_name(game.get('homeTeam')), team_name(game.get('awayTeam')),
            ]))
            if not row['home_team'] or not row['away_team']:
                raise ValueError(f'NBA game {gid} missing team identity')
            if gid in rows and rows[gid] != row:
                raise ValueError(f'Conflicting schedule entries for {gid}')
            rows[gid] = row
    if not rows:
        raise ValueError('No NBA games in schedule')
    return sorted(rows.values(), key=lambda r: (r['game_date'], r['nba_game_id']))


def run(url, output, timeout):
    if not url.startswith('https://cdn.nba.com/'):
        raise ValueError('Only the official NBA CDN HTTPS endpoint is allowed')
    request = Request(url, headers={'User-Agent': 'NBA-LOCKBOX-Research/1.0',
                                    'Accept': 'application/json'})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    rows = extract(payload)  # Validate everything before writing any output.
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    print(f'Official NBA schedule games: {len(rows)}')
    print(f'Date range: {rows[0]["game_date"]} through {rows[-1]["game_date"]}')
    print(f'CSV: {output}')
    print('This is schedule identity evidence, NOT scoring verification.')
    print('Historical games outside this feed remain unmatched until separately sourced.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--out', type=Path,
                        default=Path('exports/player_quarters/nba_official_schedule.csv'))
    parser.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args()
    run(args.url, args.out, args.timeout)
