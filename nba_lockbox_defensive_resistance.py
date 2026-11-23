#!/usr/bin/env python3
"""NBA LOCKBOX 2026-27 — defensive resistance of actual quarter player stats.

Requires verified player-quarter stats plus independently verified, PRE-GAME
opponent NBA.com season-to-date defensive ratings/ranks. Does not infer
player periods, game-time usage, or odds. Designed to sit beside
nba_lockbox_quarter_player_engine.py and reuse its validation.

    python nba_lockbox_defensive_resistance.py \
      --quarters NBA_LOCKBOX_Player_Quarter_Input.csv \
      --opponents NBA_LOCKBOX_Opponent_Defense_Input.csv \
      --out exports/defensive_resistance

Optionally supply --segments NBA_LOCKBOX_Score_State_Segments_Input.csv for
leading/trailing usage *proxy* splits by defense tier.
"""
import argparse
import csv
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

from nba_lockbox_quarter_player_engine import (
    MARKET_THRESHOLDS, STATE_FIELDS, get_input_quarters, quarter_analysis,
    read_csv, tidy, nonneg,
)

OPP_FIELDS = [
    'game_date','game_id','player_team','opponent_team','opponent_def_rank_pregame',
    'opponent_def_rtg_pregame','teams_ranked','rank_window','ranking_as_of',
    'source_type','source_url','verification_status',
]


def csv_out(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def datekey(val):
    x = str(val).strip().replace('-','')
    if not (len(x) == 8 and x.isdigit() and '20261001' <= x <= '20270630'):
        raise ValueError('Invalid 2026-27 date: '+str(val))
    return x


def parse_opponents(path):
    rows = read_csv(path, OPP_FIELDS)
    lookup = {}
    skipped = Counter()
    for i,r in enumerate(rows,2):
        if r['verification_status'].strip().upper() != 'VERIFIED':
            skipped['nonverified_opponent_rows'] += 1
            continue
        if r['source_type'].strip().upper() != 'NBA_OFFICIAL':
            skipped['nonofficial_def_ratings'] += 1
            continue
        if r['rank_window'].strip().upper() != 'SEASON_TO_DATE':
            skipped['nonseason_def_ratings'] += 1
            continue
        if not r['source_url'].strip().startswith('https://'):
            raise ValueError(f'Opponent row {i}: verified row needs an official HTTPS source')
        d, asof = datekey(r['game_date']), datekey(r['ranking_as_of'])
        if asof >= d:
            raise ValueError(f'Opponent row {i}: defensive ranking must predate game')
        n, rank = int(nonneg(r['teams_ranked'],'teams_ranked')), int(nonneg(r['opponent_def_rank_pregame'],'def_rank'))
        if n != 30 or not 1 <= rank <= n:
            raise ValueError(f'Opponent row {i}: tier needs a complete 30-team ranking')
        drtg = nonneg(r['opponent_def_rtg_pregame'],'opponent_def_rtg_pregame')
        team = r['player_team'].strip()
        opp = r['opponent_team'].strip()
        if not team or not opp or team == opp or not r['game_id'].strip():
            raise ValueError(f'Opponent row {i}: invalid game/team/opponent mapping')
        tier = 'ELITE_1_5' if rank <= 5 else ('STRONG_6_10' if rank <=10 else ('MIDDLE_11_20' if rank <=20 else 'WEAK_21_30'))
        key = (d,r['game_id'].strip(),team)
        if key in lookup:
            raise ValueError(f'Opponent row {i}: duplicate defense context {key}')
        lookup[key] = dict(game_date=d,game_id=key[1],player_team=team,
                           opponent_team=opp, opponent_def_rank_pregame=rank,
                           opponent_def_rtg_pregame=drtg, ranking_as_of=asof,
                           defense_tier=tier, top10=int(rank<=10),
                           source_type='NBA_OFFICIAL',source_url=r['source_url'].strip())
    return lookup, skipped


def pct(a,b):
    return round(a*100/b,1) if b else ''


def avg(a):
    return round(sum(a)/len(a),2) if a else ''


def verified_player_games(groups, contexts):
    p_games,timings,_,audit = quarter_analysis(groups)
    merged = []
    eligible = {}
    missing=Counter()
    for g in p_games:
        key=(g['game_date'],g['game_id'],g['team'])
        ctx=contexts.get(key)
        if not ctx:
            missing['player_games_missing_pregame_defense']+=1
            continue
        periods=groups[(g['game_date'],g['game_id'],g['player_id'])]
        # Quarter engine already requires all Q1-Q4 present, including verified zero-minute quarters.
        totals = {stat:sum(p[stat] for p in periods.values()) for stat in ('minutes','pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta','turnovers')}
        first3 = {stat:sum(periods[q][stat] for q in (1,2,3)) for stat in totals}
        if totals['fga'] < totals['fgm'] or totals['three_pa'] < totals['three_pm']:
            raise ValueError('Inconsistent individual game totals')
        row=dict(g)
        row.update(ctx)
        row.update({
          'FGA_FINAL':tidy(totals['fga']), 'FG_PCT_FINAL':pct(totals['fgm'],totals['fga']),
          'THREE_PA_FINAL':tidy(totals['three_pa']), 'THREE_PCT_FINAL':pct(totals['three_pm'],totals['three_pa']),
          'PTS_PER36_FINAL':round(36*totals['pts']/totals['minutes'],2) if totals['minutes'] else '',
          'FGA_PER36_FINAL':round(36*totals['fga']/totals['minutes'],2) if totals['minutes'] else '',
          'FGA_BY_Q3':tidy(first3['fga']),
          'FG_PCT_BY_Q3':pct(first3['fgm'],first3['fga']),
          'PTS_PER36_BY_Q3':round(36*first3['pts']/first3['minutes'],2) if first3['minutes'] else '',
          'opportunity_completeness':'VERIFIED' if all(p['potential_ast'] is not None and p['reb_chances'] is not None for p in periods.values()) else 'NOT_PROVIDED',
        })
        if row['opportunity_completeness']=='VERIFIED':
            pot=sum(p['potential_ast'] for p in periods.values())
            chances=sum(p['reb_chances'] for p in periods.values())
            row['POT_AST_FINAL']=round(pot,2); row['REBOUND_CHANCES_FINAL']=round(chances,2)
            row['AST_CONVERSION_FINAL_PCT']=pct(totals['ast'],pot)
            row['REB_CONVERSION_FINAL_PCT']=pct(totals['reb'],chances)
        else:
            for k in ('POT_AST_FINAL','REBOUND_CHANCES_FINAL','AST_CONVERSION_FINAL_PCT','REB_CONVERSION_FINAL_PCT'):
                row[k]=''
        for q in (1,2,3,4):
            p=periods[q]
            row[f'Q{q}_FGA']=tidy(p['fga'])
            row[f'Q{q}_FG_PCT']=pct(p['fgm'],p['fga'])
            row[f'Q{q}_THREE_PA']=tidy(p['three_pa'])
        merged.append(row)
        eligible[(g['game_date'],g['game_id'],g['player_id'])] = row
    joined=[]
    for t in timings:
        g=eligible.get((t['game_date'],t['game_id'],t['player_id']))
        if not g: continue
        joined.append({**t,'opponent_team':g['opponent_team'],
            'opponent_def_rank_pregame':g['opponent_def_rank_pregame'],
            'opponent_def_rtg_pregame':g['opponent_def_rtg_pregame'],
            'defense_tier':g['defense_tier'],'top10':g['top10'],
            'ranking_as_of':g['ranking_as_of'],'source_url':g['source_url'],
            'FGA_FINAL':g['FGA_FINAL'],'FGA_BY_Q3':g['FGA_BY_Q3'],
            'MIN_FINAL':g['MIN_FINAL'], 'FG_PCT_FINAL':g['FG_PCT_FINAL']})
    return merged,joined,missing,audit


def split_labels(tier):
    return [tier,'TOP10' if tier in ('ELITE_1_5','STRONG_6_10') else 'NON_TOP10']


def threshold_summary(rows):
    by=defaultdict(list)
    for r in rows:
        for label in split_labels(r['defense_tier']):
            by[(r['player_id'],r['team'],r['market'],str(r['threshold']),label)].append(r)
    summaries=[]
    for (pid,team,market,threshold,label),group in by.items():
        group.sort(key=lambda x:(x['game_date'],x['game_id']),reverse=True)
        for w in (3,5,8,12,20):
            batch=group[:w]
            n=len(batch)
            if not n: continue
            q1=sum(int(x['hit_by_q1']) for x in batch)
            half=sum(int(x['hit_by_half']) for x in batch)
            q3=sum(int(x['hit_by_q3']) for x in batch)
            full=sum(int(x['hit_final']) for x in batch)
            summaries.append(dict(player_id=pid,player_name=batch[0]['player_name'],player_team=team,
                opponent_tier_group=label,market=market,threshold=threshold,window=f'L{w}',
                sample_n=n,complete_window=int(n==w),
                observation_quality=('SUFFICIENT_5PLUS' if n>=5 else ('PRELIMINARY_3_4' if n>=3 else 'TOO_SMALL')),
                last_date=batch[0]['game_date'],q1_hits=q1,halftime_hits=half,
                by_q3_hits=q3,full_game_hits=full,q4_or_ot_needed=full-q3,
                by_q3_hit_rate_pct=pct(q3,n),full_game_hit_rate_pct=pct(full,n),
                avg_by_q3=avg([float(x['by_q3']) for x in batch]),
                avg_final=avg([float(x['final']) for x in batch]),
                avg_fga_final=avg([float(x['FGA_FINAL']) for x in batch]),
                avg_minutes=avg([float(x['MIN_FINAL']) for x in batch]),
                games_counted=';'.join(x['game_id'] for x in batch)))
    return summaries


def comparisons(rows):
    ix={}
    for r in rows:
        if r['opponent_tier_group'] in ('TOP10','NON_TOP10'):
            ix[(r['player_id'],r['player_team'],r['market'],r['threshold'],r['window'],r['opponent_tier_group'])]=r
    keys=sorted({k[:5] for k in ix})
    out=[]
    for key in keys:
        a=ix.get((*key,'TOP10'))
        b=ix.get((*key,'NON_TOP10'))
        if not a and not b:continue
        na=a['sample_n'] if a else 0; nb=b['sample_n'] if b else 0
        eligible=(na>=3 and nb>=3)
        out.append(dict(player_id=key[0],player_name=(a or b)['player_name'],
            player_team=key[1],market=key[2],threshold=key[3],window=key[4],
            top10_games=na,other_games=nb,
            top10_q3_hits=a['by_q3_hits'] if a else '',
            other_q3_hits=b['by_q3_hits'] if b else '',
            top10_q3_rate_pct=a['by_q3_hit_rate_pct'] if a else '',
            other_q3_rate_pct=b['by_q3_hit_rate_pct'] if b else '',
            top10_full_hit_pct=a['full_game_hit_rate_pct'] if a else '',
            other_full_hit_pct=b['full_game_hit_rate_pct'] if b else '',
            top10_avg_fga=a['avg_fga_final'] if a else '',
            other_avg_fga=b['avg_fga_final'] if b else '',
            top10_avg_minutes=a['avg_minutes'] if a else '',
            other_avg_minutes=b['avg_minutes'] if b else '',
            top10_q3_minus_other_pp=round(float(a['by_q3_hit_rate_pct'])-float(b['by_q3_hit_rate_pct']),1) if eligible else '',
            comparison_status='PRELIMINARY_3PLUS_EACH' if eligible else 'INSUFFICIENT_DEFENSE_TIER_SAMPLE',
            note='Descriptive only, opponent strength alone does not explain changes'))
    return out


def score_states(path, eligible):
    if path is None: return [],Counter()
    rows=read_csv(path,STATE_FIELDS)
    groups=defaultdict(list); audit=Counter()
    for i,r in enumerate(rows,2):
        if r['verification_status'].strip().upper()!='VERIFIED':
            audit['unverified_score_segments']+=1;continue
        if not r['source_url'].strip().startswith('https://'):
            raise ValueError(f'State row {i}: source URL missing')
        date=datekey(r['game_date']);quarter=int(nonneg(r['quarter'],'quarter'))
        state=r['score_state'].strip().upper()
        if state not in ('LEADING','TRAILING','TIED') or quarter not in range(1,10):
            raise ValueError(f'State row {i}: score state/quarter invalid')
        g=eligible.get((date,r['game_id'].strip(),r['player_id'].strip()))
        if not g:
            audit['state_segment_missing_complete_quarter_or_defense']+=1;continue
        if r['team'].strip()!=g['team']:
            raise ValueError(f'State row {i}: team mismatch')
        numeric={k:nonneg(r[k],k) for k in ('minutes','pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta','turnovers','team_fga_on','team_fta_on','team_turnovers_on')}
        if numeric['fgm']>numeric['fga'] or numeric['three_pm']>numeric['three_pa'] or numeric['three_pa']>numeric['fga']:
            raise ValueError(f'State row {i}: shooting mismatch')
        if abs(numeric['pts']-(2*numeric['fgm']+numeric['three_pm']+numeric['ftm']))>.01:
            raise ValueError(f'State row {i}: PTS mismatch')
        if any(numeric[k]>numeric[v] for k,v in (('fga','team_fga_on'),('fta','team_fta_on'),('turnovers','team_turnovers_on'))):
            raise ValueError(f'State row {i}: on-court team events less than player events')
        for tier in split_labels(g['defense_tier']):
            for period in [str(quarter),'ALL']:
                groups[(r['player_id'],r['team'],tier,state,period)].append((r['game_id'],numeric))
    result=[]
    cols=('minutes','pts','reb','ast','fgm','fga','three_pm','three_pa','ftm','fta','turnovers','team_fga_on','team_fta_on','team_turnovers_on')
    for (pid,team,tier,state,period),rs in groups.items():
        totals={k:sum(x[1][k] for x in rs) for k in cols}
        mins=totals['minutes']; fga=totals['fga']; fta=totals['fta'];to=totals['turnovers']
        den=totals['team_fga_on']+.44*totals['team_fta_on']+totals['team_turnovers_on']
        numerator=fga+.44*fta+to
        result.append(dict(player_id=pid,player_team=team,defense_group=tier,score_state=state,period=period,
            games=len(set(x[0] for x in rs)),minutes=round(mins,2),pts=tidy(totals['pts']),
            fga=tidy(fga),fg_pct=pct(totals['fgm'],fga),three_pa=tidy(totals['three_pa']),
            three_pct=pct(totals['three_pm'],totals['three_pa']),
            pts_per36=round(36*totals['pts']/mins,2) if mins else '',
            fga_per36=round(36*fga/mins,2) if mins else '',
            offensive_finish_share_proxy_pct=pct(numerator,den),
            ranking_ready=int(mins>=24 and len(set(x[0] for x in rs))>=3),
            definition='On-court finishing share proxy (FGA+.44FTA+TOV), NOT official USG%'))
    return result,audit


def run(args):
    periods,audit_quarters=get_input_quarters(args.quarters)
    contexts,audit_ctx=parse_opponents(args.opponents)
    games,timings,missing,calc_audit=verified_player_games(periods,contexts)
    eligible={(x['game_date'],x['game_id'],x['player_id']):x for x in games}
    sums=threshold_summary(timings)
    comparisons_out=comparisons(sums)
    states,states_audit=score_states(args.segments,eligible)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    outputs=[('player_game_defense_context.csv',games),('defense_threshold_game_results.csv',timings),
             ('defense_tier_threshold_summary.csv',sums),('defense_resilience_comparisons.csv',comparisons_out),
             ('defense_lead_trail_splits.csv',states)]
    for name,rows in outputs:
        if rows: csv_out(out/name,rows,list(rows[0]))
    status=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),season='2026-27',
       verified_player_quarter_rows=audit_quarters,opponent_context_rows=len(contexts),
       opponent_source_exclusions=dict(audit_ctx),qualified_player_games=len(games),
       player_games_missing_opponent_context=dict(missing),threshold_game_rows=len(timings),
       split_rows=len(sums),comparisons=len(comparisons_out),lead_trail_rows=len(states),
       lead_trail_exclusions=dict(states_audit),
       warning='NO rows are generated from team-only quarter scores. Minimum 3 top10 and 3 outside top10 for comparative signal; 5+ each preferred. No predictive probability or sportsbook odds.',
       defensive_rank_basis='Official 30-team NBA.com season-to-date defensive rating rank, frozen prior to the game date.',
       score_state_usage_basis='On-court finishing share proxy, not official usage percent.')
    (out/'defense_audit.json').write_text(json.dumps(status,indent=2)+'\n')
    return status


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--quarters',required=True,type=Path)
    ap.add_argument('--opponents',required=True,type=Path)
    ap.add_argument('--segments',type=Path)
    ap.add_argument('--out',default='exports/defensive_resistance',type=Path)
    print(json.dumps(run(ap.parse_args()),indent=2))
