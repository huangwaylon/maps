#!/usr/bin/env python3
"""Enrich N places from list_raw.json with details from the Maps place RPC."""
import json,sys,time,re
from fetch_place import fetch,parse,g

places=json.load(open('list_raw.json'))['places']
n=int(sys.argv[1]) if len(sys.argv)>1 else 12
# spread across countries for a representative sample
picked,seen=[],set()
for x in places:
    if not x['fid']: continue
    key=(x['address_full'] or '')[-8:]
    if key in seen and len(picked)<n: continue
    seen.add(key); picked.append(x)
    if len(picked)>=n: break

out=[]
for i,x in enumerate(picked,1):
    try:
        doc=fetch(x['fid'],x['lat'],x['lng'])
        det=parse(doc) if doc else None
    except Exception as e:
        det={"error":str(e)}
    rec={"list_name":x['name'],"note":x['note'],"gid":x['gid'],
         "lat":x['lat'],"lng":x['lng'],"details":det}
    out.append(rec)
    d=det or {}
    print("%2d/%d  %-30s cat=%-24s ★%-4s (%s)  hrs=%s  web=%s"%(
        i,len(picked),(x['name'] or '')[:28],
        (d.get('categories') or [''])[0][:22], d.get('rating'), d.get('review_count'),
        "Y" if d.get('hours_today') else "-", "Y" if d.get('website') else "-"),flush=True)
    time.sleep(1.2)
json.dump(out,open('enriched.json','w'),ensure_ascii=False,indent=1)
print("\nwrote enriched.json")
