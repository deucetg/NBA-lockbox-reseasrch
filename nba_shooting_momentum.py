"""NBA LOCKBOX research-only shooting momentum scanner.

Input: research_exports/player_last20.csv (one row per player-game).
Uses non-overlapping prior-five baselines; no odds or predictive grades.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path('research_exports/player_last20.csv')
OUT = Path('research_exports')
PROFILES = {
    'early_hot_hand': ('three', 3, 12, 3),
    'emerging_volume': ('three', 4, 15, 5),
    'established_shooter': ('three', 6, 20, 5),
    'early_scoring_breakout': ('field', 5, 12, 3),
    'emerging_scorer': ('field', 8, 15, 5),
    'established_scorer': ('field', 12, 20, 5),
}
VOLUME_GROWTH = 0.25
EFFICIENCY_PP = 8.0
MIN_EFFICIENCY_ATTEMPTS = {'three': 10, 'field': 15}


def load(path):
    grouped = defaultdict(list)
    with open(path, newline='', encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            try:
                if not all(row.get(k) for k in ('game_id', 'game_date', 'player_id')):
                    continue
                for k in ('minutes', 'three_pa', 'three_pm', 'fga', 'fgm', 'pts', 'fta'):
                    row[k] = float(row[k])
                if any(row[k] < 0 for k in ('minutes', 'three_pa', 'three_pm', 'fga', 'fgm', 'pts', 'fta')):
                    continue
                if not (row['three_pm'] <= row['three_pa'] <= row['fga'] and row['fgm'] <= row['fga'] and row['three_pm'] <= row['fgm']):
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            grouped[row['player_id']].append(row)
    for pid, games in grouped.items():
        unique = {}
        for g in sorted(games, key=lambda x: (x['game_date'], x['game_id'])):
            unique.setdefault(g['game_id'], g)
        grouped[pid] = list(unique.values())
    return grouped


def stats(games, kind):
    attempts_key, makes_key = ('three_pa', 'three_pm') if kind == 'three' else ('fga', 'fgm')
    attempts = sum(g[attempts_key] for g in games)
    makes = sum(g[makes_key] for g in games)
    return {'attempts': attempts, 'makes': makes, 'apg': attempts / len(games),
            'mpg': sum(g['minutes'] for g in games) / len(games),
            'pct': 100 * makes / attempts if attempts else None,
            'pts_pg': sum(g['pts'] for g in games) / len(games),
            'fta_pg': sum(g['fta'] for g in games) / len(games)}


def signals(games):
    result = []
    for name, (kind, minimum, min_minutes, n) in PROFILES.items():
        if len(games) < n + 5:
            continue
        recent, prior = stats(games[-n:], kind), stats(games[-n-5:-n], kind)
        if recent['apg'] < minimum or recent['mpg'] < min_minutes:
            continue
        growth = recent['apg'] / prior['apg'] - 1 if prior['apg'] else None
        volume = growth is not None and growth >= VOLUME_GROWTH and recent['apg'] - prior['apg'] >= 1
        change = recent['pct'] - prior['pct'] if recent['pct'] is not None and prior['pct'] is not None else None
        efficiency = (change is not None and change >= EFFICIENCY_PP
                      and recent['attempts'] >= MIN_EFFICIENCY_ATTEMPTS[kind]
                      and prior['attempts'] >= MIN_EFFICIENCY_ATTEMPTS[kind])
        if not (volume or efficiency):
            continue
        result.append({'profile': name, 'shot_type': kind, 'recent_games': n,
                       'recent_attempts_pg': round(recent['apg'], 2),
                       'prior5_attempts_pg': round(prior['apg'], 2),
                       'volume_growth_pct': round(growth * 100, 1) if growth is not None else '',
                       'recent_shooting_pct': round(recent['pct'], 1) if recent['pct'] is not None else '',
                       'prior5_shooting_pct': round(prior['pct'], 1) if prior['pct'] is not None else '',
                       'efficiency_change_pp': round(change, 1) if change is not None else '',
                       'recent_mpg': round(recent['mpg'], 1),
                       'recent_pts_pg': round(recent['pts_pg'], 1),
                       'recent_fta_pg': round(recent['fta_pg'], 1),
                       'volume_signal': int(volume), 'efficiency_signal': int(efficiency),
                       'bench_candidate_proxy': int(recent['mpg'] < 25),
                       'recent_attempts_total': int(recent['attempts'])})
    # Low-volume breakout is an independent watchlist: no fixed minimum attempts/G.
    # Requires a substantial absolute and relative attempt jump and >=12 MPG.
    if len(games) >= 8:
        for kind in ('three', 'field'):
            recent, prior = stats(games[-3:], kind), stats(games[-8:-3], kind)
            increase = recent['apg'] - prior['apg']
            if (recent['mpg'] >= 12 and recent['attempts'] >= (6 if kind == 'three' else 12)
                    and increase >= (2 if kind == 'three' else 3)
                    and (prior['apg'] == 0 or recent['apg'] >= prior['apg'] * 1.75)):
                growth = (recent['apg'] / prior['apg'] - 1) * 100 if prior['apg'] else None
                change = (recent['pct'] - prior['pct']) if prior['pct'] is not None else None
                result.append({'profile': 'low_volume_breakout_' + kind, 'shot_type': kind, 'recent_games': 3,
                               'recent_attempts_pg': round(recent['apg'], 2),
                               'prior5_attempts_pg': round(prior['apg'], 2),
                               'volume_growth_pct': round(growth, 1) if growth is not None else '',
                               'recent_shooting_pct': round(recent['pct'], 1) if recent['pct'] is not None else '',
                               'prior5_shooting_pct': round(prior['pct'], 1) if prior['pct'] is not None else '',
                               'efficiency_change_pp': round(change, 1) if change is not None else '',
                               'recent_mpg': round(recent['mpg'], 1),
                               'recent_pts_pg': round(recent['pts_pg'], 1),
                               'recent_fta_pg': round(recent['fta_pg'], 1),
                               'volume_signal': 1, 'efficiency_signal': 0,
                               'bench_candidate_proxy': int(recent['mpg'] < 25),
                               'recent_attempts_total': int(recent['attempts'])})
    return result


def reports(grouped):
    current, history = [], []
    for pid, games in grouped.items():
        if not games:
            continue
        previous = {s['profile'] for s in signals(games[:-1])}
        for s in signals(games):
            current.append({'player_id': pid, 'player_name': games[-1]['player_name'],
                            'team_id': games[-1]['team_id'], 'last_game_date': games[-1]['game_date'],
                            'newly_hot': int(s['profile'] not in previous), **s})
        for i in range(8, len(games)):
            before = {s['profile'] for s in signals(games[:i-1])}
            for s in signals(games[:i]):
                target = games[i]
                history.append({'player_id': pid, 'player_name': target['player_name'],
                                'target_game_id': target['game_id'], 'target_game_date': target['game_date'],
                                'newly_hot': int(s['profile'] not in before), **s,
                                'next_3pm': int(target['three_pm']), 'next_3pa': int(target['three_pa']),
                                'next_fgm': int(target['fgm']), 'next_fga': int(target['fga']),
                                'next_pts': int(target['pts']), 'next_minutes': target['minutes'],
                                'next_2plus_3pm': int(target['three_pm'] >= 2),
                                'next_3plus_3pm': int(target['three_pm'] >= 3),
                                'next_10plus_pts': int(target['pts'] >= 10),
                                'next_15plus_pts': int(target['pts'] >= 15)})
    current.sort(key=lambda r: (-r['newly_hot'], -r['bench_candidate_proxy'], -r['volume_signal'], -r['recent_attempts_pg'], r['player_name']))
    history.sort(key=lambda r: (r['target_game_date'], r['player_id'], r['profile']))
    return current, history


def save(path, rows, columns):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not SOURCE.is_file():
        raise SystemExit(f'Missing input: {SOURCE}')
    OUT.mkdir(parents=True, exist_ok=True)
    grouped = load(SOURCE)
    current, history = reports(grouped)
    common = ['profile', 'shot_type', 'recent_games', 'recent_attempts_pg', 'prior5_attempts_pg',
              'volume_growth_pct', 'recent_shooting_pct', 'prior5_shooting_pct', 'efficiency_change_pp',
              'recent_mpg', 'recent_pts_pg', 'recent_fta_pg', 'volume_signal', 'efficiency_signal',
              'bench_candidate_proxy', 'recent_attempts_total']
    save(OUT / 'shooting_momentum_current.csv', current,
         ['player_id', 'player_name', 'team_id', 'last_game_date', 'newly_hot'] + common)
    save(OUT / 'shooting_momentum_walkforward.csv', history,
         ['player_id', 'player_name', 'target_game_id', 'target_game_date', 'newly_hot'] + common +
         ['next_3pm', 'next_3pa', 'next_fgm', 'next_fga', 'next_pts', 'next_minutes',
          'next_2plus_3pm', 'next_3plus_3pm', 'next_10plus_pts', 'next_15plus_pts'])
    status = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'source': str(SOURCE),
              'players_with_logs': len(grouped), 'current_signals': len(current),
              'historical_evaluations': len(history), 'newly_hot_signals': sum(r['newly_hot'] for r in current),
              'volume_growth_threshold_pct': 25, 'efficiency_increase_threshold_pp': 8,
              'profiles': {k: {'shot_type': v[0], 'min_attempts_pg': v[1], 'min_minutes_pg': v[2],
                               'recent_games': v[3], 'prior_games': 5} for k, v in PROFILES.items()},
              'caveats': ['No predictive grades; evaluate walk-forward outcomes before calibration.',
                          'Bench candidate is a minutes proxy, not verified bench status.',
                          'Newly hot compares adjacent player-game cutoffs, not calendar days.',
                          'Players with fewer than 8 games cannot be evaluated.',
                          'Source retains only recent player games; historical evaluation is limited.',
                          'Use last_game_date to check freshness; signals may overlap.']}
    (OUT / 'shooting_momentum_status.json').write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
