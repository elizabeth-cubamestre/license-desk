"""License Desk core: catalog, AI quoting, PayPal sandbox orders, signed receipts."""
import os, json, hmac, hashlib, base64, sqlite3, time, uuid
import httpx
from dotenv import load_dotenv
load_dotenv()

DB = os.environ.get("DB_PATH", "licensedesk.db")
SECRET = os.environ.get("RECEIPT_SECRET", "dev-secret").encode()
PP = os.environ.get("PAYPAL_BASE", "https://api-m.sandbox.paypal.com")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000")
# Price multipliers. The LLM picks a tier; the price is always computed here, never by the LLM.
TIERS = {"personal": 1.0, "editorial": 2.0, "commercial": 5.0, "ai_training": 20.0}

SEED = [
    ("art-001", "Neon Koi Run", "Mira Tanaka", "Hand-drawn 2D animation loop of koi in a neon city", 20.0, 10.0),
    ("art-002", "Paper Moon Lullaby", "Diego Ruiz", "Stop-motion style short with paper puppets", 30.0, 15.0),
    ("art-003", "Orbit Cats", "Amara Okafor", "Character design sheet and walk cycles for a cat astronaut crew", 25.0, 12.0),
]

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS artworks(id TEXT PRIMARY KEY, title TEXT, artist TEXT,
                     description TEXT, base_price REAL, min_price REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, artwork_id TEXT, tier TEXT,
                     months INTEGER, amount REAL, status TEXT, receipt TEXT, created REAL)""")
        c.execute("CREATE TABLE IF NOT EXISTS refs(artwork_id TEXT PRIMARY KEY, mime TEXT, data BLOB)")
        c.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, v TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY AUTOINCREMENT, artwork_id TEXT,
                     image_url TEXT, source_hint TEXT, verdict TEXT, score INTEGER, evidence TEXT,
                     status TEXT DEFAULT 'OPEN', invoice TEXT, created REAL)""")
        for col in ("allow_ai INTEGER DEFAULT 1", "phash TEXT"):
            try: c.execute(f"ALTER TABLE artworks ADD COLUMN {col}")
            except sqlite3.OperationalError: pass
        if not c.execute("SELECT 1 FROM artworks").fetchone():
            c.executemany("INSERT INTO artworks(id,title,artist,description,base_price,min_price) VALUES(?,?,?,?,?,?)", SEED)

    # Optional demo images: put art-001.jpg, art-002.jpg, art-003.jpg in demo/seed/ so a fresh deploy has real artwork
    seed_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "seed")
    for aid, *_ in SEED:
        for ext in ("jpg", "jpeg", "png", "webp"):
            f = os.path.join(seed_dir, f"{aid}.{ext}")
            if not os.path.exists(f): continue
            with db() as c: has = c.execute("SELECT 1 FROM refs WHERE artwork_id=? OR NOT EXISTS (SELECT 1 FROM artworks WHERE id=?)", (aid, aid)).fetchone()
            if not has:
                data = open(f, "rb").read()
                save_reference(aid, "image/" + ("jpeg" if ext in ("jpg", "jpeg") else ext), data)
                with db() as c: c.execute("UPDATE artworks SET phash=? WHERE id=?", (dhash(data), aid))

def list_artworks(query=""):
    q = f"%{query.lower()}%"
    with db() as c:
        rows = c.execute("SELECT * FROM artworks WHERE lower(title||description||artist) LIKE ?", (q,)).fetchall()
    return [dict(r) for r in rows]

def get_artwork(aid):
    with db() as c:
        r = c.execute("SELECT * FROM artworks WHERE id=?", (aid,)).fetchone()
    if not r: raise ValueError(f"unknown artwork {aid}")
    return dict(r)

def price(art, tier, months):
    return round(max(art["min_price"], art["base_price"] * TIERS[tier] * months / 12), 2)

def _rule_based(intent):
    t = intent.lower()
    tier = ("ai_training" if any(w in t for w in ("train", "dataset", "fine-tune", "finetune")) else
            "commercial" if any(w in t for w in ("ad", "brand", "commercial", "product", "marketing")) else
            "editorial" if any(w in t for w in ("article", "news", "blog", "editorial")) else "personal")
    return {"tier": tier, "months": 6, "rationale": "Keyword-based fallback."}

def quote(artwork_id, intent):
    """Licensing agent: maps a buyer's plain-language intent to a tier + duration, then prices it."""
    art = get_artwork(artwork_id)
    choice = _rule_based(intent)
    if os.environ.get("LLM_API_KEY"):
        try:
            from openai import OpenAI
            r = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"], timeout=float(os.environ.get("LLM_TIMEOUT", "45")), max_retries=1).chat.completions.create(
                model=os.environ.get("LLM_MODEL", "gemini-2.5-flash"), temperature=0,
                messages=[{"role": "system", "content": "You are an artist's licensing agent. Reply with JSON only: "
                           '{"tier": one of personal|editorial|commercial|ai_training, "months": integer 1-60, '
                           '"rationale": one sentence}. Choose the narrowest tier that covers the stated use. '
                           "Any use as model training data is ai_training."},
                          {"role": "user", "content": f"Work: {art['title']} - {art['description']}\nBuyer intent: {intent}"}])
            txt = r.choices[0].message.content
            choice = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        except Exception as e:
            choice["rationale"] += f" (LLM failed: {type(e).__name__}: {str(e)[:200]})"
    tier = choice.get("tier") if choice.get("tier") in TIERS else "commercial"
    months = max(1, min(60, int(choice.get("months", 6))))
    if tier == "ai_training" and not art.get("allow_ai", 1):
        return {"artwork_id": artwork_id, "title": art["title"], "refused": True, "tier": tier, "months": months, "amount": 0,
                "currency": "USD", "rationale": "The artist does not allow this work to be used for AI training."}
    return {"artwork_id": artwork_id, "title": art["title"], "tier": tier, "months": months,
            "amount": price(art, tier, months), "currency": "USD", "rationale": choice.get("rationale", "")}

# ---------- PayPal ----------
_tok = {"v": None, "exp": 0}
def _token():
    if _tok["v"] and time.time() < _tok["exp"] - 60: return _tok["v"]
    r = httpx.post(f"{PP}/v1/oauth2/token", auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
                   data={"grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status(); d = r.json()
    _tok.update(v=d["access_token"], exp=time.time() + d["expires_in"]); return _tok["v"]

def _pp(method, path, body=None, req_id=None):
    h = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    if req_id: h["PayPal-Request-Id"] = req_id  # idempotency key
    r = httpx.request(method, f"{PP}{path}", headers=h, json=body, timeout=30)
    if r.status_code >= 400: raise RuntimeError(f"PayPal {r.status_code}: {r.text[:400]}")
    return r.json()

def create_order(artwork_id, tier, months, amount=None):
    art = get_artwork(artwork_id)
    if tier not in TIERS: raise ValueError("bad tier")
    if tier == "ai_training" and not art.get("allow_ai", 1): raise ValueError("The artist does not allow AI training use")
    amount = amount if amount is not None else price(art, tier, months)  # API callers never pass amount; server prices it
    o = _pp("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": artwork_id, "custom_id": f"{tier}:{months}",
                            "description": f"{tier} license ({months} mo): {art['title']}"[:127],
                            "amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}],
        "payment_source": {"paypal": {"experience_context": {
            "user_action": "PAY_NOW", "return_url": f"{PUBLIC_URL}/paid", "cancel_url": f"{PUBLIC_URL}/paid?cancelled=1"}}},
    }, req_id=str(uuid.uuid4()))
    link = next(l["href"] for l in o["links"] if l["rel"] in ("payer-action", "approve"))
    with db() as c:
        c.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?)",
                  (o["id"], artwork_id, tier, months, amount, "CREATED", None, time.time()))
    return {"order_id": o["id"], "approve_url": link, "amount": amount, "status": o["status"]}

def order_status(order_id):
    o = _pp("GET", f"/v2/checkout/orders/{order_id}")
    with db() as c: c.execute("UPDATE orders SET status=? WHERE order_id=?", (o["status"], order_id))
    return {"order_id": order_id, "status": o["status"]}

def capture_order(order_id):
    with db() as c: row = c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not row: raise ValueError("unknown order")
    if row["receipt"]: return {"status": "COMPLETED", "receipt": row["receipt"]}  # already captured
    if order_status(order_id)["status"] != "APPROVED":
        return {"status": "WAITING_FOR_BUYER_APPROVAL"}
    cap = _pp("POST", f"/v2/checkout/orders/{order_id}/capture", {}, req_id=f"capture-{order_id}")
    if cap["status"] != "COMPLETED": return {"status": cap["status"]}
    cap_id = cap["purchase_units"][0]["payments"]["captures"][0]["id"]
    art = get_artwork(row["artwork_id"])
    now = int(time.time())
    receipt = sign({"license_id": f"LD-{cap_id}", "artwork_id": art["id"], "title": art["title"], "artist": art["artist"],
                    "tier": row["tier"], "months": row["months"], "amount_usd": row["amount"],
                    "paypal_capture_id": cap_id, "issued_at": now, "expires_at": now + row["months"] * 30 * 86400})
    with db() as c: c.execute("UPDATE orders SET status='COMPLETED', receipt=? WHERE order_id=?", (receipt, order_id))
    return {"status": "COMPLETED", "receipt": receipt}

# ---------- Signed receipts (anyone with the token can verify; only we can issue) ----------
def _b64(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def sign(payload):
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())}"

def verify(token):
    try:
        body, sig = token.split(".")
        ok = hmac.compare_digest(sig, _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()))
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return {"valid": False}
    return {"valid": ok, "expired": payload["expires_at"] < time.time(), "license": payload if ok else None}


# ---------- Sentinel: find suspected copies, verify with a vision model, convert to revenue ----------
import base64 as _b64mod
from concurrent.futures import ThreadPoolExecutor

def save_reference(artwork_id, mime, data):
    get_artwork(artwork_id)
    with db() as c: c.execute("INSERT OR REPLACE INTO refs VALUES(?,?,?)", (artwork_id, mime, data))

def _data_url(mime, data): return f"data:{mime};base64,{_b64mod.b64encode(data).decode()}"

def _tavily(query):
    with db() as c:  # cache so repeat scans cost no credits
        hit = c.execute("SELECT v FROM cache WHERE k=?", (query,)).fetchone()
    if hit: return json.loads(hit["v"])
    r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                   json={"query": query, "search_depth": "basic", "include_images": True, "max_results": 5}, timeout=30)
    r.raise_for_status(); d = r.json()
    out = {"images": [i if isinstance(i, str) else i.get("url") for i in d.get("images", [])],
           "page": (d.get("results") or [{}])[0].get("url", "")}
    with db() as c: c.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (query, json.dumps(out)))
    return out

def dhash(data, flip=False):
    """64-bit perceptual hash: survives resizing, recompression and mild edits. Runs locally, no API quota."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("L")
    if flip: im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)  # catches mirrored copies
    px = list(im.resize((9, 8)).getdata())
    bits = 0
    for r in range(8):
        for c in range(8): bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return f"{bits:016x}"

def _score(d): return max(0, round(100 * (1 - d / 32)))  # distance ~32 is what unrelated images score, so that maps to 0%
def _verdict(d): return "exact_copy" if d <= 5 else "derivative" if d <= 12 else "unrelated"

def hamming(a, b): return bin(int(a, 16) ^ int(b, 16)).count("1")

def create_artwork(title, artist, description, base_price, min_price, allow_ai, mime, data):
    if base_price <= 0 or min_price <= 0: raise ValueError("Prices must be above zero")
    h = dhash(data); aid = "art-" + uuid.uuid4().hex[:6]
    with db() as c:
        c.execute("INSERT INTO artworks(id,title,artist,description,base_price,min_price,allow_ai,phash) VALUES(?,?,?,?,?,?,?,?)",
                  (aid, title.strip(), artist.strip(), description.strip(), base_price, min_price, int(allow_ai), h))
    save_reference(aid, mime, data)
    return get_artwork(aid)

def list_licenses():
    with db() as c:
        rows = c.execute("""SELECT o.order_id, o.tier, o.months, o.amount, o.created, a.title, a.artist FROM orders o
                            JOIN artworks a ON a.id=o.artwork_id WHERE o.status='COMPLETED' ORDER BY o.created DESC""").fetchall()
    return [dict(r) for r in rows]

def _fetch_image(url):
    from urllib.parse import urlparse
    u = urlparse(url)
    r = httpx.get(url, timeout=25, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8", "Referer": f"{u.scheme}://{u.netloc}/"})
    if r.status_code != 200: raise ValueError(f"HTTP {r.status_code}, the site blocks downloads")
    if len(r.content) > 40_000_000: raise ValueError("file too large")
    return r.content

def _lens(artwork_id):
    """True reverse image search (Google Lens via SerpApi). Needs a public URL for our image, so it works once deployed."""
    key, base = os.environ.get("SERPAPI_KEY"), os.environ.get("PUBLIC_URL", "")
    if not key or not base or "localhost" in base or "127.0.0.1" in base: return None
    ck = f"lens:{artwork_id}"
    with db() as c: hit = c.execute("SELECT v FROM cache WHERE k=?", (ck,)).fetchone()
    if hit: return [tuple(x) for x in json.loads(hit["v"])]
    r = httpx.get("https://serpapi.com/search.json", timeout=90,
                  params={"engine": "google_lens", "url": f"{base}/artworks/{artwork_id}/image", "api_key": key})
    r.raise_for_status()
    out = [(m.get("thumbnail") or m.get("image"), m.get("link", "")) for m in r.json().get("visual_matches", [])[:25]
           if m.get("thumbnail") or m.get("image")]
    with db() as c: c.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (ck, json.dumps(out)))
    return out

def scan(artwork_id):
    art = get_artwork(artwork_id)
    with db() as c: ref = c.execute("SELECT * FROM refs WHERE artwork_id=?", (artwork_id,)).fetchone()
    if not ref: raise ValueError("This artwork has no image to compare against.")
    h = art.get("phash") or dhash(ref["data"]); hf = dhash(ref["data"], True)
    cands, mode, queries = None, "reverse image search", []
    try: cands = _lens(artwork_id)
    except Exception: cands = None
    if not cands:
        mode = "keyword web search"
        queries = [q for q in [f"{art['title']} {art['artist']}", f"{art['title']} art", art["description"]] if q.strip()]
        queries = queries[: int(os.environ.get("MAX_SEARCHES_PER_SCAN", "5"))]
        seen, cands = set(), []
        for q in queries:
            t = _tavily(q)
            for u in t["images"]:
                if u and u not in seen: seen.add(u); cands.append((u, t["page"]))
    cands = cands[: int(os.environ.get("MAX_IMAGES", "12"))]
    def one(c):
        try:
            dh = dhash(_fetch_image(c[0])); d = min(hamming(h, dh), hamming(hf, dh)); sc = _score(d)
            return (c, {"verdict": _verdict(d), "score": sc, "evidence": f"Perceptual-hash distance {d}/64 (0 = identical, about 32 = unrelated)."})
        except Exception as e:
            return (c, {"verdict": "skipped", "score": 0, "evidence": f"Could not analyze: {str(e)[:90]}"})
    with ThreadPoolExecutor(4) as ex: results = list(ex.map(one, cands))
    found, checked = [], []
    for (url, page), j in results:
        row = {"image_url": url, "source_hint": page, **j}
        if j["verdict"] in ("exact_copy", "derivative"):
            with db() as c:
                old = c.execute("SELECT id FROM findings WHERE artwork_id=? AND image_url=?", (artwork_id, url)).fetchone()
                fid = old["id"] if old else c.execute(
                    "INSERT INTO findings(artwork_id,image_url,source_hint,verdict,score,evidence,created) VALUES(?,?,?,?,?,?,?)",
                    (artwork_id, url, page, j["verdict"], j["score"], j["evidence"], time.time())).lastrowid
            row["id"] = fid; found.append(row)
        checked.append(row)
    return {"mode": mode, "queries_used": len(queries), "images_checked": len(cands), "matches": found, "checked": checked}

def check_image(artwork_id, mime, data):
    """Artist uploads a suspect image; same matching as the web scan."""
    art = get_artwork(artwork_id)
    with db() as c: ref = c.execute("SELECT * FROM refs WHERE artwork_id=?", (artwork_id,)).fetchone()
    if not ref: raise ValueError("This artwork has no image to compare against.")
    h = art.get("phash") or dhash(ref["data"]); hf = dhash(ref["data"], True)
    dh = dhash(data); d = min(hamming(h, dh), hamming(hf, dh)); v = _verdict(d); sc = _score(d)
    ev = f"Perceptual-hash distance {d}/64 (0 = identical, about 32 = unrelated)."
    out = {"image_url": "", "source_hint": "", "verdict": v, "score": sc, "evidence": ev}
    if v in ("exact_copy", "derivative"):
        with db() as c:
            fid = c.execute("INSERT INTO findings(artwork_id,image_url,source_hint,verdict,score,evidence,created) VALUES(?,?,?,?,?,?,?)",
                            (artwork_id, "", "Uploaded by the artist", v, sc, ev, time.time())).lastrowid
            c.execute("INSERT OR REPLACE INTO refs VALUES(?,?,?)", (f"finding-{fid}", mime, data))
            c.execute("UPDATE findings SET image_url=? WHERE id=?", (f"/findings/{fid}/image", fid))
        out.update(id=fid, image_url=f"/findings/{fid}/image")
    return {"images_checked": 1, "matches": [out] if out.get("id") else [], "checked": [out]}

def list_findings():
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM findings ORDER BY id DESC")]

def retro_price(art): return round(price(art, "commercial", 12) * 1.5, 2)  # 1.5x for unlicensed past use

def invoice_finding(finding_id, recipient_email):
    """Turn a confirmed copy into revenue: PayPal invoice for a retroactive license (falls back to an order link)."""
    with db() as c: f = c.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    if not f: raise ValueError("unknown finding")
    art = get_artwork(f["artwork_id"]); amt = f"{retro_price(art):.2f}"
    where = f["image_url"] if f["image_url"].startswith("http") else (f["source_hint"] or "the web")
    note = (f"Retroactive commercial license for '{art['title']}' by {art['artist']}. "
            f"Our scan found a likely unlicensed use at {where}. Paying grants a 12-month license.")
    try:
        inv = _pp("POST", "/v2/invoicing/invoices", {
            "detail": {"currency_code": "USD", "note": note[:4000]},
            "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
            "items": [{"name": f"Retroactive license: {art['title']}"[:200], "quantity": "1",
                       "unit_amount": {"currency_code": "USD", "value": amt}}]}, req_id=f"inv-{finding_id}-{recipient_email}")
        inv_id = inv["href"].rstrip("/").split("/")[-1]
        _pp("POST", f"/v2/invoicing/invoices/{inv_id}/send", {"send_to_recipient": True, "send_to_invoicer": True})
        result = {"mode": "invoice", "invoice_id": inv_id, "amount": amt}
    except Exception as e:
        o = create_order(art["id"], "commercial", 12, amount=float(amt))
        result = {"mode": "order_link", "order_id": o["order_id"], "pay_url": o["approve_url"], "amount": amt,
                  "note": f"Invoicing API failed ({str(e)[:160]}); created a payment link instead."}
    with db() as c: c.execute("UPDATE findings SET status='INVOICED', invoice=? WHERE id=?", (json.dumps(result), finding_id))
    return result