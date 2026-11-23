#!/usr/bin/env python3
"""NBA LOCKBOX V4 — reconstruct real player-quarter statistics from saved ESPN PBP.

READ-ONLY: reads the existing ESPN SQLite database; writes separate research CSVs.
No NBA.com access, no downloaded new stats, no synthetic quarter minutes.

VERIFIED game: every PBP quarter's points reconcile to its ESPN period-end
scoreboard. VERIFIED player/market: that player's event-derived full-game total
matches the independently saved ESPN full-game box score. For composite
markets, EVERY constituent must reconcile. Unverified markets are omitted from
threshold outputs (never silently treated as zero).

Run:
  python nba_espn_player_quarter_rebuild_v4.py --db data/nba_lockbox.sqlite \\
      --out exports/espn_player_quarters_v4

The outputs are research inputs, not sportsbook odds or probability forecasts.
"""
import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

STATS = ('pts','reb','ast','three_pm','fgm','fga','three_pa','ftm','fta','oreb','dreb','stl','blk','turnovers')
MARKETS = {'PTS': ('pts',), 'REB': ('reb',), 'AST': ('ast',),
           '3PM': ('three_pm',), 'PR': ('pts','reb'),
           'PA': ('pts','ast'), 'RA': ('reb','ast'), 'PRA': ('pts','reb','ast')}
THRESHOLDS = {
  'PTS': (5,8,10,12,15,16,17,18,19,20,22,25,30,35,40),
  'REB': (2,3,4,5,6,7,8,9,10,12,15),
  'AST': (1,2,3,4,5,6,7,8,10,12),
  '3PM': (1,2,3,4,5,6,7),
  'PR': (10,15,18,20,22,25,30,35,40),
  'PA': (10,15,18,20,22,25,30,35,40),
  'RA': (4,5,6,7,8,10,12,15,18),
  'PRA': (15,18,20,22,25,30,35,40,45,50),
}
WINDOWS = (3,5,8,12,20)
VERIFY_STATS = ('pts','reb','ast','three_pm')


def save_csv(path,rows,columns):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=columns,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def seconds_remaining(value):
    if value is None:return None
    try:
        s=str(value)
        if ':' in s:
            m,sec=s.split(':',1)
            return round(float(m)*60+float(sec),2)
        return round(float(s),2)
    except (ValueError,TypeError):return None


def player_ids(play):
    out=[]
    for e in play.get('participants') or []:
        if not isinstance(e,dict):continue
        athlete=e.get('athlete') or {}
        if athlete.get('id') is not None:out.append(str(athlete['id']))
    return out


def parse_events(records):
    """Returns quarter counters and ordered stat increments with source event IDs."""
    by_player=defaultdict(Counter)
    incremental=[]
    period_end={}
    team_scores=defaultdict(Counter)
    ignored=Counter()
    score_consistency=Counter()
    previous=(0,0)
    sequence_numbers=set()
    event_by_period=Counter()
    ordered=[]
    for r in records:
        try:p=json.loads(r['raw_json'])
        except (json.JSONDecodeError,TypeError):
            ignored['invalid_raw_json']+=1;continue
        try:seq=int(p.get('sequenceNumber') or 0)
        except (ValueError,TypeError):seq=0
        ordered.append((seq,str(r['play_id']),p))
    ordered.sort(key=lambda x:(x[0],x[1]))
    for seq,play_id,p in ordered:
        try:quarter=int((p.get('period') or {}).get('number') or 0)
        except (TypeError,ValueError):quarter=0
        if quarter<1: ignored['invalid_period']+=1;continue
        if seq in sequence_numbers:ignored['duplicate_sequence']+=1
        sequence_numbers.add(seq)
        event_by_period[quarter]+=1
        title=str((p.get('type') or {}).get('text') or '').lower().replace('\n',' ')
        text=str(p.get('text') or '').lower().replace('\n',' ')
        ids=player_ids(p)
        attempted=p.get('pointsAttempted')
        scoreval=p.get('scoreValue')
        scoring=p.get('scoringPlay') is True
        scoring_team=str((p.get('team') or {}).get('id') or '')
        if scoring and scoreval in (1,2,3) and scoring_team:
            team_scores[quarter][scoring_team]+=scoreval
        if title=='end period':
            h,a=p.get('homeScore'),p.get('awayScore')
            if isinstance(h,int) and isinstance(a,int): period_end[quarter]=(h,a)
        h,a=p.get('homeScore'),p.get('awayScore')
        if isinstance(h,int) and isinstance(a,int):
            delta=(h-previous[0],a-previous[1])
            if delta[0]<0 or delta[1]<0 or delta[0]+delta[1]>3:
                score_consistency['nonmonotonic_or_large_event_score_delta']+=1
            previous=(h,a)
        increments=[]
        def add(pid,field,amount=1):
            by_player[(str(pid),quarter)][field]+=amount
            increments.append((str(pid),field,amount))
        if p.get('shootingPlay') is True and attempted in (2,3):
            if not ids:ignored['shot_without_shooter']+=1
            else:
                add(ids[0],'fga')
                if attempted==3:add(ids[0],'three_pa')
                if scoring and scoreval in (2,3):
                    add(ids[0],'fgm')
                    add(ids[0],'pts',scoreval)
                    if attempted==3 and scoreval==3:add(ids[0],'three_pm')
                    if len(ids)>=2 and ' assist' in text:add(ids[1],'ast')
                elif len(ids)>=2 and ' blocks ' in (' '+text+' '):add(ids[1],'blk')
        elif 'free throw' in title and attempted==1:
            if not ids:ignored['free_throw_without_shooter']+=1
            else:
                add(ids[0],'fta')
                if scoring and scoreval==1:
                    add(ids[0],'ftm');add(ids[0],'pts')
        elif title in ('offensive rebound','defensive rebound'):
            if ids:
                add(ids[0],'reb')
                add(ids[0],'oreb' if title=='offensive rebound' else 'dreb')
            else:ignored['team_rebound_not_attributed']+=1
        elif 'turnover' in title or title in ('traveling','offensive charge'):
            if ids:
                add(ids[0],'turnovers')
                if len(ids)>1 and ' steals' in text:add(ids[1],'stl')
            else:ignored['team_turnover_not_attributed']+=1
        incremental.append({'period':quarter,'sequence':seq,'play_id':play_id,
                           'clock':str((p.get('clock') or {}).get('displayValue') or ''),
                           'seconds_remaining':seconds_remaining((p.get('clock') or {}).get('displayValue')),
                           'increment':increments,'scoreval':scoreval,
                           'home_score':h,'away_score':a,'team_id':scoring_team})
    return by_player,incremental,period_end,team_scores,ignored,score_consistency,event_by_period


def reconcile_period_scores(period_end,team_scores,event_by_period):
    """Check each scoring event against accumulated official PBP end-period score.

    Do not infer team identity from names or arbitrary quarter totals.
    """
    if not all(q in period_end for q in (1,2,3,4)):
        return False,'MISSING_END_PERIOD',{},[]
    expected=set(range(1,max(period_end)+1))
    if set(period_end)!=expected or not expected.issubset(event_by_period):
        return False,'MISSING_PERIOD_SEQUENCE',{},[]
    teamids=set(t for counts in team_scores.values() for t in counts)
    if len(teamids)!=2:return False,'UNRESOLVED_SCORING_TEAM_IDS',{},[]
    ids=sorted(teamids); tests=[]
    for home,away in ((ids[0],ids[1]),(ids[1],ids[0])):
        mismatches=[]
        before_h=before_a=0
        for q in sorted(period_end):
            end_h,end_a=period_end[q]
            if team_scores[q][home]!=end_h-before_h or team_scores[q][away]!=end_a-before_a:
                mismatches.append(q)
            before_h,before_a=end_h,end_a
        tests.append((len(mismatches),mismatches,home,away))
    tests.sort(key=lambda x:x[0]);best=tests[0]
    if best[0]:return False,'PERIOD_SCORING_MISMATCH',{},best[1]
    if len(tests)>1 and tests[1][0]==0:return False,'AMBIGUOUS_HOME_AWAY_MAPPING',{},[]
    return True,'VERIFIED_PERIOD_SCORING',{'home':best[2],'away':best[3]},[]


def market_value(counter,market):
    return sum(counter.get(field,0) for field in MARKETS[market])


def audit_game(game,box_rows,plays):
    counted,events,period_end,scores,skips,score_warn,event_by_period=parse_events(plays)
    valid,reason,sides,wrong_periods=reconcile_period_scores(period_end,scores,event_by_period)
    periods=sorted(period_end) if valid else sorted(event_by_period)
    # All 4 regulation periods and any played overtime must appear.
    if valid and not all(q in periods for q in (1,2,3,4)):
        valid=False;reason='MISSING_REGULATION_PERIOD'
    source=f'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={game["game_id"]}'
    keys={str(p['player_id']):p for p in box_rows}
    p_checks=[]; quarter=[]; games=[];timing=[];state=[]
    present_ids=set(pid for pid,q in counted)
    extra=sorted(present_ids-set(keys))
    for row in box_rows:
        pid=str(row['player_id']);total=Counter()
        for q in periods:total.update(counted.get((pid,q),{}))
        matches={metric:(row[metric] is not None and total.get(metric,0)==row[metric]) for metric in STATS}
        for metric in STATS:
            p_checks.append({'game_date':game['game_date'],'game_id':game['game_id'],
                'player_id':pid,'player_name':row['player_name'],'metric':metric,
                'full_game_boxscore':row[metric] if row[metric] is not None else '',
                'pbp_sum':total.get(metric,0),'equal_to_full_game':int(matches[metric]),
                'period_scoring_verified':int(valid),
                'verification_status':'VERIFIED' if valid and matches[metric] else 'PROVISIONAL',
                'source_url':source})
        for q in periods:
            rec={'game_date':game['game_date'],'game_id':game['game_id'],
                'player_id':pid,'player_name':row['player_name'],'team_id':row['team_id'],
                'quarter':q,'source_url':source,'quarter_minutes':'',
                'period_scoring_verified':int(valid)}
            for metric in VERIFY_STATS:
                rec[metric]=counted.get((pid,q),{}).get(metric,0) if valid and matches[metric] else ''
                rec[metric+'_verified']=int(valid and matches[metric])
            quarter.append(rec)
        for market in MARKETS:
            market_ok=valid and all(matches[m] for m in MARKETS[market])
            source_market={'game_date':game['game_date'],'game_id':game['game_id'],
                'player_id':pid,'player_name':row['player_name'],'team_id':row['team_id'],
                'market':market,'verification_status':'VERIFIED' if market_ok else 'PROVISIONAL',
                'source_url':source}
            if not market_ok:
                games.append({**source_market,'Q1':'','Q2':'','Q3':'','Q4':'','OT':'',
                              'BY_HALF':'','BY_Q3':'','FINAL':'','q4_or_ot_contribution':'',
                              'score_state_status':'NOT_VERIFIED'})
                continue
            qvals={q:market_value(counted.get((pid,q),{}),market) for q in periods}
            box_final=sum(row[metric] for metric in MARKETS[market])
            assert sum(qvals.values())==box_final,(game['game_id'],pid,market)
            h=sum(qvals.get(q,0) for q in (1,2));q3=h+qvals.get(3,0)
            games.append({**source_market,'Q1':qvals.get(1,0),'Q2':qvals.get(2,0),
                'Q3':qvals.get(3,0),'Q4':qvals.get(4,0),'OT':sum(v for q,v in qvals.items() if q>=5),
                'BY_HALF':h,'BY_Q3':q3,'FINAL':box_final,
                'q4_or_ot_contribution':box_final-q3,
                'score_state_status':'PENDING_SCORE_EVENT_VALIDATION'})
            seen=0; crossing={}
            for event in events:
                increment=sum(v for ep,field,v in event['increment'] if ep==pid and field in MARKETS[market])
                if increment:
                    before=seen;seen+=increment
                    for th in THRESHOLDS[market]:
                        if before<th<=seen:crossing[th]=(event['period'],event['clock'],event['play_id'])
            if seen!=box_final:raise ValueError(f'{game["game_id"]} {pid} {market} event running total mismatch')
            for th in THRESHOLDS[market]:
                hit=box_final>=th
                first=crossing.get(th)
                timing.append({**source_market,'threshold':th,
                    'q1_total':qvals.get(1,0),'half_total':h,'q3_total':q3,'final_total':box_final,
                    'hit_by_q1':int(qvals.get(1,0)>=th),'hit_by_half':int(h>=th),
                    'hit_by_q3':int(q3>=th),'hit_final':int(hit),'q4_or_ot_needed':int(hit and q3<th),
                    'first_hit_period':first[0] if first else '',
                    'first_hit_clock':first[1] if first else '',
                    'first_hit_play_id':first[2] if first else '',
                    'source_url':source})
    out={'game_date':game['game_date'],'game_id':game['game_id'],
        'home_team':game['home_team'],'away_team':game['away_team'],
        'periods':len(periods),'play_events':len(plays),'boxscore_players':len(box_rows),
        'period_scoring_status':reason,'verified_period_scoring':int(valid),
        'unreconciled_periods':';'.join(map(str,wrong_periods)),
        'unmapped_pbp_player_ids':len(extra),'score_sequence_anomalies':sum(score_warn.values()),
        'team_rebounds_ignored':skips['team_rebound_not_attributed'],
        'team_turnovers_ignored':skips['team_turnover_not_attributed'],
        'source_url':source}
    return out,p_checks,quarter,games,timing


def rolling_metrics(per_game,box_log):
    """Denominator is the actual N most recent box-score games, not only clean matches."""
    bykey={(r['game_id'],r['player_id'],r['market']):r for r in per_game}
    out=[]
    for pid,all_games in box_log.items():
        all_games=sorted(all_games,key=lambda r:(r['game_date'],r['game_id']),reverse=True)
        for w in WINDOWS:
            sampled=all_games[:w]
            if not sampled:continue
            for market in MARKETS:
                values=[bykey.get((row['game_id'],pid,market)) for row in sampled]
                available=[x for x in values if x and x['verification_status']=='VERIFIED']
                valid_n=len(available);complete=len(sampled)==w and valid_n==w
                for th in THRESHOLDS[market]:
                    byhalf=sum(r['BY_HALF']>=th for r in available)
                    byq3=sum(r['BY_Q3']>=th for r in available)
                    final=sum(r['FINAL']>=th for r in available)
                    out.append({'player_id':pid,'player_name':sampled[0]['player_name'],
                        'team_id':sampled[0]['team_id'],'market':market,'threshold':th,
                        'window':f'L{w}','window_boxscore_games':len(sampled),
                        'verified_games':valid_n,'complete_window':int(complete),
                        'as_of_game_date':sampled[0]['game_date'],
                        'hits_by_half':byhalf if complete else '',
                        'hits_by_q3':byq3 if complete else '',
                        'hits_final':final if complete else '',
                        'by_q3_hit_rate_pct':round(100*byq3/w,1) if complete else '',
                        'full_game_hit_rate_pct':round(100*final/w,1) if complete else '',
                        'hits_requiring_q4_or_ot':final-byq3 if complete else '',
                        'avg_by_q3':round(sum(x['BY_Q3'] for x in available)/w,2) if complete else '',
                        'avg_final':round(sum(x['FINAL'] for x in available)/w,2) if complete else '',
                        'status':'VERIFIED_COMPLETE_WINDOW' if complete else 'INCOMPLETE_DO_NOT_RANK'})
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',type=Path,default=Path('data/nba_lockbox.sqlite'))
    ap.add_argument('--out',type=Path,default=Path('exports/espn_player_quarters_v4'))
    ap.add_argument('--start-date',default='20261001')
    ap.add_argument('--end-date',default='20270630')
    ap.add_argument('--max-games',type=int,default=0,help='Only for smoke tests; 0 means all games')
    a=ap.parse_args()
    if not a.db.exists():raise FileNotFoundError(a.db)
    db=sqlite3.connect(f'file:{a.db.resolve()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    game_rows=db.execute('SELECT game_id,game_date,home_team,away_team FROM games '
                         'WHERE game_date BETWEEN ? AND ? ORDER BY game_date,game_id',
                         (a.start_date,a.end_date)).fetchall()
    if a.max_games:game_rows=game_rows[:a.max_games]
    if not game_rows:raise ValueError('No completed games for target dates')
    game_audits=[]; stat_audits=[];q_rows=[];game_markets=[];timing=[]
    box_log=defaultdict(list)
    for index,game in enumerate(game_rows,1):
        g=dict(game)
        box=[dict(row) for row in db.execute('SELECT * FROM player_boxscores WHERE game_id=?',(g['game_id'],))]
        plays=db.execute('SELECT play_id,raw_json FROM pbp_events WHERE game_id=?',(g['game_id'],)).fetchall()
        if not box or not plays:
            game_audits.append({'game_date':g['game_date'],'game_id':g['game_id'],
                                'period_scoring_status':'MISSING_SOURCE_ROWS','verified_period_scoring':0})
            continue
        for b in box:
            box_log[str(b['player_id'])].append({'game_id':g['game_id'],'game_date':g['game_date'],
                'player_name':b['player_name'],'team_id':b['team_id']})
        result,checks,quarters,markets,timings=audit_game(g,box,plays)
        game_audits.append(result);stat_audits.extend(checks)
        q_rows.extend(quarters);game_markets.extend(markets);timing.extend(timings)
        if index%50==0:print(f'Processed {index}/{len(game_rows)} games',flush=True)
    rolling=rolling_metrics(game_markets,box_log)
    dest=a.out;dest.mkdir(parents=True,exist_ok=True)
    outputs=[
      ('game_period_scoring_audit.csv',game_audits,list(game_audits[0])),
      ('player_metric_boxscore_reconciliation.csv',stat_audits,list(stat_audits[0])),
      ('player_quarter_core_market_checked.csv',q_rows,list(q_rows[0])),
      ('player_game_quarter_market_checked.csv',game_markets,list(game_markets[0])),
      ('player_game_early_hit_timing_verified.csv',timing,list(timing[0])),
      ('player_rolling_early_hit_rates.csv',rolling,list(rolling[0])),
    ]
    for name,rows,columns in outputs:save_csv(dest/name,rows,columns)
    valid_games=sum(x.get('verified_period_scoring')==1 for x in game_audits)
    verify_counter=Counter((r['metric'],r['verification_status']) for r in stat_audits)
    market_counter=Counter((r['market'],r['verification_status']) for r in game_markets)
    summary={'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_db':str(a.db),'database_game_count':len(game_rows),
        'period_score_verified_games':valid_games,'period_score_provisional_games':len(game_rows)-valid_games,
        'player_boxscore_rows':len(box_log) and sum(len(v) for v in box_log.values()),
        'player_quarter_rows':len(q_rows),'verified_market_game_rows':len([x for x in game_markets if x['verification_status']=='VERIFIED']),
        'verified_threshold_timing_rows':len(timing),
        'rolling_complete_window_rows':sum(x['complete_window']==1 for x in rolling),
        'market_verification':{k:{s:market_counter[(k,s)] for s in ('VERIFIED','PROVISIONAL')} for k in MARKETS},
        'core_reconciliation':{k:{s:verify_counter[(k,s)] for s in ('VERIFIED','PROVISIONAL')} for k in VERIFY_STATS},
        'limitations':['No player-quarter minutes reconstructed; no false zero-minute claim.',
           'Individual stat zeros mean no attributed event in a game with completed period-score and full-game reconciliation.',
           'Player/market mismatches and period-scoring mismatches are excluded from timing statistics.',
           'Score-state on-court usage, potential assists, rebound chances and opponent pregame defensive ranks are not derived.',
           'Hit rates are retrospective descriptive frequencies, not forward probabilities.',
           'NBA LOCKBOX research only. No FanDuel odds.']}
    (dest/'rebuild_audit.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
