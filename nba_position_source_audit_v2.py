#!/usr/bin/env python3
"""NBA LOCKBOX roster-position source audit v2.

Run at repository root. Outputs separate v2 files; does not overwrite v1.
Broad ESPN G/F labels are NEVER converted into PG/SG/SF/PF guesses.
"""
import csv
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path('research_exports/player_last20.csv')
OUT = Path('research_exports')
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{}/roster'
EXACT = {'PG', 'SG', 'SF', 'PF', 'C'}
BROAD = {'G': 'G', 'F': 'F', 'G/F': 'G/F', 'F/G': 'G/F',
         'F/C': 'F/C', 'C/F': 'F/C', 'G-F': 'G/F', 'F-G': 'G/F',
         'F-C': 'F/C', 'C-F': 'F/C', 'GUARD': 'G',
         'FORWARD': 'F', 'CENTER': 'C'}


def fetch(team):
    request = urllib.request.Request(BASE.format(team), headers={
        'User-Agent': 'NBA-LOCKBOX-Research/2.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def athletes(payload):
    for item in payload.get('athletes', []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('items'), list):
            for athlete in item['items']:
                if isinstance(athlete, dict):
                    yield athlete
        elif item.get('id'):
            yield item


def position_fields(athlete):
    pos = athlete.get('position') or {}
    if not isinstance(pos, dict):
        pos = {}
    abbreviation = str(pos.get('abbreviation') or '').strip().upper()
    display = str(pos.get('displayName') or pos.get('name') or '').strip()
    return abbreviation, display


def classify(abbr, display):
    for raw in (abbr, display.upper()):
        if raw in EXACT:
            return 'exact_position', raw
    for raw in (abbr, display.upper()):
        if raw in BROAD:
            return 'broad_position', BROAD[raw]
    return 'unknown_position', ''


def main():
    if not SOURCE.is_file():
        sys.exit(f'Missing {SOURCE}')
    with SOURCE.open(newline='', encoding='utf-8') as stream:
        logs = list(csv.DictReader(stream))
    if not logs or not {'player_id', 'player_name', 'team_id', 'game_date'}.issubset(logs[0]):
        sys.exit('Player export missing required columns or records')
    players = {}
    for row in logs:
        key = (row['player_id'].strip(), row['team_id'].strip())
        if key not in players or row['game_date'] > players[key]['game_date']:
            players[key] = row
    teams = sorted({t for _, t in players}, key=int)
    indexed = defaultdict(list)
    errors = {}
    for team in teams:
        try:
            entries = list(athletes(fetch(team)))
            if not entries:
                raise ValueError('No athletes in roster response')
            for athlete in entries:
                pid = str(athlete.get('id') or '').strip()
                if pid:
                    abbr, display = position_fields(athlete)
                    indexed[(pid, team)].append((abbr, display, str(athlete.get('displayName') or '')))
        except Exception as exc:
            errors[team] = f'{type(exc).__name__}: {exc}'
    now = datetime.now(timezone.utc).isoformat()
    output = []
    for (pid, team), log in sorted(players.items(), key=lambda p: (int(p[0][1]), p[0][0])):
        matches = indexed.get((pid, team), [])
        abbr = display = roster_name = position = ''
        if team in errors:
            status = 'source_error'
        elif not matches:
            status = 'not_on_current_roster'
        elif len(matches) != 1:
            status = 'duplicate_roster_entry'
        else:
            abbr, display, roster_name = matches[0]
            status, position = classify(abbr, display)
        output.append({
            'player_id': pid, 'player_name': log['player_name'], 'team_id': team,
            'last_game_date': log['game_date'], 'roster_name': roster_name,
            'raw_position_abbreviation': abbr, 'raw_position_display': display,
            'position_label': position, 'verification_status': status,
            'exact_pg_sg_sf_pf_c': position if status == 'exact_position' else '',
            'source_url': BASE.format(team), 'checked_at_utc': now,
            'limitation': 'Current roster position, not historical or on-court assignment',
        })
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / 'player_positions_audit_v2.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    counts = dict(Counter(row['verification_status'] for row in output))
    raw_counts = dict(Counter(row['raw_position_abbreviation'] or '(empty)' for row in output))
    report = {
        'checked_at_utc': now, 'source_rows': len(logs),
        'distinct_player_team_pairs': len(output), 'teams_attempted': len(teams),
        'counts': counts, 'raw_position_abbreviation_counts': raw_counts,
        'team_source_errors': errors,
        'limitations': [
            'G/F labels are not interchangeable with PG/SG/SF/PF assignments',
            'Current roster cannot verify historical positions or past team membership',
            'No position-specific defensive allowances generated by this audit',
        ],
    }
    (OUT / 'player_position_audit_v2.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if errors:
        sys.exit('Roster source errors occurred; inspect report')


if __name__ == '__main__':
    main()
