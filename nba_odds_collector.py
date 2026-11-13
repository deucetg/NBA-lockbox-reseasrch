"""NBA LOCKBOX: cautious SportsGameOdds FanDuel NBA odds snapshot collector.

Run from repository root with SPORTSGAMEODDS_API_KEY configured.
Uses ONE request per invocation; no automatic pagination or polling.
The CSV is a best-effort view; raw JSON preserves fields for schema validation.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = 'https://api.sportsgameodds.com/v2/events'
OUT_DIR = Path('odds_snapshots')
CSV_FIELDS = ['captured_at_utc', 'event_id', 'event_name', 'market_id',
              'market_name', 'bookmaker', 'line_type', 'line_index',
              'player', 'selection', 'line', 'odds', 'raw_market_json']


def find_first(obj, keys):
    if not isinstance(obj, dict):
        return ''
    for key in keys:
        val = obj.get(key)
        if val is not None and not isinstance(val, (dict, list)):
            return str(val)
    return ''


def main():
    key = os.getenv('SPORTSGAMEODDS_API_KEY')
    if not key:
        sys.exit('Missing SPORTSGAMEODDS_API_KEY secret')

    limit = int(os.getenv('ODDS_EVENT_LIMIT', '2'))
    if not 1 <= limit <= 10:
        sys.exit('ODDS_EVENT_LIMIT must be 1 through 10')

    params = urllib.parse.urlencode({
        'leagueID': 'NBA', 'oddsAvailable': 'true',
        'bookmakerID': 'fanduel', 'includeAltLines': 'true',
        'limit': str(limit),
    })
    req = urllib.request.Request(API_URL + '?' + params,
                                 headers={'x-api-key': key, 'Accept': 'application/json'})
    # One request, with at most one 429 retry after waiting >= 60 seconds.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                print('Rate limited (429). Waiting 65 seconds before one retry.')
                time.sleep(65)
                continue
            sys.exit(f'SportsGameOdds returned HTTP {exc.code}')
        except (urllib.error.URLError, TimeoutError) as exc:
            sys.exit(f'Network error: {exc}')

    if payload.get('success') is not True:
        sys.exit('API reported an unsuccessful response; no snapshot written')
    events = payload.get('data')
    if not isinstance(events, list):
        sys.exit('Unexpected API response: data is not an event list')

    now = datetime.now(timezone.utc)
    timestamp = now.isoformat(timespec='seconds')
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    folder = OUT_DIR / now.strftime('%Y-%m-%d')
    folder.mkdir(parents=True, exist_ok=True)

    # Preserve the exact returned API data for later normalization and audit.
    raw_file = folder / f'fanduel_nba_{stamp}.json'
    raw_file.write_text(json.dumps({'captured_at_utc': timestamp,
                                    'source': 'SportsGameOdds',
                                    'query': {'leagueID': 'NBA', 'bookmakerID': 'fanduel',
                                              'includeAltLines': True, 'limit': limit},
                                    'events': events}, ensure_ascii=False, indent=2),
                        encoding='utf-8')

    rows = []
    markets_seen = 0
    fd_seen = 0
    alts_seen = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = find_first(event, ('eventID', 'eventId', 'id'))
        event_name = find_first(event, ('name', 'eventName', 'matchup'))
        odds = event.get('odds') or {}
        if not isinstance(odds, dict):
            continue
        for market_id, market in odds.items():
            markets_seen += 1
            if not isinstance(market, dict):
                continue
            by_book = market.get('byBookmaker') or {}
            if not isinstance(by_book, dict):
                continue
            fd = by_book.get('fanduel')
            if not isinstance(fd, dict):
                continue
            fd_seen += 1
            name = find_first(market, ('name', 'marketName', 'statID', 'statEntityID'))
            player = find_first(market, ('playerName', 'player', 'statEntityName'))
            selection = find_first(market, ('side', 'betType', 'outcome'))

            def add_row(offer, line_type, index):
                if not isinstance(offer, dict):
                    return
                rows.append({
                    'captured_at_utc': timestamp, 'event_id': event_id,
                    'event_name': event_name, 'market_id': market_id,
                    'market_name': name, 'bookmaker': 'fanduel',
                    'line_type': line_type, 'line_index': index,
                    'player': player or find_first(offer, ('playerName', 'player')),
                    'selection': selection or find_first(offer, ('side', 'outcome', 'betType')),
                    'line': find_first(offer, ('line', 'point', 'points', 'value')),
                    'odds': find_first(offer, ('odds', 'price', 'americanOdds')),
                    'raw_market_json': json.dumps(offer, ensure_ascii=False, separators=(',', ':')),
                })

            add_row(fd, 'main', 0)
            alt = fd.get('altLines') or []
            if isinstance(alt, list):
                alts_seen += len(alt)
                for i, offer in enumerate(alt, 1):
                    add_row(offer, 'alternate', i)
            elif isinstance(alt, dict):
                alts_seen += len(alt)
                for i, offer in enumerate(alt.values(), 1):
                    add_row(offer, 'alternate', i)

    csv_file = folder / f'fanduel_nba_{stamp}.csv'
    with csv_file.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    status = {'captured_at_utc': timestamp, 'events': len(events),
              'total_markets': markets_seen, 'fanduel_markets': fd_seen,
              'alternate_line_entries': alts_seen, 'csv_rows': len(rows),
              'raw_json': str(raw_file), 'csv': str(csv_file),
              'requests_per_run': 1, 'notes': [
                  'CSV fields are best-effort until API schema is verified.',
                  'Raw JSON is authoritative for this snapshot.',
                  'This does not confirm that every NBA event or alternate market is present.',
              ]}
    status_file = folder / f'fanduel_nba_{stamp}_status.json'
    status_file.write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
