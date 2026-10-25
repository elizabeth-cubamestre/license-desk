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
        if not c.execute("SELECT 1 FROM artworks").fetchone():
            c.executemany("INSERT INTO artworks VALUES(?,?,?,?,?,?)", SEED)

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

def _compare(ref_mime, ref_data, art, image_url):
    img = httpx.get(image_url, timeout=15, follow_redirects=True, headers={"User-Agent": "LicenseDesk-Sentinel"})
    mime = img.headers.get("content-type", "").split(";")[0]
    if img.status_code != 200 or not mime.startswith("image/") or len(img.content) > 4_000_000: return None
    from openai import OpenAI
    r = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"],
               timeout=float(os.environ.get("LLM_TIMEOUT", "45")), max_retries=1).chat.completions.create(
        model=os.environ.get("LLM_MODEL", "gemini-2.5-flash"), temperature=0,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": f"Image 1 is the original artwork '{art['title']}' by {art['artist']}. Image 2 was found online. "
             'Reply JSON only: {"verdict": "exact_copy"|"derivative"|"style_imitation"|"unrelated", "score": 0-100, '
             '"evidence": "specific shared or differing elements"}. Similar style alone is NOT copying; '
             "use exact_copy/derivative only if specific expression (characters, composition, frames) is reproduced."},
            {"type": "image_url", "image_url": {"url": _data_url(ref_mime, ref_data)}},
            {"type": "image_url", "image_url": {"url": _data_url(mime, img.content)}}]}])
    t = r.choices[0].message.content
    return json.loads(t[t.index("{"): t.rindex("}") + 1])

def scan(artwork_id):
    art = get_artwork(artwork_id)
    with db() as c: ref = c.execute("SELECT * FROM refs WHERE artwork_id=?", (artwork_id,)).fetchone()
    if not ref: raise ValueError("Upload a reference image first: POST /artworks/{id}/image")
    queries = [f"{art['title']} {art['artist']}", f"{art['title']} animation", art["description"]]
    queries = queries[: int(os.environ.get("MAX_SEARCHES_PER_SCAN", "5"))]
    seen, cands = set(), []
    for q in queries:
        t = _tavily(q)
        for u in t["images"]:
            if u and u not in seen: seen.add(u); cands.append((u, t["page"]))
    cands = cands[:8]
    def one(c):
        try: return (c, _compare(ref["mime"], ref["data"], art, c[0]))
        except Exception as e: return (c, {"verdict": "error", "score": 0, "evidence": f"{type(e).__name__}: {str(e)[:120]}"})
    with ThreadPoolExecutor(4) as ex: results = list(ex.map(one, cands))
    found = []
    for (url, page), j in results:
        if j and j.get("verdict") in ("exact_copy", "derivative") and int(j.get("score", 0)) >= 60:
            with db() as c:
                cur = c.execute("INSERT INTO findings(artwork_id,image_url,source_hint,verdict,score,evidence,created) VALUES(?,?,?,?,?,?,?)",
                                (artwork_id, url, page, j["verdict"], int(j["score"]), j.get("evidence", ""), time.time()))
            found.append({"id": cur.lastrowid, "image_url": url, "source_hint": page, **j})
    checked = [{"image_url": u, "source_hint": p, **(j or {"verdict": "skipped", "score": 0, "evidence": "Could not analyze."})}
               for (u, p), j in results]
    return {"queries_used": len(queries), "images_checked": len(cands), "matches": found, "checked": checked}

def list_findings():
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM findings ORDER BY id DESC")]

def retro_price(art): return round(price(art, "commercial", 12) * 1.5, 2)  # 1.5x for unlicensed past use

def invoice_finding(finding_id, recipient_email):
    """Turn a confirmed copy into revenue: PayPal invoice for a retroactive license (falls back to an order link)."""
    with db() as c: f = c.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    if not f: raise ValueError("unknown finding")
    art = get_artwork(f["artwork_id"]); amt = f"{retro_price(art):.2f}"
    note = (f"Retroactive commercial license for '{art['title']}' by {art['artist']}. "
            f"Our scan found a likely unlicensed use at {f['image_url']}. Paying grants a 12-month license.")
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
