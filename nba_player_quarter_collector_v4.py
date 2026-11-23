#!/usr/bin/env python3
"""NBA LOCKBOX V4: official NBA.com player Q1-Q4/OT traditional box-score intake.

This is a fail-closed staging importer. VERIFIED means individual NBA Stats period
lines reconcile to the independent NBA Stats full-game line AND NBA scoreboard
team-period scores. Game IDs are NBA IDs, not ESPN IDs. No odds or tracking
opportunity variables are estimated. Scores/boxscores for non-final games are
excluded. Missing player-quarter lines are not inferred as zeros.

Live (requires functioning stats.nba.com access):
 python nba_player_quarter_collector_v4.py --date 2026-11-13 --out exports/players_v4
Offline fixtures: append --fixture-dir tests/fixtures
"""
import argparse
import csv
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = 'https://stats.nba.com/stats/'
CORE = ['pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta',
        'oreb','dreb','turnovers','steals','blocks','fouls']
MAPPING = {'pts':'points','reb':'reboundsTotal','ast':'assists',
           'fgm':'fieldGoalsMade','fga':'fieldGoalsAttempted',
           'three_pm':'threePointersMade','three_pa':'threePointersAttempted',
           'ftm':'freeThrowsMade','fta':'freeThrowsAttempted',
           'oreb':'reboundsOffensive','dreb':'reboundsDefensive',
           'turnovers':'turnovers','steals':'steals','blocks':'blocks',
           'fouls':'foulsPersonal'}
CSV_FIELDS=['game_date','game_id','player_id','player_name','team','quarter',
            'minutes','pts','reb','ast','fgm','fga','three_pm','three_pa',
            'ftm','fta','turnovers','potential_ast','reb_chances','source_url',
            'verification_status','oreb','dreb','steals','blocks','fouls',
            'game_source','reconciliation']


def make_url(name,params):
    return ROOT+name+'?'+urlencode(params)


def fetch_json(url, fixture=None, retries=2):
    if fixture is not None:
        return json.loads(Path(fixture).read_text(encoding='utf-8'))
    headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36',
             'Referer':'https://www.nba.com/', 'Origin':'https://www.nba.com',
             'x-nba-stats-origin':'stats','x-nba-stats-token':'true',
             'Accept':'application/json'}
    last=None
    for attempt in range(retries):
        try:
            with urlopen(Request(url,headers=headers),timeout=25) as response:
                return json.load(response)
        except (OSError,ValueError) as exc:
            last=exc
            if attempt+1<retries:time.sleep(2*(attempt+1))
    raise RuntimeError('Official NBA Stats request failed: '+str(last))


def decode_minutes(value):
    if isinstance(value,(int,float)) and value>=0:return round(float(value),4)
    if value is None:raise ValueError('minutes missing')
    s=str(value).strip()
    iso=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?',s)
    if iso:
        hh,mm,ss=iso.groups()
        return round(int(hh or 0)*60+int(mm or 0)+float(ss or 0)/60,4)
    if re.fullmatch(r'\d{1,3}:\d{2}(?:\.\d+)?',s):
        mins,secs=s.split(':')
        return round(int(mins)+float(secs)/60,4)
    if re.fullmatch(r'\d+(?:\.\d+)?',s):return round(float(s),4)
    raise ValueError('invalid minute format: '+s)


def parse_num(v,k):
    if isinstance(v,bool) or v is None or str(v).strip()=='':
        raise ValueError('missing '+k)
    f=float(v)
    if not f.is_integer() or f<0:raise ValueError('invalid nonnegative count: '+k)
    return int(f)


def validate(row):
    if row['oreb']+row['dreb']!=row['reb']:
        raise ValueError('rebound components disagree')
    if row['fgm']>row['fga'] or row['three_pm']>row['three_pa'] or row['three_pa']>row['fga'] or row['three_pm']>row['fgm']:
        raise ValueError('shooting component mismatch')
    if row['pts']!=2*row['fgm']+row['three_pm']+row['ftm']:
        raise ValueError('point components disagree')


def extract_players(payload,game_id,quarter=None):
    box=payload.get('boxScoreTraditional') or {}
    if str(box.get('gameId'))!=game_id:
        raise ValueError('NBA boxscore ID differs from requested game')
    by_id={}
    for side in ('homeTeam','awayTeam'):
        team=box.get(side) or {}
        tricode=team.get('teamTricode')
        if not tricode or not team.get('players'):
            raise ValueError('NBA boxscore lacks '+side+' player data')
        for p in team['players']:
            if p.get('personId') is None:raise ValueError('missing player ID')
            st=p.get('statistics') or {}
            # Do not turn DNP into zero production. Full-game DNPs are excluded.
            if quarter is None and (st.get('minutes') is None or decode_minutes(st.get('minutes')) == 0):
                continue
            pid=str(p['personId'])
            if pid in by_id:raise ValueError('duplicate player ID')
            base=dict(player_id=pid,player_name=' '.join(x for x in (p.get('firstName',''),p.get('familyName','')) if x).strip(),
                      team=tricode,minutes=decode_minutes(st.get('minutes')))
            if not base['player_name']:raise ValueError('missing player name')
            for col,source in MAPPING.items():base[col]=parse_num(st.get(source),source)
            validate(base)
            if quarter and base['minutes']> (12.01 if quarter<=4 else 5.01):
                raise ValueError('player minutes exceed period length')
            by_id[pid]=base
    if not by_id:raise ValueError('empty parsed player boxscore')
    return by_id


def get_games(game_date,fixture_dir):
    url=make_url('scoreboardv3',{'GameDate':game_date,'LeagueID':'00'})
    fixture=fixture_dir / ('scoreboard_'+game_date+'.json') if fixture_dir else None
    obj=fetch_json(url,fixture)
    if str((obj.get('scoreboard') or {}).get('gameDate',''))!=game_date:
        raise ValueError('scoreboard date mismatch')
    return (obj['scoreboard'].get('games') or []),url


def get_box(game_id,quarter,fixture_dir):
    periods=(quarter,quarter) if quarter else (0,14)
    params={'GameID':game_id,'StartPeriod':periods[0], 'EndPeriod':periods[1],
            'StartRange':0,'EndRange':0,'RangeType':0}
    url=make_url('boxscoretraditionalv3',params)
    name=f'box_{game_id}_'+(str(quarter) if quarter else 'full')+'.json'
    fixture=fixture_dir/name if fixture_dir else None
    return fetch_json(url,fixture),url


def reconcile_one_game(game,game_date,fixture_dir):
    game_id=str(game.get('gameId',''))
    if not re.fullmatch(r'\d{10}',game_id):raise ValueError('invalid NBA game ID')
    if int(game.get('gameStatus',-1))!=3:raise ValueError('game not final')
    last=int(game.get('period',0))
    if not 4<=last<=10:raise ValueError('unknown regulation/overtime period count')
    teams={}
    for side in ('homeTeam','awayTeam'):
        t=game.get(side) or {}
        period_scores={int(p['period']):int(p['score']) for p in t.get('periods',[]) }
        if not t.get('teamId') or len(period_scores)!=last or set(period_scores)!=set(range(1,last+1)):
            raise ValueError(side+' official period scores unavailable')
        if sum(period_scores.values()) != int(t.get('score',-1)):
            raise ValueError('official period scores differ from final team score')
        teams[str(t['teamId'])]=(period_scores,str(t.get('teamTricode','')))
    if len(teams)!=2:raise ValueError('two valid NBA teams required')
    full_payload,full_url=get_box(game_id,None,fixture_dir)
    full=extract_players(full_payload,game_id)
    if not full:raise ValueError('no full-game players')
    periods={}
    q_urls={}
    for q in range(1,last+1):
        obj,q_url=get_box(game_id,q,fixture_dir)
        q_urls[q]=q_url
        periods[q]=extract_players(obj,game_id,q)
    out=[]
    for pid,f in full.items():
        missing=[q for q in periods if pid not in periods[q]]
        if missing:raise ValueError(f'player {pid} absent in period(s) {missing}; cannot impute zeros')
        for key in ['pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta','oreb','dreb','turnovers','steals','blocks','fouls']:
            if sum(periods[q][pid][key] for q in periods)!=f[key]:
                raise ValueError(f'player {pid}: quarter {key} sum differs from full-game boxscore')
        if abs(sum(periods[q][pid]['minutes'] for q in periods)-f['minutes'])>.16:
            raise ValueError(f'player {pid}: quarter minutes sum differs from full game')
        for q in periods:
            r=dict(periods[q][pid])
            if r['team']!=f['team']:raise ValueError('team changed between period and full box')
            r.update(game_date=game_date.replace('-',''),game_id=game_id,quarter=q,
                potential_ast='',reb_chances='',source_url=q_urls[q],
                verification_status='VERIFIED',game_source=full_url,
                reconciliation='NBA_Q_SUM_EQUALS_FULL_AND_SCOREBOARD')
            out.append(r)
    for q in periods:
        for team_side in ('homeTeam','awayTeam'):
            team=game[team_side]
            tc=str(team.get('teamTricode',''))
            # If teamTricode absent in scoreboard, use NBA ID mapping from V3 box response.
            if not tc:
                payload_team=(full_payload['boxScoreTraditional'][team_side])
                tc=str(payload_team.get('teamTricode',''))
            if not tc:raise ValueError('team tricode missing for score reconciliation')
            official_score=int(next(p['score'] for p in team['periods'] if int(p['period'])==q))
            actual=sum(p['pts'] for p in out if p['quarter']==q and p['team']==tc)
            if actual!=official_score:
                raise ValueError(f'game {game_id} {tc} Q{q}: players scored {actual}, scoreboard {official_score}')
    out.sort(key=lambda p:(p['game_id'],p['player_id'],p['quarter']))
    return out


def run(a):
    date.fromisoformat(a.date)
    fixture=Path(a.fixture_dir) if a.fixture_dir else None
    games,scoreboard_url=get_games(a.date,fixture)
    if a.game_id:
        games=[g for g in games if str(g.get('gameId'))==a.game_id]
        if not games:raise ValueError('requested game ID not found in NBA scoreboard on date')
    if a.max_games:games=games[:a.max_games]
    output=[]; audit=[]
    for i,g in enumerate(games,1):
        gid=str(g.get('gameId',''))
        try:
            rows=reconcile_one_game(g,a.date,fixture)
            output.extend(rows)
            audit.append({'game_id':gid,'status':'VERIFIED','player_quarter_rows':len(rows),'error':''})
        except (ValueError,KeyError,TypeError,RuntimeError) as e:
            audit.append({'game_id':gid,'status':'PROVISIONAL','player_quarter_rows':0,'error':str(e)})
        if not fixture and i<len(games):time.sleep(a.delay)
    dest=Path(a.out);dest.mkdir(parents=True,exist_ok=True)
    with (dest/'NBA_LOCKBOX_Player_Quarter_Verified_v4.csv').open('w',newline='',encoding='utf-8') as fh:
        writer=csv.DictWriter(fh,fieldnames=CSV_FIELDS,extrasaction='ignore');writer.writeheader();writer.writerows(output)
    with (dest/'NBA_LOCKBOX_Player_Quarter_Game_Audit_v4.csv').open('w',newline='',encoding='utf-8') as fh:
        writer=csv.DictWriter(fh,fieldnames=['game_id','status','player_quarter_rows','error']);writer.writeheader();writer.writerows(audit)
    summary=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),game_date=a.date,
        scoreboard_source=scoreboard_url,games_seen=len(games),verified_games=sum(x['status']=='VERIFIED' for x in audit),
        provisional_games=sum(x['status']!='VERIFIED' for x in audit),verified_player_period_rows=len(output),
        data_source='Official NBA.com scoreboardv3 and boxscoretraditionalv3',
        note='Any incomplete game is quarantined. No guessed zero-minute periods, tracking, score-state, or odds.')
    (dest/'collector_audit_v4.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',required=True,help='YYYY-MM-DD NBA game local date')
    parser.add_argument('--game-id',help='Optional exact NBA 10-digit game ID')
    parser.add_argument('--out',default='exports/player_quarters_v4')
    parser.add_argument('--fixture-dir',help='Offline test JSON directory, no network')
    parser.add_argument('--max-games',type=int,default=None)
    parser.add_argument('--delay',type=float,default=0.8)
    print(json.dumps(run(parser.parse_args()),indent=2))
