#!/usr/bin/env python3
# PRIMA - pubblicazione automatica del feed e delle storie Syntonia.
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

# le storie: due momenti al giorno, ora italiana
MATTINA, SERA = (9, 30), (21, 0)
GIORNO_ZERO = datetime.date(2026, 9, 3)

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

def now():   return datetime.datetime.now(datetime.timezone.utc)
def when(p): return datetime.datetime.strptime(p["when"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
def capo(p): return p["caption"].split("\n")[0].strip()

def _ultima_domenica(anno, mese):
    d = datetime.date(anno, mese, 31)
    while d.weekday() != 6: d -= datetime.timedelta(days=1)
    return d

def ora_italiana():
    """L'ora legale la calcola, non la indovina: ultima domenica di marzo e di ottobre."""
    n = now(); a = n.year
    est = _ultima_domenica(a, 3) <= n.date() < _ultima_domenica(a, 10)
    return n + datetime.timedelta(hours=2 if est else 1)

def leggi(f, vuoto):
    try: return json.loads((STATO / f).read_text(encoding="utf-8"))
    except Exception: return vuoto

def scrivi(f, dati):
    STATO.mkdir(exist_ok=True)
    (STATO / f).write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")

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

def ig_storia(rel):
    cid = api(IG + "/media", {"media_type": "STORIES", "image_url": RAW + rel}, post=True)["id"]
    ig_attendi(cid, 4)
    api(IG + "/media_publish", {"creation_id": cid}, post=True)

# ── le storie ────────────────────────────────────────────────────────────────
def elenco(cartella):
    d = ROOT / cartella
    return sorted(x.name for x in d.glob("*.jpg")) if d.is_dir() else []

def storie():
    carte, foto = elenco("carte"), elenco("storie")
    if not carte and not foto:
        log("ST  nessun materiale: cartelle carte/ e storie/ vuote o assenti"); return
    loc = ora_italiana()
    minuti = loc.hour * 60 + loc.minute
    d = (loc.date() - GIORNO_ZERO).days
    if d < 0: return
    blocchi = []
    if minuti >= MATTINA[0] * 60 + MATTINA[1]:
        b = []
        if carte: b.append("carte/" + carte[d % len(carte)])
        if foto:  b.append("storie/" + foto[(2 * d) % len(foto)])
        blocchi.append(("mattina", b))
    if minuti >= SERA[0] * 60 + SERA[1] and foto:
        blocchi.append(("sera", ["storie/" + foto[(2 * d + 1) % len(foto)]]))
    fatti = leggi("storie.json", {})
    nuove = 0
    for nome, files in blocchi:
        chiave = "%s-%s" % (loc.date().isoformat(), nome)
        if chiave in fatti: continue
        usciti = []
        for rel in files:
            try:
                ig_storia(rel); usciti.append(rel); nuove += 1
            except Exception as e:
                log("ST  FALLITA %s: %s" % (rel, e))
        if usciti:
            fatti[chiave] = usciti
            log("ST  %s: %s" % (nome, ", ".join(usciti)))
    limite = (loc.date() - datetime.timedelta(days=40)).isoformat()
    fatti = {k: v for k, v in fatti.items() if k[:10] >= limite}
    scrivi("storie.json", fatti)
    if not nuove: log("ST  niente da pubblicare")

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
def feed(posts):
    usciti_n = set(leggi("usciti.json", []))
    recenti = {(m.get("caption") or "").split("\n")[0].strip()
               for m in api(IG + "/media", {"fields": "caption", "limit": "40"}).get("data", [])}
    soglia  = now() + datetime.timedelta(minutes=10)
    vecchio = now() - datetime.timedelta(days=21)
    dovuti = [p for p in posts if when(p) <= soglia and when(p) >= vecchio
              and p["n"] not in usciti_n and capo(p) not in recenti]
    if not dovuti: log("IG  niente da pubblicare")
    for p in dovuti[:1]:
        try:
            ig_pubblica(p); usciti_n.add(p["n"])
        except Exception as e:
            log("IG  FALLITO n.%s %s: %s" % (p["n"], p["id"], e)); break
    if len(dovuti) > 1:
        log("IG  ATTENZIONE: restano %d post arretrati" % (len(dovuti) - 1))
    for p in posts:
        if when(p) < vecchio and p["n"] not in usciti_n and capo(p) not in recenti:
            usciti_n.add(p["n"])
    scrivi("usciti.json", sorted(usciti_n))

def main():
    posts = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["posts"]
    STATO.mkdir(exist_ok=True)
    for nome, f in (("feed", lambda: feed(posts)), ("storie", storie), ("facebook", lambda: fb_ricarica(posts))):
        try: f()
        except Exception as e: log("%s: fallito -> %s" % (nome, e))
    try:
        d = api("debug_token", {"input_token": TOKEN})["data"]
        g = (datetime.datetime.fromtimestamp(d["data_access_expires_at"], datetime.timezone.utc) - now()).days
        log("Token: accesso ai dati fra %d giorni" % g)
        if g < 30: log("!!! TOKEN IN SCADENZA: va rinnovato o la macchina si ferma")
    except Exception as e:
        log("debug_token: %s" % e)
    scrivi("log.json", {"run": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "ora italiana": ora_italiana().strftime("%Y-%m-%d %H:%M"), "righe": LOG})

if __name__ == "__main__":
    main()
