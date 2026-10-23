"""HTTP API. Run: uvicorn api:app --reload   Docs: http://localhost:8000/docs"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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
