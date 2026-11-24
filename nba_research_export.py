#!/usr/bin/env python3
"""Patch nba_research_export.py in a local NBA LOCKBOX checkout, then test it.
Usage: python fix_lockbox_scope.py [path/to/nba_research_export.py]
Does not change the SQLite database or push to GitHub.
"""
import ast
import sqlite3
import sys
from pathlib import Path

HELPER = '''def research_scope(db):
    """Return the active date scope and separate storage/coverage counts."""
    dates = [str(row[0]) for row in db.execute(
        'SELECT DISTINCT g.game_date FROM games g '
        'JOIN player_boxscores b ON b.game_id = g.game_id'
    ) if len(str(row[0])) == 8 and str(row[0]).isdigit()]
    if not dates:
        raise ValueError('No valid game dates with player box scores')
    latest = max(dates)
    active_season = season(latest)
    start = REGULAR_SEASON_STARTS.get(active_season)
    if start is None or latest < start:
        raise ValueError(f'No configured regular-season scope for {active_season}')
    bounds = (start, latest)
    game_count = db.execute('SELECT COUNT(*) FROM games').fetchone()[0]
    box_count = db.execute('SELECT COUNT(*) FROM player_boxscores').fetchone()[0]
    current_games = db.execute(
        'SELECT COUNT(*) FROM games WHERE game_date BETWEEN ? AND ?', bounds
    ).fetchone()[0]
    current_box_games, current_box_rows = db.execute(
        'SELECT COUNT(DISTINCT b.game_id), COUNT(*) '
        'FROM player_boxscores b JOIN games g ON g.game_id = b.game_id '
        'WHERE g.game_date BETWEEN ? AND ?', bounds
    ).fetchone()
    return dict(latest_game_date=latest, season=active_season, season_start=start,
                database_games_total=game_count,
                database_boxscore_rows_total=box_count,
                current_regular_season_games=current_games,
                current_regular_season_boxscore_games=current_box_games,
                current_regular_season_boxscore_rows=current_box_rows,
                current_regular_season_games_without_boxscores=current_games-current_box_games,
                stored_games_outside_research_scope=game_count-current_games,
                scope_method='Regular-season opening date through latest box-score date; date filter only')
'''

STARTS = "REGULAR_SEASON_STARTS = {'2026-27': '20261020'}"

def patch(c):
    def replace(a,b):
        nonlocal c
        if c.count(a) != 1:
            raise ValueError('Unexpected source or patch already applied: '+a[:80])
        c=c.replace(a,b,1)
    replace('WINDOWS = (3, 5, 8, 12, 20)', 'WINDOWS = (3, 5, 8, 12, 20)\n'+STARTS)
    replace('def minutes(value):', HELPER+'\n\ndef minutes(value):')
    replace("        rows = db.execute(f'''", "        scope = research_scope(db)\n        latest = scope['latest_game_date']\n        active_season = scope['season']\n        start = scope['season_start']\n        rows = db.execute(f'''")
    replace("""            ORDER BY g.game_date DESC, b.game_id DESC
        ''').fetchall()
        game_count = db.execute('SELECT COUNT(*) FROM games').fetchone()[0]
        box_count = db.execute('SELECT COUNT(*) FROM player_boxscores').fetchone()[0]

    dates = [str(r[4]) for r in rows if len(str(r[4])) == 8 and str(r[4]).isdigit()]
    if not dates:
        raise ValueError('No valid game dates')
    latest = max(dates)
    active_season = season(latest)""", """            WHERE g.game_date BETWEEN ? AND ?
            ORDER BY g.game_date DESC, b.game_id DESC
        ''', (start, latest)).fetchall()""")
    replace('if len(date) != 8 or not date.isdigit() or season(date) != active_season:', 'if len(date) != 8 or not date.isdigit() or not start <= date <= latest or season(date) != active_season:')
    replace('                  latest_game_date=latest, season=active_season,\n                  database_games=game_count, database_boxscore_rows=box_count,', """                  **scope,
                  # Legacy fields retain their original whole-database semantics.
                  database_games=scope['database_games_total'],
                  database_boxscore_rows=scope['database_boxscore_rows_total'],
                  eligible_player_game_rows=sum(len(g) for g in grouped.values()),
                  eligible_player_games=len({gid for g in grouped.values() for gid in g}),
                  exported_games=len({g['game_id'] for g in logs}),""")
    replace('Current database season only; incomplete windows labeled; ', 'Date-filtered active regular season; postseason needs explicit phase filtering; incomplete windows labeled; ')
    return c

def test_scope(source):
    tree=ast.parse(source)
    nodes=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in ('season','research_scope')) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REGULAR_SEASON_STARTS' for t in n.targets))]
    ns={}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<scope test>','exec'),ns)
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE games(game_id TEXT PRIMARY KEY, game_date TEXT); CREATE TABLE player_boxscores(game_id TEXT, player_id TEXT);')
        db.executemany('INSERT INTO games VALUES (?,?)',[('old','20251101'),('pre','20261019'),('first','20261020'),('gap','20261120'),('last','20261123'),('future','20261124')])
        db.executemany('INSERT INTO player_boxscores VALUES (?,?)',[('old','p'),('pre','p'),('first','p'),('last','p'),('last','q')])
        s=ns['research_scope'](db)
        expected={'database_games_total':6,'database_boxscore_rows_total':5,'current_regular_season_games':3,'current_regular_season_boxscore_games':2,'current_regular_season_boxscore_rows':3,'current_regular_season_games_without_boxscores':1,'stored_games_outside_research_scope':3,'latest_game_date':'20261123'}
        for k,v in expected.items():
            assert s[k]==v,(k,s[k],v)
    print('PASS: synthetic scope test (old season, preseason, missing box score, future date).')

if __name__=='__main__':
    if sys.argv[1:]==['--self-test']:
        test_scope(STARTS+'\n\ndef season(date):\n    year, month = int(date[:4]), int(date[4:6])\n    start = year if month >= 7 else year - 1\n    return f"{start}-{str(start + 1)[-2:]}"\n\n'+HELPER)
    else:
        path=Path(sys.argv[1] if len(sys.argv)>1 else 'nba_research_export.py')
        original=path.read_text(encoding='utf-8')
        updated=patch(original)
        compile(updated,str(path),'exec')
        test_scope(updated)
        backup=path.with_suffix(path.suffix+'.bak')
        if backup.exists():
            raise FileExistsError(backup)
        backup.write_text(original,encoding='utf-8')
        path.write_text(updated,encoding='utf-8')
        print(f'Patched {path}; backup: {backup}. No database changes.')
