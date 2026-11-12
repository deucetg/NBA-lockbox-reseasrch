"""NBA LOCKBOX: research-only player logs, hit rates and shooting trends."""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB = Path('data/nba_lockbox.sqlite')
OUT = Path('research_exports')
WINDOWS = (3, 5, 8, 12, 20)
THRESHOLDS = {'PTS': range(1, 61), 'REB': range(1, 31),
              'AST': range(1, 21), '3PM': range(1, 11),
              'PRA': range(1, 81), 'PR': range(1, 71),
              'PA': range(1, 71), 'RA': range(1, 41)}
MARKETS = {'PTS': 'pts', 'REB': 'reb', 'AST': 'ast', '3PM': 'three_pm',
           'PRA': 'PRA', 'PR': 'PR', 'PA': 'PA', 'RA': 'RA'}
COUNT_FIELDS = ('pts', 'reb', 'ast', 'three_pm', 'fga', 'three_pa',
                'fta', 'turnovers', 'PRA', 'PR', 'PA', 'RA')
SHOOTING_FIELDS = ('fgm', 'fga', 'three_pm', 'three_pa', 'ftm', 'fta')


def season(date):
    year, month = int(date[:4]), int(date[4:6])
    start = year if month >= 7 else year - 1
    return f'{start}-{str(start + 1)[-2:]}'


def minutes(value):
    if value is None or str(value).strip() in ('', '--'):
        return None
    try:
        raw = str(value).strip()
        if ':' in raw:
            mm, ss = raw.split(':', 1)
            return int(mm) + int(ss) / 60
        return float(raw)
    except (TypeError, ValueError):
        return None


def write(filename, records, fields):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / filename).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)


def average(values):
    return round(sum(values) / len(values), 2) if values and all(
        value is not None for value in values) else ''


def percentage(makes, attempts):
    if makes is None or attempts is None or attempts <= 0:
        return ''
    return round(100 * makes / attempts, 2)


def shooting_summary(games):
    result = {}
    for field in SHOOTING_FIELDS:
        vals = [game[field] for game in games]
        result[field + '_total'] = sum(vals) if all(
            value is not None for value in vals) else None
        result[field + '_avg'] = average(vals)
    fgm, fga = result['fgm_total'], result['fga_total']
    tpm, tpa = result['three_pm_total'], result['three_pa_total']
    ftm, fta = result['ftm_total'], result['fta_total']
    result['fg_pct'] = percentage(fgm, fga)
    result['three_pct'] = percentage(tpm, tpa)
    result['ft_pct'] = percentage(ftm, fta)
    result['efg_pct'] = percentage(
        fgm + 0.5 * tpm if fgm is not None and tpm is not None else None, fga)
    points = [g['pts'] for g in games]
    result['ts_pct'] = percentage(
        sum(points) if all(v is not None for v in points) else None,
        2 * (fga + 0.44 * fta) if fga is not None and fta is not None else None)
    mins = [g['minutes'] for g in games]
    result['fga_per36'] = round(36 * fga / sum(mins), 2) if (
        fga is not None and all(v is not None for v in mins) and sum(mins) > 0) else ''
    return result


def change(a, b):
    if a == '' or b == '' or a is None or b is None:
        return ''
    return round(a - b, 2)


def streak_label(recent, previous):
    volume = change(recent['fga_avg'], previous['fga_avg'])
    efficiency = change(recent['fg_pct'], previous['fg_pct'])
    if volume == '' or efficiency == '':
        return 'INSUFFICIENT_DATA'
    if volume >= 2 and efficiency >= 3:
        return 'VOLUME_UP_EFFICIENCY_UP'
    if volume >= 2 and efficiency <= -3:
        return 'VOLUME_UP_EFFICIENCY_DOWN'
    if volume <= -2 and efficiency <= -3:
        return 'VOLUME_DOWN_EFFICIENCY_DOWN'
    if volume <= -2 and efficiency >= 3:
        return 'VOLUME_DOWN_EFFICIENCY_UP'
    if efficiency >= 5 and abs(volume) < 2:
        return 'HOT_SHOOTING_STABLE_VOLUME'
    if efficiency <= -5 and abs(volume) < 2:
        return 'COLD_SHOOTING_STABLE_VOLUME'
    return 'NO_STRONG_SIGNAL'


def main():
    if not DB.exists():
        raise FileNotFoundError(f'Missing research database: {DB}')
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(player_boxscores)')}
        required = {'game_id', 'player_id', 'player_name', 'team_id', 'minutes',
                    'pts', 'reb', 'ast', 'three_pm', 'fga', 'three_pa',
                    'fta', 'turnovers'}
        missing = required - columns
        if missing:
            raise ValueError(f'Missing required player_boxscores columns: {sorted(missing)}')
        optional = ['fgm', 'ftm']
        expressions = [f'b.{field}' if field in columns else f'NULL AS {field}'
                       for field in optional]
        rows = db.execute(f'''
            SELECT b.game_id, b.player_id, b.player_name, b.team_id,
                   g.game_date, b.minutes, b.pts, b.reb, b.ast,
                   b.three_pm, b.fga, b.three_pa, b.fta, b.turnovers,
                   {', '.join(expressions)}
            FROM player_boxscores b JOIN games g ON g.game_id = b.game_id
            ORDER BY g.game_date DESC, b.game_id DESC
        ''').fetchall()
        game_count = db.execute('SELECT COUNT(*) FROM games').fetchone()[0]
        box_count = db.execute('SELECT COUNT(*) FROM player_boxscores').fetchone()[0]

    dates = [str(r[4]) for r in rows if len(str(r[4])) == 8 and str(r[4]).isdigit()]
    if not dates:
        raise ValueError('No valid game dates')
    latest = max(dates)
    active_season = season(latest)
    grouped = defaultdict(dict)
    for (gid, pid, name, team, date, mins, pts, reb, ast, pm3,
         fga, pa3, fta, tov, fgm, ftm) in rows:
        date = str(date)
        if len(date) != 8 or not date.isdigit() or season(date) != active_season:
            continue
        m = minutes(mins)
        if m is None or m <= 0 or any(v is None for v in (pts, reb, ast, pm3)):
            continue
        grouped[pid][str(gid)] = dict(
            game_id=gid, player_id=pid, player_name=name, team_id=team,
            game_date=date, minutes=round(m, 2), pts=pts, reb=reb,
            ast=ast, three_pm=pm3, fgm=fgm, fga=fga, three_pa=pa3,
            ftm=ftm, fta=fta, turnovers=tov, PRA=pts + reb + ast,
            PR=pts + reb, PA=pts + ast, RA=reb + ast)

    logs, rolling, hits, shooting, trends, streaks = [], [], [], [], [], []
    base_fields = ['player_id', 'player_name', 'team_id', 'season', 'window',
                   'sample_n', 'complete', 'first_date', 'last_date']
    shoot_fields = [f + suffix for f in SHOOTING_FIELDS for suffix in ('_total', '_avg')]
    shoot_fields += ['fg_pct', 'three_pct', 'ft_pct', 'efg_pct', 'ts_pct', 'fga_per36']
    trend_fields = ['player_id', 'player_name', 'team_id', 'season', 'comparison',
                    'recent_start', 'recent_end', 'previous_start', 'previous_end',
                    'sample_each', 'recent_fga_avg', 'previous_fga_avg',
                    'fga_avg_delta', 'recent_fg_pct', 'previous_fg_pct',
                    'fg_pct_delta_pp', 'recent_three_pa_avg',
                    'previous_three_pa_avg', 'three_pa_avg_delta',
                    'recent_three_pct', 'previous_three_pct',
                    'three_pct_delta_pp', 'recent_efg_pct', 'previous_efg_pct',
                    'efg_pct_delta_pp', 'recent_ts_pct', 'previous_ts_pct',
                    'ts_pct_delta_pp', 'recent_fga_per36', 'previous_fga_per36',
                    'fga_per36_delta', 'signal']

    for pid, bygame in grouped.items():
        games = sorted(bygame.values(), key=lambda g: (
            g['game_date'], str(g['game_id'])), reverse=True)
        logs.extend(games[:20])
        for n in WINDOWS:
            batch = games[:n]
            if not batch:
                continue
            base = dict(player_id=pid, player_name=games[0]['player_name'],
                        team_id=games[0]['team_id'], season=active_season,
                        window=f'L{n}', sample_n=len(batch),
                        complete='yes' if len(batch) == n else 'no',
                        first_date=batch[-1]['game_date'],
                        last_date=batch[0]['game_date'])
            summary = dict(base)
            for field in ('minutes',) + COUNT_FIELDS:
                vals = [g[field] for g in batch]
                summary[field + '_avg'] = average(vals) if len(batch) == n else ''
            rolling.append(summary)
            shooting.append({**base, **shooting_summary(batch)})
            for market, thresholds in THRESHOLDS.items():
                vals = [g[MARKETS[market]] for g in batch]
                if any(v is None for v in vals):
                    continue
                avg = average(vals)
                for threshold in thresholds:
                    count = sum(v >= threshold for v in vals)
                    hits.append({**base, 'market': market,
                                 'threshold': threshold, 'hits': count,
                                 'hit_rate_pct': round(100 * count / len(vals), 1),
                                 'avg_stat': avg})

        for n in (3, 5, 8):
            if len(games) < 2 * n:
                continue
            recent_games, previous_games = games[:n], games[n:2 * n]
            a, b = shooting_summary(recent_games), shooting_summary(previous_games)
            record = dict(player_id=pid, player_name=games[0]['player_name'],
                          team_id=games[0]['team_id'], season=active_season,
                          comparison=f'L{n} vs previous {n}',
                          recent_start=recent_games[-1]['game_date'],
                          recent_end=recent_games[0]['game_date'],
                          previous_start=previous_games[-1]['game_date'],
                          previous_end=previous_games[0]['game_date'],
                          sample_each=n, recent_fga_avg=a['fga_avg'],
                          previous_fga_avg=b['fga_avg'],
                          fga_avg_delta=change(a['fga_avg'], b['fga_avg']),
                          recent_fg_pct=a['fg_pct'], previous_fg_pct=b['fg_pct'],
                          fg_pct_delta_pp=change(a['fg_pct'], b['fg_pct']),
                          recent_three_pa_avg=a['three_pa_avg'],
                          previous_three_pa_avg=b['three_pa_avg'],
                          three_pa_avg_delta=change(a['three_pa_avg'], b['three_pa_avg']),
                          recent_three_pct=a['three_pct'],
                          previous_three_pct=b['three_pct'],
                          three_pct_delta_pp=change(a['three_pct'], b['three_pct']),
                          recent_efg_pct=a['efg_pct'], previous_efg_pct=b['efg_pct'],
                          efg_pct_delta_pp=change(a['efg_pct'], b['efg_pct']),
                          recent_ts_pct=a['ts_pct'], previous_ts_pct=b['ts_pct'],
                          ts_pct_delta_pp=change(a['ts_pct'], b['ts_pct']),
                          recent_fga_per36=a['fga_per36'],
                          previous_fga_per36=b['fga_per36'],
                          fga_per36_delta=change(a['fga_per36'], b['fga_per36']),
                          signal=streak_label(a, b))
            trends.append(record)
            # Require meaningful shot volume before treating a signal as actionable.
            if (record['signal'] != 'NO_STRONG_SIGNAL'
                    and record['signal'] != 'INSUFFICIENT_DATA'
                    and a['fga_total'] is not None and b['fga_total'] is not None
                    and a['fga_total'] >= 5 * n and b['fga_total'] >= 5 * n):
                streaks.append(record)

    if not logs:
        raise ValueError('No eligible player game logs')
    write('player_last20.csv', logs, list(logs[0]))
    write('rolling_form.csv', rolling, list(rolling[0]))
    write('prop_hit_rates.csv', hits, list(hits[0]))
    write('shooting_efficiency.csv', shooting, base_fields + shoot_fields)
    write('shooting_trends.csv', trends, trend_fields)
    write('shooting_streaks.csv', streaks, trend_fields)
    status = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),
                  latest_game_date=latest, season=active_season,
                  database_games=game_count, database_boxscore_rows=box_count,
                  eligible_players=len(grouped), exported_player_game_rows=len(logs),
                  hit_rate_rows=len(hits), shooting_rows=len(shooting),
                  shooting_trend_rows=len(trends), shooting_streak_rows=len(streaks),
                  source='ESPN player_boxscores via NBA LOCKBOX SQLite',
                  limitations=('Current database season only; incomplete windows labeled; '
                               'historical hit rates are not probabilities; '
                               'shooting metrics require non-null makes/attempts; '
                               'no NBA.com tracking or sportsbook odds.'))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'research_status.json').write_text(
        json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
