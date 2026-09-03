#!/usr/bin/env python3
"""Fetch rich place details from Google Maps.

Two-step: load the place page (which embeds a <link rel=preload> pointing at the
/maps/preview/place RPC with a valid per-request session token), then replay it.
"""
import json,subprocess,urllib.parse,re,html,sys,time

UA=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def curl(url,accept_lang="en-US,en;q=0.9"):
    return subprocess.run(["curl","-s","--max-time","30","-A",UA,
                           "-H","Accept-Language: "+accept_lang,
                           "-H","Referer: https://www.google.com/maps/",url],
                          capture_output=True,text=True).stdout

def hexfid(fid):
    if fid.startswith("0x"): return fid
    a,b=fid.split(':'); u=lambda v:(int(v)+(1<<64))%(1<<64)
    return "0x%x:0x%x"%(u(a),u(b))

# Accept-Language headers per UI language; the RPC honours both the header and
# the hl/gl query params, so we set them consistently.
ACCEPT_LANG={"en":"en-US,en;q=0.9","ja":"ja,en-US;q=0.9,en;q=0.8"}

def fetch(fid,lat,lng,hl="en",gl="us"):
    """Fetch the place doc. Defaults to English/US so existing callers are unaffected."""
    al=ACCEPT_LANG.get(hl,hl+",en;q=0.9")
    page=curl("https://www.google.com/maps/place/data=!4m5!3m4!1s%s!8m2!3d%s!4d%s?hl=%s&gl=%s"
              %(hexfid(fid),lat,lng,hl,gl),al)
    m=re.search(r'<link href="(/maps/preview/place\?[^"]+)"',page)
    if not m: return None
    raw=curl("https://www.google.com"+html.unescape(m.group(1)),al)
    if '[' not in raw: return None
    return json.loads(raw[raw.index('['):],strict=False)

def g(o,*path):
    for i in path:
        if o is None: return None
        try: o=o[i]
        except (IndexError,KeyError,TypeError): return None
    return o

def parse(doc):
    P=doc[6]
    ext=lambda blob: sorted({v for v in re.findall(r'https?://[^"\\ ]+',json.dumps(blob or []))
                             if 'google.com' not in v and 'gstatic' not in v})
    return {
        "name": g(P,11),
        "categories": g(P,13),
        "rating": g(P,4,7),
        "review_count": g(P,37,1),
        "price_level": g(P,4,2),
        "address": g(P,18) or " ".join(x for x in (g(P,2) or []) if x),
        "neighborhood": g(P,14),
        "website": g(P,7,0),
        "phone": g(P,178,0,0),
        "hours_week": [[d[0],[s[0] for s in (d[3] or [])]]
                       for d in (g(P,34,1) or []) if d] or None,
        "hours_today": [s[0] for s in (g(P,203,0,0,3) or [])] or None,
        "status": g(P,203,1,4,0),
        "timezone": g(doc,31,1,0,0),
        "icon": g(doc,29),
        "photo_urls": [u for u in (g(P,72,0) or []) for u in [g(u,6,0)] if u][:5],
        "booking_links": ext(g(P,75)),
        "attributes": g(P,100,1),
    }

if __name__=="__main__":
    doc=fetch(sys.argv[1],sys.argv[2],sys.argv[3])
    if doc is None: sys.exit("no data")
    if "--raw" in sys.argv: json.dump(doc,open("place_last.json","w"),ensure_ascii=False)
    out=parse(doc); out.pop("attributes",None)
    print(json.dumps(out,ensure_ascii=False,indent=1)[:2200])
