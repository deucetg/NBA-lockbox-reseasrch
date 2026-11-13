"""NBA LOCKBOX: audit and rank defensive matchup environments.

Research only. Reads the existing team_defensive_dashboard.csv.
Rankings describe opponent team box-score allowances, not player projections.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('research_exports')
SOURCE = ROOT / 'team_defensive_dashboard.csv'
WINDOWS = ('L3', 'L5', 'L8', 'L12', 'L20')
METRICS = {
    'points_allowed': ('opp_pts_pg', 'high'),
    'rebounds_allowed': ('opp_reb_pg', 'high'),
    'assists_allowed': ('opp_ast_pg', 'high'),
    'three_attempts_allowed': ('opp_three_pa_pg', 'high'),
    'three_makes_allowed': ('opp_three_pm_pg', 'high'),
    'field_goal_attempts_allowed': ('opp_fga_pg', 'high'),
    'opponent_fg_pct': ('opp_fg_pct', 'high'),
    'opponent_three_pct': ('opp_3p_pct', 'high'),
    'estimated_defensive_rating': ('drtg_est', 'high'),
}


def load():
    if not SOURCE.is_file():
        raise FileNotFoundError(f'Missing {SOURCE}; run defensive dashboard first')
    with SOURCE.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def save(name, rows, fields):
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / name).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    records = load()
    if not records:
        raise ValueError('Defensive dashboard is empty')
    seasons = sorted({r.get('season', '') for r in records})
    if len(seasons) != 1:
        raise ValueError(f'Mixed seasons in dashboard: {seasons}')
    warnings = []
    eligible = []
    for r in records:
        team = r.get('team_id', '')
        window = r.get('window', '')
        sample = int(r.get('sample_n') or 0)
        expected = int(window[1:]) if window in WINDOWS else 0
        if window not in WINDOWS or r.get('complete') != 'yes' or sample != expected:
            warnings.append({'team_id': team, 'window': window,
                             'issue': 'incomplete or unsupported sample window'})
            continue
        missing = [field for field, _ in METRICS.values()
                   if number(r.get(field)) is None]
        if missing:
            warnings.append({'team_id': team, 'window': window,
                             'issue': 'missing metrics: ' + ', '.join(missing)})
            continue
        pts = number(r.get('team_pts_pg'))
        opp = number(r.get('opp_pts_pg'))
        if pts is not None and opp is not None and abs(pts - opp) < 0.005:
            warnings.append({'team_id': team, 'window': window,
                             'issue': 'team and opponent scoring averages identical; audit source'})
        eligible.append(r)

    # If every complete row has identical team/opponent scoring, fail closed.
    equal_rows = sum(abs(number(r['team_pts_pg']) - number(r['opp_pts_pg'])) < .005
                     for r in eligible if number(r.get('team_pts_pg')) is not None)
    systemic_issue = bool(eligible) and equal_rows >= max(10, int(len(eligible) * .8))
    ranks = []
    if not systemic_issue:
        groups = defaultdict(list)
        for r in eligible:
            groups[r['window']].append(r)
        for window in WINDOWS:
            group = groups[window]
            for metric, (field, direction) in METRICS.items():
                ordered = sorted(group, key=lambda r: (
                    -number(r[field]) if direction == 'high' else number(r[field]),
                    r['team_id']))
                for rank, r in enumerate(ordered, 1):
                    ranks.append({
                        'season': r['season'], 'window': window,
                        'team_id': r['team_id'], 'metric': metric,
                        'metric_value': r[field], 'rank_most_allowed': rank,
                        'teams_ranked': len(ordered), 'sample_n': r['sample_n'],
                        'last_date': r['last_date'],
                        'interpretation': 'higher = more allowed (not causal)',
                        'rating_type': ('estimated possessions' if metric ==
                                        'estimated_defensive_rating' else 'boxscore allowance'),
                    })
    else:
        warnings.append({'team_id': 'ALL', 'window': 'ALL',
                         'issue': 'SYSTEMIC scoring equality; rankings withheld pending audit'})

    save('team_matchup_rankings.csv', ranks,
         ['season', 'window', 'team_id', 'metric', 'metric_value',
          'rank_most_allowed', 'teams_ranked', 'sample_n', 'last_date',
          'interpretation', 'rating_type'])
    save('team_matchup_audit.csv', warnings, ['team_id', 'window', 'issue'])
    status = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'season': seasons[0], 'input_rows': len(records),
        'complete_rows': len(eligible), 'identical_scoring_rows': equal_rows,
        'systemic_scoring_issue': systemic_issue,
        'ranking_rows': len(ranks), 'audit_flags': len(warnings),
        'notes': ['Ranks opponent box-score allowances, not player-specific odds.',
                  'Official pace and defensive schemes are not available.',
                  'DRTG is an estimate, not official NBA.com defensive rating.',
                  'No betting recommendations are generated.'],
    }
    (ROOT / 'team_matchup_rankings_status.json').write_text(
        json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status, indent=2))
    if systemic_issue:
        raise ValueError('Source data audit failed: scoring equality across teams')


if __name__ == '__main__':
    main()
