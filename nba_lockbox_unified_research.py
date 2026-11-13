"""NBA LOCKBOX: validate defensive totals and join research-only player trends.

Uses existing CSV exports; does not invent opponent assignments, odds, or projections.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('research_exports')
WINDOWS = (3, 5, 8, 12, 20)


def read(name):
    path = ROOT / name
    if not path.is_file():
        raise FileNotFoundError(f'Missing {path}; run upstream exports first')
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def save(name, rows, columns):
    ROOT.mkdir(exist_ok=True, parents=True)
    with (ROOT / name).open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def num(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def main():
    dashboard = read('team_defensive_dashboard.csv')
    games = read('team_opponent_game_logs.csv')
    rankings = read('team_matchup_rankings.csv')
    rolling = read('rolling_form.csv')
    hits = read('prop_hit_rates.csv')
    shooting = read('shooting_efficiency.csv')
    trends = read('shooting_trends.csv')
    streaks = read('shooting_streaks.csv')

    # Independently validate points averages from team/opponent game records.
    game_index = defaultdict(list)
    for g in games:
        game_index[str(g['team_id'])].append(g)
    for values in game_index.values():
        values.sort(key=lambda g: (g['game_date'], g['game_id']), reverse=True)

    audit = []
    verified = set()
    for r in dashboard:
        team, window = str(r['team_id']), str(r['window'])
        n = int(window[1:]) if window.startswith('L') and window[1:].isdigit() else 0
        if n not in WINDOWS:
            continue
        batch = game_index[team][:n]
        if len(batch) != n:
            result = 'insufficient game logs'
        elif any(num(g.get('team_points')) is None or num(g.get('opp_points')) is None for g in batch):
            result = 'missing game points'
        else:
            scored = round(sum(float(g['team_points']) for g in batch) / n, 2)
            conceded = round(sum(float(g['opp_points']) for g in batch) / n, 2)
            source_scored, source_conceded = num(r.get('team_pts_pg')), num(r.get('opp_pts_pg'))
            result = ('verified' if source_scored is not None and source_conceded is not None
                      and abs(scored - source_scored) <= .011
                      and abs(conceded - source_conceded) <= .011 else 'mismatch')
            if result == 'verified':
                verified.add((team, window))
        audit.append({
            'team_id': team, 'window': window, 'sample_n': r.get('sample_n'),
            'scored_avg': r.get('team_pts_pg'), 'allowed_avg': r.get('opp_pts_pg'),
            'equal_scoring': 'yes' if r.get('team_pts_pg') == r.get('opp_pts_pg') else 'no',
            'validation': result,
        })

    # Compact, readable hit-rate summary for all players; no odds or implied win rates.
    hit_index = defaultdict(dict)
    for h in hits:
        key = (str(h['player_id']), str(h['window']))
        market, threshold = h['market'], h['threshold']
        if (market, threshold) in [('PTS', '20'), ('PTS', '25'), ('PTS', '30'),
                                   ('REB', '8'), ('REB', '10'), ('AST', '4'),
                                   ('AST', '6'), ('3PM', '2'), ('3PM', '3'),
                                   ('PRA', '30'), ('PR', '25'), ('PA', '25'),
                                   ('PA', '30'), ('RA', '10')]:
            hit_index[key][f'{market}_{threshold}_hits'] = h['hits']

    shooting_index = {(str(x['player_id']), str(x['window'])): x for x in shooting}
    trend_index = {(str(x['player_id']), str(x.get('window', ''))): x for x in trends}
    streak_index = defaultdict(list)
    for s in streaks:
        streak_index[str(s.get('player_id'))].append(s)

    # Rankings are attached to a team as defensive context, not to an
    # unverified next opponent. Never claim a player is facing their own team.
    defense_index = defaultdict(dict)
    for r in rankings:
        if (str(r['team_id']), str(r['window'])) in verified:
            defense_index[(str(r['team_id']), str(r['window']))][r['metric']] = r['rank_most_allowed']

    player_rows = []
    for r in rolling:
        pid, window = str(r['player_id']), str(r['window'])
        if window not in ('L3', 'L5', 'L8', 'L12', 'L20'):
            continue
        shot = shooting_index.get((pid, window), {})
        item = {
            'player_id': pid, 'player_name': r['player_name'],
            'team_id': r['team_id'], 'window': window,
            'sample_n': r['sample_n'], 'complete': r['complete'],
            'last_date': r['last_date'],
            'pts_avg': r.get('pts_avg', ''), 'reb_avg': r.get('reb_avg', ''),
            'ast_avg': r.get('ast_avg', ''), 'PRA_avg': r.get('PRA_avg', ''),
            'PA_avg': r.get('PA_avg', ''), 'PR_avg': r.get('PR_avg', ''),
            'fga_avg': r.get('fga_avg', ''), 'three_pa_avg': r.get('three_pa_avg', ''),
            'fg_pct': shot.get('fg_pct', ''),
            'three_pct': shot.get('three_pct', shot.get('three_p_pct', '')),
            'ts_pct': shot.get('ts_pct', ''),
            'streak_signals': len(streak_index.get(pid, [])),
            'opponent_id': '', 'opponent_verified': 'no',
        }
        item.update(hit_index.get((pid, window), {}))
        player_rows.append(item)

    audit_fields = ['team_id', 'window', 'sample_n', 'scored_avg',
                    'allowed_avg', 'equal_scoring', 'validation']
    player_fields = list(dict.fromkeys(k for row in player_rows for k in row))
    save('team_scoring_validation.csv', audit, audit_fields)
    save('unified_player_research.csv', player_rows, player_fields)
    status = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'validated_team_windows': len(verified),
        'team_windows_checked': len(audit),
        'equal_scoring_verified': sum(x['equal_scoring'] == 'yes' and x['validation'] == 'verified' for x in audit),
        'incomplete_or_unavailable': sum(x['validation'] == 'insufficient game logs' for x in audit),
        'mismatches': sum(x['validation'] == 'mismatch' for x in audit),
        'player_research_rows': len(player_rows),
        'note': 'No opponent mapping or official defensive ratings inferred. Historical research only.',
    }
    (ROOT / 'unified_research_status.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status, indent=2))
    if any(x['validation'] == 'mismatch' for x in audit):
        raise ValueError('Defensive scoring validation mismatch; inspect team_scoring_validation.csv')


if __name__ == '__main__':
    main()
