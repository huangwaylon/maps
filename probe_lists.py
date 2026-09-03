import json,subprocess,urllib.parse
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
def call(lid,size=1):
    pb="!1m4!1s%s!2e1!3m1!1e1!2e2!3e2!4i%d!28e2!16b1"%(lid,size)
    url="https://www.google.com/maps/preview/entitylist/getlist?authuser=0&hl=en&gl=us&pb="+urllib.parse.quote(pb,safe='')
    r=subprocess.run(["curl","-s","-A",UA,"-H","Referer: https://www.google.com/maps/",url],capture_output=True,text=True).stdout
    try: return json.loads(r[r.index('['):],strict=False)[0]
    except Exception as e: return {"err":str(e),"raw":r[:120]}
for lid in ["Xm3xOyR-QfeDy1e-M1ybhA","2J9_dfZ9SFC8057vxW68Og","KZcC6Xa7QbqyBh9ggGRRBA",
            "2ekeJ01PTTOw-8WHOICRYQ","6OyzhbfFQ224t49jhJDrLQ","ZX8XR2fWQ32mFsF6REyHdg",
            "p4Q94-FoS7qia8YYeIcGnQ","v_Ui5k6t3xF92nkzhz2PfA","EBzSbBK2QuqZFCEqMd5Grg"]:
    d=call(lid)
    if isinstance(d,dict): print("%-24s ERR %s"%(lid,d)); continue
    name=d[4]; total=d[12]; owner=d[3][0] if d[3] else None; emoji=d[17] if len(d)>17 else None
    print("%-24s  %-28s total=%-5s owner=%-8s icon=%s"%(lid,name,total,owner,emoji))
