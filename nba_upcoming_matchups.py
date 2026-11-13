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
    build_opportunity_shortlist(output)



def build_opportunity_shortlist(matchups):
    """Rank descriptive research signals; never estimate bet probabilities."""
    hit_rows = load('prop_hit_rates.csv')
    trend_rows = load('shooting_trends.csv')
    hit_lookup = {}
    for h in hit_rows:
        if h.get('complete') == 'yes' and h.get('window') in ('L5', 'L8'):
            hit_lookup[(str(h['player_id']), h['window'], h['market'],
                        str(h['threshold']))] = h
    trends = {}
    for t in trend_rows:
        if t.get('comparison') == 'L3 vs previous 3':
            trends[str(t['player_id'])] = t

    market_specs = {
        'PTS': ('pts_avg', 'points_allowed_rank_most_allowed'),
        'REB': ('reb_avg', 'rebounds_allowed_rank_most_allowed'),
        'AST': ('ast_avg', 'assists_allowed_rank_most_allowed'),
        '3PM': (None, 'three_makes_allowed_rank_most_allowed'),
        'PRA': ('PRA_avg', None),
        'PR': ('PR_avg', None),
        'PA': ('PA_avg', None),
        'RA': (None, None),
    }
    # Prefer complete L5, with L8 as a second independent window.
    grouped = defaultdict(dict)
    for p in matchups:
        if p.get('window') in ('L5', 'L8'):
            grouped[(p['game_id'], p['player_id'])][p['window']] = p
    results = []
    for (game_id, pid), windows in grouped.items():
        base = windows.get('L5') or windows.get('L8')
        if not base or base.get('player_sample_complete') != 'yes':
            continue
        for market, (avg_field, defense_field) in market_specs.items():
            # The best descriptive threshold is near but below the L5 average.
            if market == '3PM':
                average = None
                # Use threshold hit-rate avg_stat, not 3PA as made threes.
                candidate = hit_lookup.get((pid, base['window'], market, '1'))
                if candidate:
                    average = number_or_none(candidate.get('avg_stat'))
            elif market == 'RA':
                r, a = number_or_none(base.get('reb_avg')), number_or_none(base.get('ast_avg'))
                average = r + a if r is not None and a is not None else None
            else:
                average = number_or_none(base.get(avg_field))
            if average is None or average < 1:
                continue
            threshold = max(1, int(average * 0.8))
            evidence = []
            for window in ('L5', 'L8'):
                row = windows.get(window)
                if not row or row.get('player_sample_complete') != 'yes':
                    continue
                h = hit_lookup.get((pid, window, market, str(threshold)))
                if h:
                    evidence.append((window, h))
            if not evidence:
                continue
            # Research score is a sorting heuristic, not a probability.
            hit_strength = sum(float(h['hit_rate_pct']) for _, h in evidence) / len(evidence)
            rank = number_or_none(base.get(defense_field)) if defense_field else None
            matchup_bonus = (max(0, 31 - rank) / 30 * 15) if rank is not None else 0
            t = trends.get(pid, {})
            volume_delta = number_or_none(t.get('fga_avg_delta'))
            efficiency_delta = number_or_none(t.get('fg_pct_delta_pp'))
            volume_bonus = 0
            if market in ('PTS', '3PM', 'PRA', 'PR', 'PA') and volume_delta is not None:
                volume_bonus = max(-5, min(5, volume_delta * 1.5))
            score = round(0.75 * hit_strength + matchup_bonus + volume_bonus, 1)
            flags = []
            if len(evidence) == 1:
                flags.append('L8 hit history unavailable')
            if rank is None:
                flags.append('opponent defensive metric unavailable')
            if base.get('player_availability_verified') != 'yes':
                flags.append('player availability unverified')
            if efficiency_delta is not None and efficiency_delta <= -8:
                flags.append('recent FG% decline')
            results.append({
                'game_id': game_id, 'scheduled_utc': base['scheduled_utc'],
                'player_id': pid, 'player_name': base['player_name'],
                'player_team_id': base['player_team_id'],
                'opponent_team_id': base['opponent_team_id'],
                'opponent_team_name': base['opponent_team_name'],
                'market': market, 'research_threshold': threshold,
                'average_recent': round(average, 2),
                'L5_hits': next((h['hits'] + '/' + h['sample_n'] for w, h in evidence if w == 'L5'), ''),
                'L8_hits': next((h['hits'] + '/' + h['sample_n'] for w, h in evidence if w == 'L8'), ''),
                'opponent_rank_most_allowed': rank if rank is not None else '',
                'fga_delta_L3_vs_previous3': volume_delta if volume_delta is not None else '',
                'fg_pct_delta_pp': efficiency_delta if efficiency_delta is not None else '',
                'research_sort_score': score,
                'flags': '; '.join(flags),
                'note': 'Historical research signal only; not a betting line or probability',
            })
    results.sort(key=lambda r: (r['game_id'], r['market'], -r['research_sort_score'], r['player_name']))
    counts = defaultdict(int)
    for row in results:
        key = (row['game_id'], row['market'])
        counts[key] += 1
        row['rank_in_game_market'] = counts[key]
    fields = ['game_id', 'scheduled_utc', 'player_id', 'player_name',
              'player_team_id', 'opponent_team_id', 'opponent_team_name',
              'market', 'research_threshold', 'average_recent', 'L5_hits',
              'L8_hits', 'opponent_rank_most_allowed',
              'fga_delta_L3_vs_previous3', 'fg_pct_delta_pp',
              'research_sort_score', 'rank_in_game_market', 'flags', 'note']
    write('daily_player_opportunity_shortlist.csv', results, fields)
    (ROOT / 'daily_player_opportunity_status.json').write_text(json.dumps({
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'rows': len(results), 'games': len(set(r['game_id'] for r in results)),
        'markets': sorted(set(r['market'] for r in results)),
        'warning': 'Heuristic ordering only. No odds, injury verification, or hit probability.'
    }, indent=2) + '\n')
    print('Opportunity shortlist rows:', len(results))


def number_or_none(value):
    try:
        return float(value) if value not in (None, '') else None
    except (ValueError, TypeError):
        return None

if __name__ == '__main__':
    main()
