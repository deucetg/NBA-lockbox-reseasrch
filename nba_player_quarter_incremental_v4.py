#!/usr/bin/env python3
"""Idempotent V4 player-quarter ingestion; no invented data or ESPN/NBA ID joins.

Runs the independently checked NBA Stats period-box collector on a bounded date
window, retains all previously VERIFIED rows, and quarantines conflicts. An
optional ESPN SQLite crosswalk requires exact date/home/away identity AND both
final scores. No crosswalk is trusted merely because a game date matches.

First probe:
  python nba_player_quarter_incremental_v4.py --start-date 2026-11-22 \
      --end-date 2026-11-22 --max-new-games 1 --out exports/player_quarters_v4

For offline synthetic fixtures, pass --fixture-dir DIRECTORY.
"""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from nba_player_quarter_collector_v4 import CSV_FIELDS, get_games, reconcile_one_game

VERIFIED_NAME = 'NBA_LOCKBOX_Player_Quarter_Verified_v4.csv'
AUDIT_NAME = 'NBA_LOCKBOX_Player_Quarter_Import_Audit_v4.csv'
MAP_NAME = 'NBA_LOCKBOX_ESPN_NBA_ID_Crosswalk_v4.csv'
TRI_TO_ESPN = {
    'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
    'CHA': 'Charlotte Hornets', 'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
    'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets', 'DET': 'Detroit Pistons',
    'GSW': 'Golden State Warriors', 'HOU': 'Houston Rockets', 'IND': 'Indiana Pacers',
    'LAC': 'LA Clippers', 'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
    'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks', 'MIN': 'Minnesota Timberwolves',
    'NOP': 'New Orleans Pelicans', 'NYK': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
    'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers', 'PHX': 'Phoenix Suns',
    'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings',
    'SAS': 'San Antonio Spurs', 'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz',
    'WAS': 'Washington Wizards'
}


def atomic_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', dir=path.parent,
                                     prefix=path.name+'.tmp.', delete=False) as out:
        tmp = Path(out.name)
        try:
            writer = csv.DictWriter(out, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name+'.tmp.', delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump(obj, out, indent=2)
            out.write('\n')
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def require_existing_consistency(rows):
    games = defaultdict(lambda: defaultdict(set))
    seen = set()
    for r in rows:
        if r.get('verification_status') != 'VERIFIED' or not r.get('source_url'):
            raise ValueError('Existing quarter export contains unverified or unproven rows')
        key = (r.get('game_date'), r.get('game_id'), r.get('player_id'), r.get('quarter'))
        if not all(key) or key in seen:
            raise ValueError(f'Invalid or duplicate existing key: {key}')
        seen.add(key)
        games[r['game_id']][r['player_id']].add(int(r['quarter']))
    for gid, players in games.items():
        expected = set().union(*players.values())
        if not {1, 2, 3, 4}.issubset(expected):
            raise ValueError(f'Incomplete existing game: {gid}')
        if any(periods != expected for periods in players.values()):
            raise ValueError(f'Existing game has missing player periods: {gid}')
    return set(games)


def source_espn_games(db_path):
    if not db_path:
        return None
    if not Path(db_path).is_file():
        raise FileNotFoundError(f'ESPN database not found: {db_path}')
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro', uri=True) as db:
        rows = db.execute('''
            SELECT g.game_id, g.game_date, g.home_team, g.away_team,
                   MAX(p.home_score), MAX(p.away_score)
            FROM games AS g
            LEFT JOIN pbp_events AS p ON p.game_id=g.game_id
            GROUP BY g.game_id, g.game_date, g.home_team, g.away_team
        ''').fetchall()
    return [dict(espn_game_id=str(g), date=str(d), home=h, away=a,
                 home_score=hs, away_score=aws) for g, d, h, a, hs, aws in rows]


def crosswalk(game, game_date, espn_games):
    gid = str(game.get('gameId',''))
    home = game.get('homeTeam') or {}
    away = game.get('awayTeam') or {}
    entry = dict(nba_game_id=gid, game_date=game_date.replace('-', ''),
                 espn_game_id='', home_team=home.get('teamTricode',''),
                 away_team=away.get('teamTricode',''),
                 nba_home_score=home.get('score',''), nba_away_score=away.get('score',''),
                 espn_home_score='', espn_away_score='',
                 status='NOT_PROVIDED' if espn_games is None else 'UNMATCHED')
    if espn_games is None:
        return entry
    expected_home = TRI_TO_ESPN.get(entry['home_team'])
    expected_away = TRI_TO_ESPN.get(entry['away_team'])
    if not expected_home or not expected_away:
        entry['status'] = 'UNSUPPORTED_TEAM_IDENTITY'
        return entry
    matches = [g for g in espn_games if g['date'] == entry['game_date']
               and g['home'] == expected_home and g['away'] == expected_away]
    if len(matches) != 1:
        entry['status'] = 'AMBIGUOUS' if len(matches) > 1 else 'UNMATCHED'
        return entry
    r = matches[0]
    entry.update(espn_game_id=r['espn_game_id'], espn_home_score=r['home_score'],
                 espn_away_score=r['away_score'])
    try:
        if int(r['home_score']) == int(home['score']) and int(r['away_score']) == int(away['score']):
            entry['status'] = 'VERIFIED'
        else:
            entry['status'] = 'SCORE_MISMATCH'
    except (TypeError, ValueError, KeyError):
        entry['status'] = 'MISSING_FINAL_SCORES'
    return entry


def run(a):
    first, last = date.fromisoformat(a.start_date), date.fromisoformat(a.end_date)
    if last < first or (last-first).days > 60:
        raise ValueError('Use a chronological interval of at most 61 dates')
    if first < date(2026, 10, 1) or last > date(2027, 6, 30):
        raise ValueError('Date outside the target 2026-27 season')
    if a.max_new_games is not None and a.max_new_games <= 0:
        raise ValueError('max-new-games must be positive')
    dest = Path(a.out)
    preexisting = read_csv(dest/VERIFIED_NAME)
    verified_ids = require_existing_consistency(preexisting)
    current = list(preexisting)
    audit = []
    mappings = []
    espn = source_espn_games(a.espn_db)
    fixture_dir = Path(a.fixture_dir) if a.fixture_dir else None
    fetched, added_games, added_rows, skipped_verified, blocked = 0, 0, 0, 0, 0
    d = first
    while d <= last:
        day = d.isoformat()
        games, source = get_games(day, fixture_dir)
        for g in games:
            gid = str(g.get('gameId', ''))
            mappings.append(crosswalk(g, day, espn))
            if int(g.get('gameStatus', -1)) != 3:
                audit.append(dict(game_date=day, nba_game_id=gid, status='SKIPPED_NOT_FINAL',
                                  player_quarter_rows=0, error='', source=source))
                continue
            if gid in verified_ids:
                skipped_verified += 1
                audit.append(dict(game_date=day, nba_game_id=gid, status='ALREADY_VERIFIED',
                                  player_quarter_rows=0, error='', source=source))
                continue
            if a.max_new_games and fetched >= a.max_new_games:
                audit.append(dict(game_date=day, nba_game_id=gid, status='DEFERRED_BY_LIMIT',
                                  player_quarter_rows=0, error='', source=source))
                continue
            fetched += 1
            try:
                rows = reconcile_one_game(g, day, fixture_dir)
                if not rows or any(str(r['game_id']) != gid for r in rows):
                    raise ValueError('Reconciled game ID mismatch or no verified rows')
                if any(r['verification_status'] != 'VERIFIED' for r in rows):
                    raise ValueError('Unverified quarter record')
                rowkeys = [(r['game_date'], r['game_id'], r['player_id'], str(r['quarter'])) for r in rows]
                if len(set(rowkeys)) != len(rowkeys):
                    raise ValueError('Duplicate player-period records in newly collected game')
                current.extend(rows)
                verified_ids.add(gid)
                added_games += 1
                added_rows += len(rows)
                audit.append(dict(game_date=day, nba_game_id=gid, status='VERIFIED',
                                  player_quarter_rows=len(rows), error='', source=source))
            except (ValueError, KeyError, TypeError, RuntimeError, OSError) as err:
                blocked += 1
                audit.append(dict(game_date=day, nba_game_id=gid, status='PROVISIONAL',
                                  player_quarter_rows=0, error=str(err), source=source))
            if not fixture_dir and a.delay and (a.max_new_games is None or fetched < a.max_new_games):
                time.sleep(a.delay)
        d += timedelta(days=1)
    require_existing_consistency(current)
    current.sort(key=lambda r:(r['game_date'],r['game_id'],r['player_id'],int(r['quarter'])))
    atomic_csv(dest/VERIFIED_NAME, CSV_FIELDS, current)
    atomic_csv(dest/AUDIT_NAME,
               ['game_date','nba_game_id','status','player_quarter_rows','error','source'],audit)
    atomic_csv(dest/MAP_NAME,
               ['nba_game_id','game_date','espn_game_id','home_team','away_team',
                'nba_home_score','nba_away_score','espn_home_score','espn_away_score','status'],mappings)
    summary = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),
                   season='2026-27', range_start=a.start_date, range_end=a.end_date,
                   already_verified_games=skipped_verified, newly_verified_games=added_games,
                   newly_verified_player_period_rows=added_rows, newly_provisional_games=blocked,
                   preserved_prior_rows=len(preexisting), total_verified_player_period_rows=len(current),
                   total_verified_nba_games=len(verified_ids),
                   matched_espn_nba_games=sum(m['status']=='VERIFIED' for m in mappings),
                   crosswalk_status=Counter(m['status'] for m in mappings),
                   warning='NBA and ESPN game IDs must only be joined using VERIFIED crosswalk rows. '
                           'No score-state tracking or defensive ratings are inferred.')
    atomic_json(dest/'NBA_LOCKBOX_Quarter_Incremental_Status_v4.json',summary)
    return summary


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--start-date',required=True)
    p.add_argument('--end-date',required=True)
    p.add_argument('--out',default='exports/player_quarters_v4')
    p.add_argument('--espn-db', help='Optional existing SQLite for verified ESPN-NBA game mapping')
    p.add_argument('--fixture-dir',help='Offline synthetic test directory')
    p.add_argument('--max-new-games',type=int,default=1,
                   help='Limit newly fetched games while validating live NBA Stats; use larger when validated')
    p.add_argument('--delay',type=float,default=0.8)
    print(json.dumps(run(p.parse_args()),indent=2))
