"""NBA LOCKBOX: verified ESPN schedule -> research-only upcoming opponent context.

Run after nba_lockbox_unified_research.py. Never guesses opponent assignments,
player availability, positional defense, or probabilities.
"""
import csv
import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path('research_exports')
ROOT.mkdir(parents=True, exist_ok=True)
BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
DAYS_AHEAD = 7
METRICS = ('points_allowed', 'rebounds_allowed', 'assists_allowed',
           'three_attempts_allowed', 'three_makes_allowed',
           'estimated_defensive_rating')


def load(name):
    with (ROOT / name).open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def write(name, rows, fields):
    with (ROOT / name).open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def fetch(day):
    url = BASE + '?dates=' + day.strftime('%Y%m%d')
    request = urllib.request.Request(url, headers={'User-Agent': 'NBA-LOCKBOX-Research/1.0'})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def schedule(start):
    games, issues = {}, []
    for offset in range(DAYS_AHEAD):
        day = start + timedelta(days=offset)
        payload = fetch(day)
        if not isinstance(payload.get('events'), list):
            raise ValueError('Invalid ESPN scoreboard payload for ' + day.isoformat())
        for event in payload['events']:
            comps = event.get('competitions') or []
            sides = (comps[0].get('competitors') or []) if comps else []
            if len(sides) != 2:
                issues.append({'game_id': str(event.get('id', '')), 'issue': 'not exactly two competitors'})
                continue
            byside = {s.get('homeAway'): s for s in sides}
            if set(byside) != {'home', 'away'}:
                issues.append({'game_id': str(event.get('id', '')), 'issue': 'missing home/away designation'})
                continue
            home, away = byside['home']['team'], byside['away']['team']
            hid, aid = str(home.get('id', '')), str(away.get('id', ''))
            if not hid or not aid or hid == aid:
                issues.append({'game_id': str(event.get('id', '')), 'issue': 'invalid team IDs'})
                continue
            if event.get('status', {}).get('type', {}).get('completed'):
                continue
            gid = str(event.get('id', ''))
            if not gid:
                continue
            games[gid] = {
                'game_id': gid, 'scheduled_utc': str(event.get('date', '')),
                'home_team_id': hid, 'away_team_id': aid,
                'home_team_name': str(home.get('displayName', '')),
                'away_team_name': str(away.get('displayName', '')),
                'event_status': str(event.get('status', {}).get('type', {}).get('name', '')),
                'source': 'ESPN scoreboard',
            }
    return sorted(games.values(), key=lambda x: (x['scheduled_utc'], x['game_id'])), issues


def main():
    players = load('unified_player_research.csv')
    ranks = load('team_matchup_rankings.csv')
    validation = load('team_scoring_validation.csv')
    valid = {(str(r['team_id']), str(r['window'])) for r in validation if r.get('validation') == 'verified'}
    rankmap = defaultdict(dict)
    for r in ranks:
        key = (str(r['team_id']), str(r['window']))
        if key in valid and r.get('metric') in METRICS:
            rankmap[key][r['metric']] = r.get('rank_most_allowed', '')
    today = datetime.now(timezone.utc).date()
    games, issues = schedule(today)
    if not games:
        raise ValueError('No upcoming NBA games returned; refusing to create an unverified matchup report')
    by_team = defaultdict(list)
    for g in games:
        by_team[g['home_team_id']].append((g, g['away_team_id'], g['away_team_name'], 'home'))
        by_team[g['away_team_id']].append((g, g['home_team_id'], g['home_team_name'], 'away'))

    fields = ['game_id', 'scheduled_utc', 'event_status', 'player_id', 'player_name',
              'player_team_id', 'opponent_team_id', 'opponent_team_name', 'home_away',
              'window', 'sample_n', 'player_sample_complete', 'player_last_date',
              'pts_avg', 'reb_avg', 'ast_avg', 'PRA_avg', 'PA_avg', 'PR_avg',
              'fga_avg', 'three_pa_avg', 'fg_pct', 'three_pct', 'ts_pct',
              'streak_signals', 'opponent_defense_verified']
    fields += [m + '_rank_most_allowed' for m in METRICS]
    fields += ['research_only', 'player_availability_verified']
    output = []
    for p in players:
        team = str(p.get('team_id', ''))
        window = str(p.get('window', ''))
        for g, opp, opp_name, side in by_team.get(team, []):
            metric_ranks = rankmap.get((opp, window), {})
            row = {'game_id': g['game_id'], 'scheduled_utc': g['scheduled_utc'],
                   'event_status': g['event_status'], 'player_id': p.get('player_id', ''),
                   'player_name': p.get('player_name', ''), 'player_team_id': team,
                   'opponent_team_id': opp, 'opponent_team_name': opp_name,
                   'home_away': side, 'window': window, 'sample_n': p.get('sample_n', ''),
                   'player_sample_complete': p.get('complete', ''),
                   'player_last_date': p.get('last_date', ''),
                   'opponent_defense_verified': 'yes' if metric_ranks else 'no',
                   'research_only': 'yes', 'player_availability_verified': 'no'}
            for f in ('pts_avg', 'reb_avg', 'ast_avg', 'PRA_avg', 'PA_avg', 'PR_avg',
                      'fga_avg', 'three_pa_avg', 'fg_pct', 'three_pct', 'ts_pct', 'streak_signals'):
                row[f] = p.get(f, '')
            for metric in METRICS:
                row[metric + '_rank_most_allowed'] = metric_ranks.get(metric, '')
            output.append(row)
    write('upcoming_schedule_verified.csv', games,
          ['game_id', 'scheduled_utc', 'home_team_id', 'away_team_id',
           'home_team_name', 'away_team_name', 'event_status', 'source'])
    write('upcoming_matchup_intelligence.csv', output, fields)
    write('upcoming_schedule_audit.csv', issues, ['game_id', 'issue'])
    status = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'source': 'ESPN scoreboard', 'days_checked': DAYS_AHEAD,
              'upcoming_games': len(games), 'player_matchup_rows': len(output),
              'defensive_context_rows': sum(r['opponent_defense_verified'] == 'yes' for r in output),
              'schedule_issues': len(issues),
              'warnings': ['Scheduled opponent is verified, player participation is not.',
                           'Team-level opponent allowances are not position-specific.',
                           'Rankings are descriptive, not projected hit probabilities.',
                           'Schedule may change; rerun before game time.']}
    (ROOT / 'upcoming_matchup_status.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
