"""Demo buyer agent with a spending cap. Needs the API running: uvicorn api:app
Usage: python demo/buyer_agent.py "Use in a 6 month regional ad campaign" --budget 40"""
import argparse, os, sys, time, webbrowser, httpx

p = argparse.ArgumentParser()
p.add_argument("intent"); p.add_argument("--budget", type=float, required=True)
p.add_argument("--artwork", default="art-001")
a = p.parse_args()
API = os.environ.get("API_URL", "http://localhost:8000")
c = httpx.Client(base_url=API, timeout=120)

q = c.post("/quote", json={"artwork_id": a.artwork, "intent": a.intent}).json()
print(f"Quote: {q['tier']} license, {q['months']} months = ${q['amount']:.2f}\n  why: {q['rationale']}")
if q["amount"] > a.budget:
    sys.exit(f"Over budget (${a.budget:.2f}). Refusing to purchase.")

if q.get("refused"): sys.exit("Artist rules forbid this use. Refusing to purchase.")
o = c.post("/orders", json={"artwork_id": a.artwork, "tier": q["tier"], "months": q["months"]}).json()
print(f"Order {o['order_id']} created. Approve in the browser with your SANDBOX buyer account:\n  {o['approve_url']}")
webbrowser.open(o["approve_url"])

for _ in range(60):  # wait up to ~3 minutes for the human to approve
    r = c.post(f"/orders/{o['order_id']}/capture").json()
    if r["status"] == "COMPLETED": break
    time.sleep(3)
else:
    sys.exit("Timed out waiting for approval.")

print("Paid. Receipt:", r["receipt"][:60], "...")
print("Verified:", c.post("/receipts/verify", json={"receipt": r["receipt"]}).json()["license"])
