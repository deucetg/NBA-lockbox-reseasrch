#!/usr/bin/env python3
"""NBA LOCKBOX: verify ESPN roster positions against research player IDs.

Run at repository root. Does not modify existing research exports.
Output: research_exports/player_positions_verified.csv and
        research_exports/player_position_audit.json

A roster-listed position is NOT proof of a player's in-game matchup/role.
"""
import csv
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path('research_exports/player_last20.csv')
OUT = Path('research_exports')
VALID = {'PG', 'SG', 'SF', 'PF', 'C'}
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{}/roster'


def fetch(team_id):
    request = urllib.request.Request(BASE.format(team_id), headers={
        'User-Agent': 'NBA-LOCKBOX-Research/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def roster_athletes(payload):
    """Support both grouped and flat ESPN roster response layouts."""
    for item in payload.get('athletes', []):
        if isinstance(item, dict) and isinstance(item.get('items'), list):
            yield from (x for x in item['items'] if isinstance(x, dict))
        elif isinstance(item, dict) and item.get('id'):
            yield item


def main():
    if not SOURCE.is_file():
        raise SystemExit('Missing ' + str(SOURCE))
    with SOURCE.open(newline='', encoding='utf-8') as f:
        source = list(csv.DictReader(f))
    required = {'player_id', 'player_name', 'team_id', 'game_date'}
    if not source or not required.issubset(source[0]):
        raise SystemExit('Missing required columns or player records')
    players = {}
    for record in source:
        key = (record['player_id'].strip(), record['team_id'].strip())
        previous = players.get(key)
        if previous is None or record['game_date'] > previous['game_date']:
            players[key] = record
    teams = sorted({t for _, t in players}, key=lambda x: int(x))
    observed = defaultdict(list)
    errors = {}
    for team in teams:
        try:
            payload = fetch(team)
            roster = list(roster_athletes(payload))
            if not roster:
                raise ValueError('No roster athletes found; inspect endpoint schema')
            for athlete in roster:
                athlete_id = str(athlete.get('id', '')).strip()
                if not athlete_id:
                    continue
                pos = athlete.get('position') or {}
                abbreviation = (pos.get('abbreviation', '') if isinstance(pos, dict) else '').upper().strip()
                observed[(athlete_id, team)].append((abbreviation, athlete.get('displayName', '')))
        except Exception as exc:
            errors[team] = str(exc)
    checked = datetime.now(timezone.utc).isoformat()
    output = []
    for (pid, team), record in sorted(players.items(), key=lambda x: (int(x[0][1]), x[0][0])):
        matches = observed.get((pid, team), [])
        positions = sorted({p for p, _ in matches if p in VALID})
        if team in errors:
            status, position = 'source_error', ''
        elif not matches:
            status, position = 'not_on_current_roster', ''
        elif len(positions) != 1 or len(matches) != 1:
            status, position = 'ambiguous_position', ''
        else:
            status, position = 'roster_verified', positions[0]
        output.append({
            'player_id': pid, 'player_name': record['player_name'], 'team_id': team,
            'last_game_date': record['game_date'], 'listed_position': position,
            'status': status, 'source': BASE.format(team), 'checked_at_utc': checked,
            'note': 'Current roster listing; not historical game-day position or defensive assignment',
        })
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / 'player_positions_verified.csv'
    with destination.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    counts = {status: sum(r['status'] == status for r in output) for status in
              ('roster_verified', 'not_on_current_roster', 'ambiguous_position', 'source_error')}
    report = {'checked_at_utc': checked, 'source_rows': len(source),
              'distinct_player_team_pairs': len(output), 'teams_attempted': len(teams),
              'counts': counts, 'team_source_errors': errors,
              'limitations': ['Roster-listed positions may differ from on-court roles',
                              'Current roster cannot validate historical team membership',
                              'No position-based allowances generated until source audit passes']}
    (OUT / 'player_position_audit.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if errors or not counts['roster_verified']:
        sys.exit('Position source incomplete: review audit before calculating defensive splits')


if __name__ == '__main__':
    main()
