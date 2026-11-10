"""NBA LOCKBOX: independently verify provisional PBP quarter totals against ESPN linescores.

Research only. No player attribution, betting data, or database mutation.
"""
import argparse
import csv
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary'


def official_periods(payload):
    header = payload.get('header', {})
    competitions = header.get('competitions') or []
    if not competitions:
        return None, 'missing_competition'
    comp = competitions[0]
    competitors = comp.get('competitors') or []
    if len(competitors) != 2:
        return None, 'missing_competitors'
    teams = {}
    for competitor in competitors:
        side = competitor.get('homeAway')
        if side not in ('home', 'away'):
            return None, 'missing_home_away'
        lines = competitor.get('linescores') or []
        if not lines:
            return None, 'missing_linescores'
        values = {}
        for i, line in enumerate(lines, start=1):
            period = line.get('period') or i
            try:
                period = int(period)
                points = int(line['displayValue'] if 'displayValue' in line else line['value'])
            except (ValueError, TypeError, KeyError):
                return None, 'invalid_linescore'
            if period in values:
                return None, 'duplicate_linescore_period'
            values[period] = points
        teams[side] = values
    return teams, ''


def fetch_summary(game_id, retries=3):
    url = API + '?' + urlencode({'event': game_id})
    request = Request(url, headers={'User-Agent': 'NBA-LOCKBOX-research/1.0', 'Accept': 'application/json'})
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=25) as response:
                return json.load(response), ''
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                return None, type(exc).__name__
            time.sleep(2 * (attempt + 1))
    return None, 'request_failed'


def run(input_path, output_path, delay):
    with input_path.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError('No quarter rows to verify')
    by_game = {}
    for row in rows:
        by_game.setdefault(row['game_id'], []).append(row)
    results = []
    for index, (game_id, periods) in enumerate(by_game.items(), 1):
        payload, fetch_error = fetch_summary(game_id)
        official, parse_error = official_periods(payload) if payload else (None, fetch_error)
        for row in periods:
            period = int(row['period'])
            official_home = official['home'].get(period) if official else None
            official_away = official['away'].get(period) if official else None
            provisional = row['status'] == 'provisional_period_totals'
            if official is None:
                status = 'official_unavailable'
            elif official_home is None or official_away is None:
                status = 'official_period_missing'
            elif not provisional or not row['home_points'] or not row['away_points']:
                status = 'pbp_unresolved'
            elif (int(row['home_points']), int(row['away_points'])) == (official_home, official_away):
                status = 'verified'
            else:
                status = 'mismatch'
            results.append({
                'game_id': game_id, 'game_date': row['game_date'], 'period': period,
                'pbp_home_points': row['home_points'], 'pbp_away_points': row['away_points'],
                'official_home_points': '' if official_home is None else official_home,
                'official_away_points': '' if official_away is None else official_away,
                'pbp_status': row['status'], 'verification_status': status,
                'error': parse_error, 'pbp_source': 'ESPN PBP', 'verification_source': 'ESPN summary linescores'
            })
        if index % 25 == 0:
            print(f'Checked {index}/{len(by_game)} games', flush=True)
        if index < len(by_game):
            time.sleep(delay)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    counts = {s: sum(r['verification_status'] == s for r in results) for s in
              ('verified', 'mismatch', 'pbp_unresolved', 'official_period_missing', 'official_unavailable')}
    print(f'Verification rows: {len(results)}; results: {counts}')
    if not any(r['verification_status'] in ('verified', 'mismatch') for r in results):
        raise RuntimeError('No official periods could be compared; check ESPN API access and response structure')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path('exports/quarters/quarter_team_summary.csv'))
    parser.add_argument('--out', type=Path, default=Path('exports/quarters/quarter_verification.csv'))
    parser.add_argument('--delay', type=float, default=0.4)
    args = parser.parse_args()
    run(args.input, args.out, args.delay)
