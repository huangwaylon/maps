#!/usr/bin/env python3
"""Fetch a shared Google Maps list via the entitylist RPC -> normalized JSON."""
import datetime
import json
import subprocess
import sys
import urllib.parse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def curl(args, url):
    """Run curl and return stdout, raising a clear error on failure/empty output."""
    p = subprocess.run(["curl", *args, url], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("curl failed (exit %d) for %s: %s"
                           % (p.returncode, url, p.stderr.strip()[:200]))
    if not p.stdout.strip():
        raise RuntimeError("curl returned no data for %s" % url)
    return p.stdout


def resolve_short(url):
    out = curl(["-s", "-o", "/dev/null", "-L", "-A", "curl/8.4.0",
                "-w", "%{url_effective}"], url)
    return out


def list_id_from_url(url):
    if "goo.gl" in url:
        url = resolve_short(url)
    for part in url.replace("!", "\n").split("\n"):
        if part.startswith("2s") and len(part) > 10:
            return part[2:]
    raise SystemExit("no list id in " + url)


def fetch(list_id, size=5000, hl="en", gl="us"):
    pb = "!1m4!1s%s!2e1!3m1!1e1!2e2!3e2!4i%d!28e2!16b1" % (list_id, size)
    url = ("https://www.google.com/maps/preview/entitylist/getlist"
           "?authuser=0&hl=%s&gl=%s&pb=" % (hl, gl) + urllib.parse.quote(pb, safe=''))
    raw = curl(["-s", "-A", UA, "-H", "Referer: https://www.google.com/maps/"], url)
    if '[' not in raw:
        raise RuntimeError("no JSON array in response from %s" % url)
    return json.loads(raw[raw.index('['):], strict=False)[0]


def ts(v):
    return datetime.datetime.fromtimestamp(v[0], datetime.timezone.utc).isoformat() if v else None


def normalize(d):
    places = []
    for it in d[8] or []:
        loc = it[1] or []
        geo = loc[5] if len(loc) > 5 else None
        fid = loc[6] if len(loc) > 6 else None
        reactions = []
        for grp in (it[7] or []):
            for lvl in grp:
                for r in lvl:
                    reactions.append({"user_id": r[2], "emoji": r[3], "at": ts(r[4])})
        places.append({
            "name": it[2],
            "note": it[3] or None,
            "note_author": it[15][0][0] if len(it) > 15 and it[15] else None,
            "address_full": loc[2] if len(loc) > 2 else None,
            "address_short": loc[4] if len(loc) > 4 else None,
            "lat": geo[2] if geo and len(geo) > 2 else None,
            "lng": geo[3] if geo and len(geo) > 3 else None,
            "fid": ":".join(fid) if fid else None,
            "gid": loc[7] if len(loc) > 7 else None,
            "added": ts(it[9]),
            "updated": ts(it[10]),
            "added_by": it[12][0] if it[12] else None,
            "reactions": reactions or None,
            "also_in_lists": [g[0] for g in it[19]] if len(it) > 19 and it[19] else None,
        })
    return {
        "list_id": d[0][0],
        "list_name": d[4],
        "list_description": d[5] or None,
        "list_icon": d[17] if len(d) > 17 else None,
        "owner": d[3][0] if d[3] else None,
        "total_reported": d[12],
        "created": ts(d[10]),
        "modified": ts(d[11]),
        "public_url": d[2][2] if d[2] else None,
        "places": places,
    }


if __name__ == "__main__":
    url = sys.argv[1]
    lid = list_id_from_url(url)
    print("list id:", lid, file=sys.stderr)
    out = normalize(fetch(lid))
    json.dump(out, open("list_raw.json", "w"), ensure_ascii=False, indent=1)
    print("wrote %d places" % len(out["places"]), file=sys.stderr)
