"""HTTP API. Run: uvicorn api:app --reload   Docs: http://localhost:8000/docs"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import core

core.init()
app = FastAPI(title="License Desk", description="Agent-readable licensing for artists, paid via PayPal.")

class QuoteIn(BaseModel): artwork_id: str; intent: str
class OrderIn(BaseModel): artwork_id: str; tier: str; months: int
class TokenIn(BaseModel): receipt: str

def guard(fn, *a):
    try: return fn(*a)
    except (ValueError, KeyError) as e: raise HTTPException(400, str(e))
    except RuntimeError as e: raise HTTPException(502, str(e))

@app.get("/artworks")
def artworks(q: str = ""): return core.list_artworks(q)
@app.post("/quote")
def quote(b: QuoteIn): return guard(core.quote, b.artwork_id, b.intent)
@app.post("/orders")
def orders(b: OrderIn): return guard(core.create_order, b.artwork_id, b.tier, b.months)
@app.get("/orders/{oid}")
def order(oid: str): return guard(core.order_status, oid)
@app.post("/orders/{oid}/capture")
def capture(oid: str): return guard(core.capture_order, oid)
@app.post("/receipts/verify")
def verify(b: TokenIn): return core.verify(b.receipt)

@app.get("/paid", response_class=HTMLResponse)
def paid(cancelled: int = 0):
    msg = "Payment cancelled." if cancelled else "Payment approved. You can close this tab; the agent will finish the purchase."
    return f"<body style='font:20px system-ui;padding:3rem'>{msg}</body>"

class ScanIn(BaseModel): artwork_id: str
class InvoiceIn(BaseModel): recipient_email: str

@app.post("/artworks/{aid}/image")
async def upload_reference(aid: str, image: UploadFile = File(...)):
    guard(core.save_reference, aid, image.content_type, await image.read()); return {"ok": True}
@app.post("/sentinel/scan")
def sentinel_scan(b: ScanIn): return guard(core.scan, b.artwork_id)
@app.get("/findings")
def findings(): return core.list_findings()
@app.post("/findings/{fid}/invoice")
def invoice(fid: int, b: InvoiceIn): return guard(core.invoice_finding, fid, b.recipient_email)

@app.get("/artworks/{aid}/image")
def reference_image(aid: str):
    with core.db() as c: r = c.execute("SELECT * FROM refs WHERE artwork_id=?", (aid,)).fetchone()
    if not r: raise HTTPException(404, "no reference image")
    return Response(r["data"], media_type=r["mime"])

@app.get("/findings/{fid}/image")
def finding_image(fid: int):
    with core.db() as c: r = c.execute("SELECT * FROM refs WHERE artwork_id=?", (f"finding-{fid}",)).fetchone()
    if not r: raise HTTPException(404, "no image")
    return Response(r["data"], media_type=r["mime"])

@app.post("/sentinel/check")
async def sentinel_check(artwork_id: str = Form(...), image: UploadFile = File(...)):
    return guard(core.check_image, artwork_id, image.content_type, await image.read())

LABELS = {"exact_copy": "Near-identical copy", "derivative": "Edited copy", "similar_composition": "Similar composition"}

@app.get("/findings/{fid}/report", response_class=HTMLResponse)
def report(fid: int):
    import html, time
    with core.db() as c: f = c.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
    if not f: raise HTTPException(404, "unknown finding")
    art = core.get_artwork(f["artwork_id"]); e = lambda v: html.escape(str(v))
    return f"""<body style="font:16px/1.5 system-ui;max-width:860px;margin:2rem auto;padding:0 1rem">
<h1>Similarity report #{f['id']}</h1>
<p><b>Work:</b> {e(art['title'])} by {e(art['artist'])}<br><b>Detected:</b> {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(f['created']))}</p>
<div style="display:flex;gap:16px;flex-wrap:wrap"><figure><img src="/artworks/{e(f['artwork_id'])}/image" width="400"><figcaption>Original</figcaption></figure>
<figure><img src="{e(f['image_url'])}" width="400" referrerpolicy="no-referrer"><figcaption>Found online</figcaption></figure></div>
<p><b>Result:</b> {LABELS.get(f['verdict'], e(f['verdict']))} &nbsp; <b>Match:</b> {e(f['score'])}%</p>
<p><b>What this means:</b> {e(f['evidence'])}</p>
<p style="color:#666"><small><b>How we checked:</b> we shrink both images into a tiny pattern of light and dark areas and compare the two patterns. Nearly identical patterns mean a copy, even if it was resized or recompressed. Moderately similar patterns mean the same layout, pose or color blocks, which can be a redraw or simply a shared trend. This is an automated lead, not a legal conclusion.</small></p>
<p><b>Image:</b> <a href="{e(f['image_url'])}">{e(f['image_url'])}</a><br><b>Possible source page (verify manually):</b> {e(f['source_hint'])}</p>
<p style="color:#666"><i>Automated analysis, not legal advice. A person should verify before taking action.</i></p></body>"""

@app.post("/artworks")
async def add_artwork(title: str = Form(...), artist: str = Form(...), description: str = Form(""),
                      base_price: float = Form(...), min_price: float = Form(...), allow_ai: bool = Form(True),
                      image: UploadFile = File(...)):
    return guard(core.create_artwork, title, artist, description, base_price, min_price, allow_ai,
                 image.content_type, await image.read())

@app.get("/licenses")
def licenses(): return core.list_licenses()

import os
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), html=True), name="ui")
