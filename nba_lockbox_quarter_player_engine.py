#!/usr/bin/env python3
"""NBA LOCKBOX — player quarter milestones + lead/trail research (2026–27).

Reads VERIFIED player-quarter boxscore records and, optionally, VERIFIED
on-court score-state segments. Does not create player attribution from team
quarter scores or fabricate player quarter opportunities.

Run:
 python nba_lockbox_quarter_player_engine.py --quarters player_quarters_verified.csv \
   --segments score_state_segments_verified.csv --out exports/quarter_players

Artifacts:
  player_game_quarter_totals.csv     individual Q1-4 and Q3 cumulative totals
  player_game_threshold_timing.csv  for each player/game/market/threshold
  player_quarter_hit_rates.csv      L3,L5,L8,L12,L20 and hit-by-Q3
  player_score_state_splits.csv     lead/trail efficiency and usage proxy
  audit_status.json                completeness and validation

Source files MUST be independently populated and verified from player-level
NBA quarter box scores / trustworthy play-by-play & lineup stints. Missing
quarters are never interpreted as a zero. Overtime is NOT a fifth quarter.
"""
import argparse
import csv
import json
import math
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

MARKET_THRESHOLDS = {
    'PTS': [3, 5, 8, 10, 12, 15, 18, 20, 22, 25, 30],
    'REB': [2, 4, 5, 6, 7, 8, 10, 12],
    'AST': [1, 2, 3, 4, 5, 6, 7, 8, 10],
    '3PM': [1, 2, 3, 4, 5],
    'PR': [10, 15, 20, 25, 30],
    'PA': [10, 15, 20, 25, 30],
    'RA': [4, 6, 8, 10, 12, 15],
    'PRA': [15, 20, 25, 30, 35, 40, 45],
}
WINDOWS = (3, 5, 8, 12, 20)
INPUT_FIELDS = ['game_date','game_id','player_id','player_name','team','quarter',
                'minutes','pts','reb','ast','fgm','fga','three_pm','three_pa',
                'ftm','fta','turnovers','potential_ast','reb_chances','source_url',
                'verification_status']
STATE_FIELDS = ['game_date','game_id','player_id','player_name','team','quarter',
                'score_state','minutes','pts','reb','ast','fgm','fga','three_pm',
                'three_pa','ftm','fta','turnovers','team_fga_on','team_fta_on',
                'team_turnovers_on','source_url','verification_status']
CORE = ('PTS','REB','AST','3PM')


def read_csv(path, required):
    if path is None:
        return []
    with Path(path).open(newline='', encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path}: required columns absent: {sorted(missing)}')
        return list(reader)


def nonneg(x, label):
    if x is None or str(x).strip() == '':
        raise ValueError(f'Missing {label}')
    n = float(x)
    if not math.isfinite(n) or n < 0:
        raise ValueError(f'Invalid {label}: {x}')
    return n


def optional(x):
    if x is None or str(x).strip() == '':
        return None
    return nonneg(x, 'optional value')


def tidy(n):
    return int(n) if n == int(n) else round(n, 4)


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def get_input_quarters(path):
    incoming = read_csv(path, INPUT_FIELDS)
    groups = defaultdict(dict)
    audit = Counter()
    for i, row in enumerate(incoming, start=2):
        if row['verification_status'].strip().upper() != 'VERIFIED':
            audit['unverified_rows_ignored'] += 1
            continue
        if not row['source_url'].strip():
            raise ValueError(f'row {i}: VERIFIED requires a source URL')
        date = row['game_date'].replace('-', '').strip()
        if not (len(date) == 8 and date.isdigit() and '20261001' <= date <= '20270630'):
            raise ValueError(f'row {i}: unexpected 2026–27 game date: {date}')
        if not all(row[k].strip() for k in ('game_id','player_id','player_name','team')):
            raise ValueError(f'row {i}: blank player identity')
        q = int(nonneg(row['quarter'], 'quarter'))
        if q not in range(1, 10):
            raise ValueError(f'row {i}: bad period {q}')
        item = dict(row)
        item['game_date'] = date
        item['quarter'] = q
        for k in ('minutes','pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta','turnovers'):
            item[k] = nonneg(row[k], k)
        for k in ('potential_ast','reb_chances'):
            item[k] = optional(row[k])
        if item['fgm'] > item['fga'] or item['three_pm'] > item['three_pa'] or item['three_pa'] > item['fga'] or item['three_pm'] > item['fgm']:
            raise ValueError(f'row {i}: impossible shooting totals')
        if abs(item['pts'] - (2*item['fgm'] + item['three_pm'] + item['ftm'])) > .01:
            raise ValueError(f'row {i}: scoring does not reconcile with FGM/3PM/FTM')
        if q <= 4 and item['minutes'] > 12.01:
            raise ValueError(f'row {i}: more than 12 minutes in quarter')
        if q >= 5 and item['minutes'] > 5.01:
            raise ValueError(f'row {i}: more than 5 minutes in overtime')
        key = (date, row['game_id'], row['player_id'])
        if q in groups[key]:
            raise ValueError(f'row {i}: duplicate period for {key}')
        groups[key][q] = item
        audit['verified_period_rows'] += 1
    return groups, audit


def derive(periods):
    return {"PTS": periods['pts'], "REB": periods['reb'], "AST": periods['ast'],
            "3PM": periods['three_pm'], "PR": periods['pts']+periods['reb'],
            "PA": periods['pts']+periods['ast'], "RA": periods['reb']+periods['ast'],
            "PRA": periods['pts']+periods['reb']+periods['ast']}


def quarter_analysis(groups):
    games, timelines, index, audit = [], [], defaultdict(list), Counter()
    for (date, game_id, pid), periods in sorted(groups.items()):
        if not all(q in periods for q in (1, 2, 3, 4)):
            audit['player_games_missing_one_or_more_quarters'] += 1
            continue
        audit['complete_player_games'] += 1
        q1 = periods[1]
        data = {'game_date':date,'game_id':game_id, 'player_id':pid,
                'player_name':q1['player_name'],'team':q1['team']}
        values = defaultdict(lambda: defaultdict(float))
        for q in range(1, 5):
            stats = derive(periods[q])
            for m, v in stats.items():
                values[m][q] = v
                data[f'Q{q}_{m}'] = tidy(v)
            data[f'Q{q}_MIN'] = tidy(periods[q]['minutes'])
        # Include real overtime only in FINAL; never mislabel it Q4.
        for q, row in periods.items():
            if q >= 5:
                for m, v in derive(row).items():
                    values[m][5] += v
        data['OT_PLAYED'] = int(any(q >= 5 for q in periods))
        data['MIN_BY_Q3'] = tidy(sum(periods[q]['minutes'] for q in (1,2,3)))
        data['MIN_FINAL'] = tidy(sum(r['minutes'] for r in periods.values()))
        for m in MARKET_THRESHOLDS:
            data[f'{m}_BY_HALF'] = tidy(sum(values[m][q] for q in (1,2)))
            data[f'{m}_BY_Q3'] = tidy(sum(values[m][q] for q in (1,2,3)))
            data[f'{m}_FINAL'] = tidy(sum(values[m][q] for q in (1,2,3,4,5)))
        games.append(data)
        index[pid].append(data)
        for m, thresholds in MARKET_THRESHOLDS.items():
            cumul = 0
            crossed = {}
            for q in sorted([1,2,3,4] + ([5] if values[m][5] else [])):
                cumul += values[m][q]
                crossed[q] = cumul
            for t in thresholds:
                hit = int(data[f'{m}_FINAL'] >= t)
                earliest = next((str(q) if q <= 4 else 'OT'
                                 for q, total in crossed.items() if total >= t), '')
                timelines.append({'game_date':date,'game_id':game_id,'player_id':pid,
                                  'player_name':data['player_name'],'team':data['team'],
                                  'market':m,'threshold':t,'q1':tidy(crossed.get(1,0)),
                                  'halftime':tidy(crossed.get(2,0)),
                                  'by_q3':tidy(crossed.get(3,0)),
                                  'final':tidy(data[f'{m}_FINAL']),
                                  'hit_final':hit,
                                  'hit_by_q1':int(crossed.get(1,0) >= t),
                                  'hit_by_half':int(crossed.get(2,0) >= t),
                                  'hit_by_q3':int(crossed.get(3,0) >= t),
                                  'earliest_period':earliest,
                                  'remaining_after_q3':tidy(max(0,t-crossed.get(3,0))),
                                  'minutes_by_q3':data['MIN_BY_Q3']})
    for arr in index.values():
        arr.sort(key=lambda r:(r['game_date'],r['game_id']),reverse=True)
    # All denominators require complete Q1–Q4 data; incomplete windows flagged.
    ranks = []
    for pid, arr in index.items():
        for w in WINDOWS:
            batch = arr[:w]
            if not batch:
                continue
            for m, thresholds in MARKET_THRESHOLDS.items():
                for t in thresholds:
                    hf=sum(r[f'{m}_BY_HALF'] >= t for r in batch)
                    q3=sum(r[f'{m}_BY_Q3'] >= t for r in batch)
                    fin=sum(r[f'{m}_FINAL'] >= t for r in batch)
                    ranks.append({'player_id':pid,'player_name':arr[0]['player_name'],'team':arr[0]['team'],
                                  'market':m,'threshold':t,'window':f'L{w}',
                                  'sample_n':len(batch),'complete_window':int(len(batch)==w),
                                  'last_game_date':batch[0]['game_date'],
                                  'by_q1_hits':sum(r[f'Q1_{m}'] >= t for r in batch),
                                  'by_halftime_hits':hf,'by_q3_hits':q3,'full_game_hits':fin,
                                  'q4_or_OT_needed_hits':fin-q3,
                                  'q3_hit_rate_all_games_pct':round(100*q3/len(batch),1),
                                  'full_game_hit_rate_pct':round(100*fin/len(batch),1),
                                  'pct_of_hits_already_done_by_q3':round(100*q3/fin,1) if fin else '',
                                  'avg_by_q3':round(sum(r[f'{m}_BY_Q3'] for r in batch)/len(batch),2),
                                  'avg_final':round(sum(r[f'{m}_FINAL'] for r in batch)/len(batch),2),
                                  'median_by_q3':round((sorted(r[f'{m}_BY_Q3'] for r in batch)[(len(batch)-1)//2] + sorted(r[f'{m}_BY_Q3'] for r in batch)[len(batch)//2])/2,2),
                                  'source':'verified player-quarter input'})
    return games, timelines, ranks, audit


def state_analysis(path):
    incoming=read_csv(path,STATE_FIELDS) if path else []
    grouped=defaultdict(list)
    minutes_per_period=defaultdict(float)
    rejected=Counter()
    for i,row in enumerate(incoming,start=2):
        if row['verification_status'].strip().upper()!='VERIFIED':
            rejected['unverified_state_segments_ignored']+=1
            continue
        if not row['source_url'].strip():
            raise ValueError(f'state row {i}: VERIFIED requires source URL')
        state=row['score_state'].strip().upper()
        if state not in ('LEADING','TRAILING','TIED'):
            raise ValueError(f'state row {i}: state must be LEADING, TRAILING or TIED')
        q=int(nonneg(row['quarter'],'quarter'))
        if q not in range(1,10):
            raise ValueError(f'state row {i}: invalid quarter')
        d=row['game_date'].replace('-','').strip()
        if not (len(d)==8 and d.isdigit() and '20261001'<=d<='20270630'):
            raise ValueError(f'state row {i}: invalid date')
        for col in ('game_id','player_id','player_name','team'):
            if not row[col].strip():
                raise ValueError(f'state row {i}: blank {col}')
        item=dict(row)
        item['quarter']=q
        for col in ('minutes','pts','reb','ast','fgm','fga','three_pm','three_pa',
                    'ftm','fta','turnovers','team_fga_on','team_fta_on','team_turnovers_on'):
            item[col]=nonneg(row[col],col)
        if (item['fgm']>item['fga'] or item['three_pm']>item['three_pa'] or
                item['three_pa']>item['fga'] or item['three_pm']>item['fgm']):
            raise ValueError(f'state row {i}: impossible shooting totals')
        if abs(item['pts'] - (2*item['fgm']+item['three_pm']+item['ftm']))>.01:
            raise ValueError(f'state row {i}: points do not reconcile')
        if (item['fga']>item['team_fga_on'] or item['fta']>item['team_fta_on'] or
                item['turnovers']>item['team_turnovers_on']):
            raise ValueError(f'state row {i}: player finished events exceed team on-court totals')
        quarter_key=(d,row['game_id'],row['player_id'],q)
        minutes_per_period[quarter_key]+=item['minutes']
        # State segments should be scored using pre-event differential, not after-event result.
        for bucket in (str(q),'ALL'):
            grouped[(row['player_id'],row['player_name'],row['team'],state,bucket)].append(item)
    for quarter_key, minutes in minutes_per_period.items():
        limit = 12.05 if quarter_key[3] <= 4 else 5.05
        if minutes > limit:
            raise ValueError(f'score-state minutes exceed period limit: {quarter_key}: {minutes}')
    output=[]
    for (pid,name,team,state,bucket),items in grouped.items():
        sums={k:sum(item[k] for item in items) for k in ('minutes','pts','reb','ast','fgm','fga',
            'three_pm','three_pa','ftm','fta','turnovers','team_fga_on',
            'team_fta_on','team_turnovers_on')}
        mins=sums['minutes']; fga=sums['fga']; attempts=sums['fga']+.44*sums['fta']
        usage_numerator=sums['fga']+.44*sums['fta']+sums['turnovers']
        usage_denominator=sums['team_fga_on']+.44*sums['team_fta_on']+sums['team_turnovers_on']
        output.append({'player_id':pid,'player_name':name,'team':team,'score_state':state,
                       'period':bucket,'games':len(set(i['game_id'] for i in items)),
                       'minutes':round(mins,2),'pts':tidy(sums['pts']),
                       'reb':tidy(sums['reb']),'ast':tidy(sums['ast']),
                       'fga':tidy(fga),'fg_pct':round(100*sums['fgm']/fga,1) if fga else '',
                       'three_pa':tidy(sums['three_pa']),
                       'three_pct':round(100*sums['three_pm']/sums['three_pa'],1) if sums['three_pa'] else '',
                       'ts_pct':round(100*sums['pts']/(2*attempts),1) if attempts else '',
                       'pts_per36':round(36*sums['pts']/mins,2) if mins else '',
                       'ast_per36':round(36*sums['ast']/mins,2) if mins else '',
                       'fga_per36':round(36*fga/mins,2) if mins else '',
                       'finish_share_proxy_pct':round(100*usage_numerator/usage_denominator,1) if usage_denominator else '',
                       'sample_min_24':int(mins>=24),
                       'definition':'Proxy: player FGA+.44*FTA+TOV / team on-court FGA+.44*FTA+TOV; NOT official USG%',
                       'source':'verified lineup-aware PBP segments'})
    # Compare like-for-like periods and exclude tiny exposure from rankings.
    by_player = {(r['player_id'], r['team'], r['period'], r['score_state']): r for r in output}
    for r in output:
        opp = 'LEADING' if r['score_state'] == 'TRAILING' else 'TRAILING'
        other = by_player.get((r['player_id'], r['team'], r['period'], opp))
        r['trail_minus_lead_finish_share_pp'] = ''
        r['trail_minus_lead_fga_per36'] = ''
        if (r['score_state'] in ('LEADING', 'TRAILING') and other and
                r['minutes'] >= 24 and other['minutes'] >= 24 and
                r['finish_share_proxy_pct'] != '' and other['finish_share_proxy_pct'] != ''):
            lead = r if r['score_state'] == 'LEADING' else other
            trail = r if r['score_state'] == 'TRAILING' else other
            r['trail_minus_lead_finish_share_pp'] = round(
                trail['finish_share_proxy_pct']-lead['finish_share_proxy_pct'],1)
            r['trail_minus_lead_fga_per36'] = round(
                trail['fga_per36']-lead['fga_per36'],2)
    eligible=defaultdict(list)
    for r in output:
        r['state_period_finish_share_rank']=''
        if r['sample_min_24'] and r['finish_share_proxy_pct'] != '':
            eligible[(r['period'],r['score_state'])].append(r)
    for rs in eligible.values():
        rs.sort(key=lambda r:r['finish_share_proxy_pct'],reverse=True)
        for i,r in enumerate(rs,1):
            r['state_period_finish_share_rank']=i
    output.sort(key=lambda r:(r['player_name'],r['period'],r['score_state']))
    return output,rejected


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--quarters',type=Path,required=True)
    p.add_argument('--segments',type=Path,default=None)
    p.add_argument('--out',type=Path,default=Path('exports/quarter_players'))
    a=p.parse_args()
    groups, input_audit = get_input_quarters(a.quarters)
    games,timings,ranks,calc_audit=quarter_analysis(groups)
    states,states_audit=state_analysis(a.segments)
    out=a.out;out.mkdir(parents=True,exist_ok=True)
    if games:
        write_csv(out/'player_game_quarter_totals.csv',games,list(games[0]))
    if timings:
        write_csv(out/'player_game_threshold_timing.csv',timings,list(timings[0]))
    if ranks:
        write_csv(out/'player_quarter_hit_rates.csv',ranks,list(ranks[0]))
    if states:
        write_csv(out/'player_score_state_splits.csv',states,list(states[0]))
    audit={'generated_at_utc':datetime.now(timezone.utc).isoformat(),
           'season':'2026-27','verified_quarter_input':dict(input_audit),
           'calculation':dict(calc_audit),'score_state':dict(states_audit),
           'player_games_complete':len(games),'game_threshold_rows':len(timings),
           'window_hit_rate_rows':len(ranks),'score_state_split_rows':len(states),
           'quarter_opportunity_metrics':'Potential assists/rebound chances reported only when provided by official quarter sources; not inferred.',
           'score_state_definition':'Score differential BEFORE event; only lineup-aware on-court event segments count. The finish-share proxy is NOT official USG%.',
           'hit_time_definition':'By end of Q1, Q2, Q3, Q4 or OT. Intraperiod crossing needs timestamped play-by-play.',
           'coverage_warning':'No rows means verified player-quarter source is missing. Never treat empty output as zero performance.'}
    (out/'audit_status.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit,indent=2))

if __name__=='__main__':
    main()
