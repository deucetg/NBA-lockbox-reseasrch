"""Normalize saved SportsGameOdds NBA snapshots, without making API requests.

Usage: python nba_odds_normalize.py path/to/fanduel_nba_*.json
Output: same directory, input stem + '_normalized.csv' and '_normalized_status.json'.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

FIELDS = ['captured_at_utc','event_id','event_name','player_id','player','team_id','market_id','market_name','stat_id','period','bet_type','side','line_type','threshold','american_odds','available','bookmaker_updated_at','deeplink']

def normalize(payload):
    rows=[]
    for event in payload.get('events', []):
        players=event.get('players') or {}
        event_id=event.get('eventID','')
        teams=event.get('teams') or {}
        event_name=event.get('name') or event.get('eventName') or str(teams)
        for market in (event.get('odds') or {}).values():
            fd=(market.get('byBookmaker') or {}).get('fanduel')
            if not isinstance(fd,dict):
                continue
            pid=market.get('playerID') or market.get('statEntityID')
            person=players.get(pid,{}) if isinstance(players,dict) else {}
            if not isinstance(person,dict):person={}
            common=dict(captured_at_utc=payload.get('captured_at_utc',''),event_id=event_id,event_name=event_name,
                player_id=pid if pid in players else '',player=person.get('name',''),team_id=person.get('teamID',''),
                market_id=market.get('oddID',''),market_name=market.get('marketName',''),stat_id=market.get('statID',''),
                period=market.get('periodID',''),bet_type=market.get('betTypeID',''),side=market.get('sideID',''))
            offers=[('main',fd)]+[('alternate',a) for a in (fd.get('altLines') or [])] if isinstance(fd.get('altLines') or [],list) else [('main',fd)]
            for typ,offer in offers:
                if not isinstance(offer,dict):continue
                threshold=offer.get('overUnder',offer.get('spread',offer.get('line','')))
                rows.append({**common,'line_type':typ,'threshold':threshold,'american_odds':offer.get('odds',''),
                             'available':offer.get('available',''),'bookmaker_updated_at':offer.get('lastUpdatedAt',''),
                             'deeplink':offer.get('deeplink','')})
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);args=p.parse_args()
    payload=json.loads(args.snapshot.read_text(encoding='utf-8'))
    rows=normalize(payload)
    out=args.snapshot.with_name(args.snapshot.stem+'_normalized.csv')
    with out.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    status={'source':str(args.snapshot),'output':str(out),'events':len(payload.get('events',[])),
            'rows':len(rows),'player_named_rows':sum(bool(r['player']) for r in rows),
            'rows_with_threshold':sum(r['threshold']!='' for r in rows),
            'rows_with_odds':sum(r['american_odds']!='' for r in rows),
            'alternate_rows':sum(r['line_type']=='alternate' for r in rows),
            'note':'Offline conversion only. Odds are historical snapshots, not current live prices.'}
    status_path=args.snapshot.with_name(args.snapshot.stem+'_normalized_status.json')
    status_path.write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(status,indent=2))

if __name__=='__main__':main()
