#!/usr/bin/env python3
# PRIMA - pubblicazione automatica del feed Syntonia.
# Gira su GitHub Actions. Solo libreria standard, nessuna dipendenza.
# Instagram: pubblica al momento (Meta non permette di programmare).
# Facebook:  ricarica la coda nativa entro i 29 giorni consentiti.
import json, os, time, datetime, pathlib, urllib.request, urllib.parse, urllib.error

TOKEN = os.environ["META_TOKEN"]
PAGE, IG = "1276344548890365", "17841476914737347"
G   = "https://graph.facebook.com/v21.0/"
RAW = "https://raw.githubusercontent.com/Marketingtom-ai/syntonia-prima/main/"
ROOT = pathlib.Path(__file__).parent
STATO = ROOT / "_state"
LOG = []

def log(m):
    print(m, flush=True); LOG.append(m)

def api(path, params=None, post=False, method=None):
    p = dict(params or {}); p["access_token"] = TOKEN
    if post:
        req = urllib.request.Request(G + path, data=urllib.parse.urlencode(p).encode())
    else:
        req = urllib.request.Request(G + path + "?" + urllib.parse.urlencode(p), method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("%s %s -> %s" % (method or ("POST" if post else "GET"), path, e.read().decode()[:300]))

def now():  return datetime.datetime.now(datetime.timezone.utc)
def when(p): return datetime.datetime.strptime(p["when"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
def capo(p): return p["caption"].split("\n")[0].strip()

def files_of(p):
    if p.get("tipo") == "reel":     return [p["file"]]
    if p["id"].startswith("VOCE-"): return [p["id"] + ".jpg"]
    return ["%s_0%d.jpg" % (p["id"], k) for k in (1, 2, 3)]

# ── Instagram ────────────────────────────────────────────────────────────────
def ig_attendi(cid, minuti=6):
    fine = time.time() + minuti * 60
    while time.time() < fine:
        s = api(cid, {"fields": "status_code,status"})
        if s.get("status_code") == "FINISHED": return
        if s.get("status_code") == "ERROR":    raise RuntimeError("container in errore: %s" % s.get("status"))
        time.sleep(10)
    raise RuntimeError("container non pronto entro %d minuti" % minuti)

def ig_pubblica(p):
    fs = files_of(p)
    if p.get("tipo") == "reel":
        cid = api(IG + "/media", {"media_type": "REELS", "video_url": RAW + fs[0], "caption": p["caption"]}, post=True)["id"]
        ig_attendi(cid, 10)
    elif len(fs) == 1:
        cid = api(IG + "/media", {"image_url": RAW + fs[0], "caption": p["caption"]}, post=True)["id"]
        ig_attendi(cid)
    else:
        figli = [api(IG + "/media", {"image_url": RAW + f, "is_carousel_item": "true"}, post=True)["id"] for f in fs]
        for c in figli: ig_attendi(c, 4)
        cid = api(IG + "/media", {"media_type": "CAROUSEL", "children": ",".join(figli), "caption": p["caption"]}, post=True)["id"]
        ig_attendi(cid)
    mid = api(IG + "/media_publish", {"creation_id": cid}, post=True)["id"]
    link = api(mid, {"fields": "permalink"}).get("permalink", "")
    log("IG  pubblicato n.%s %s  %s" % (p["n"], p["id"], link))

# ── Facebook ─────────────────────────────────────────────────────────────────
def fb_programma(p, ep):
    fs = files_of(p)
    if p.get("tipo") == "reel":
        api(PAGE + "/videos", {"file_url": RAW + fs[0], "description": p["caption"],
                               "published": "false", "scheduled_publish_time": ep}, post=True); return
    if len(fs) == 1:
        api(PAGE + "/photos", {"url": RAW + fs[0], "message": p["caption"],
                               "published": "false", "scheduled_publish_time": ep}, post=True); return
    ids = []
    try:
        for f in fs:
            ids.append(api(PAGE + "/photos", {"url": RAW + f, "published": "false"}, post=True)["id"])
        api(PAGE + "/feed", {"message": p["caption"],
                             "attached_media": json.dumps([{"media_fbid": i} for i in ids]),
                             "published": "false", "scheduled_publish_time": ep}, post=True)
    except Exception:
        # una foto non pubblicata vale per un solo post: si buttano e si riprova pulito
        for i in ids:
            try: api(i, method="DELETE")
            except Exception: pass
        raise

def fb_ricarica(posts):
    coda = {x["scheduled_publish_time"] for x in
            api(PAGE + "/scheduled_posts", {"fields": "scheduled_publish_time", "limit": "100"}).get("data", [])}
    limite, fatti = now() + datetime.timedelta(days=29), 0
    for p in posts:
        t = when(p)
        if t <= now() or t > limite: continue
        ep = int(t.timestamp())
        if ep in coda: continue
        for tentativo in (1, 2, 3):
            try:
                fb_programma(p, ep); coda.add(ep); fatti += 1
                log("FB  programmato n.%s %s per %s" % (p["n"], p["id"], p["when"])); break
            except Exception as e:
                if tentativo == 3: log("FB  FALLITO n.%s %s: %s" % (p["n"], p["id"], e))
                else: time.sleep(5)
    log("FB  coda: %d gia' presenti, %d aggiunti" % (len(coda) - fatti, fatti))

# ── principale ───────────────────────────────────────────────────────────────
def main():
    posts = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["posts"]
    STATO.mkdir(exist_ok=True)
    f_usciti = STATO / "usciti.json"
    usciti_n = set(json.loads(f_usciti.read_text())) if f_usciti.exists() else set()
    recenti = {(m.get("caption") or "").split("\n")[0].strip()
               for m in api(IG + "/media", {"fields": "caption", "limit": "40"}).get("data", [])}
    soglia = now() + datetime.timedelta(minutes=10)
    vecchio = now() - datetime.timedelta(days=21)
    dovuti = [p for p in posts if when(p) <= soglia and when(p) >= vecchio
              and p["n"] not in usciti_n and capo(p) not in recenti]
    if not dovuti:
        log("IG  niente da pubblicare")
    for p in dovuti[:2]:
        try:
            ig_pubblica(p); usciti_n.add(p["n"])
        except Exception as e:
            log("IG  FALLITO n.%s %s: %s" % (p["n"], p["id"], e)); break
    if len(dovuti) > 2:
        log("IG  ATTENZIONE: restano %d post arretrati" % (len(dovuti) - 2))
    for p in posts:
        if when(p) < vecchio and p["n"] not in usciti_n and capo(p) not in recenti:
            usciti_n.add(p["n"])   # troppo vecchio: si considera chiuso, non si ripubblica
    f_usciti.write_text(json.dumps(sorted(usciti_n)), encoding="utf-8")
    try: fb_ricarica(posts)
    except Exception as e: log("FB  ricarica fallita: %s" % e)
    try:
        d = api("debug_token", {"input_token": TOKEN})["data"]
        g = (datetime.datetime.fromtimestamp(d["data_access_expires_at"], datetime.timezone.utc) - now()).days
        log("Token: accesso ai dati fra %d giorni" % g)
        if g < 30: log("!!! TOKEN IN SCADENZA: va rinnovato o la macchina si ferma")
    except Exception as e:
        log("debug_token: %s" % e)
    (STATO / "log.json").write_text(json.dumps(
        {"run": now().strftime("%Y-%m-%dT%H:%M:%SZ"), "righe": LOG}, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main()
