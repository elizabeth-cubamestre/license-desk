# License Desk

**Agent-ready licensing for artists, paid through PayPal.**

AI is using artists' work without asking, and watermarks and scraping defences keep getting beaten. License Desk takes a different approach: make it easy to *pay*. An artist uploads work and sets terms once. Buyers, and AI agents, describe how they want to use it in plain language, get an exact price, pay through PayPal, and receive a signed, verifiable license. When someone skips that step, the artist can find the copy and invoice for a retroactive license instead of just sending a takedown.

## Features
- **Artist storefront:** upload a work, set a starting price and a floor, and decide whether AI-training use is allowed.
- **AI licensing agent:** turns "a 6 month regional ad campaign" into a license tier and duration. The LLM never sets prices. Code computes them and the server recomputes at checkout.
- **PayPal checkout:** Orders v2 create and capture with idempotency keys. Server-side pricing.
- **Signed licenses:** HMAC-signed receipts that anyone can verify through `/receipts/verify` or the MCP tool.
- **MCP server for agents:** five tools (`search_licenses`, `get_quote`, `purchase_license`, `complete_purchase`, `verify_receipt`).
- **Budget-capped buyer agent:** a live LLM agent buys a license end to end. Budget and artist rules are enforced in code, so the model cannot overspend or override them.
- **Sentinel:** searches the web (Tavily) for copies, compares them to the original with perceptual hashing (resizes, recompression, mirroring, light edits), produces a report, and sends a PayPal invoice for a retroactive license. Artists can also upload a suspect image directly.
- **Earnings dashboard** built with AG Grid.

## PayPal APIs used
Orders v2 (create, get, capture), Invoicing v2 (create, send; falls back to an order link if Invoicing is not enabled), OAuth2 client credentials. Sandbox only.

## AI tools used
- Any OpenAI-compatible LLM for the licensing agent and the buyer agent (tested with Gemini and Groq).
- Model Context Protocol (MCP) Python SDK for agent tools.
- Tavily Search API for finding suspected copies.
- AG Grid Community for the dashboard.

## Quickstart (about 10 minutes)
Requires Python 3.10+.
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in the keys below
uvicorn api:app --reload
```
Open http://localhost:8000.

**Keys you need**
1. PayPal sandbox app: developer.paypal.com, Apps & Credentials, Sandbox. Put the Client ID and Secret in `.env`. Optionally tick **Invoicing** in the app's features.
2. An LLM key (optional; without one, quotes use a keyword fallback). Examples: Groq (`LLM_BASE_URL=https://api.groq.com/openai/v1`) or Google AI Studio.
3. Tavily key (only for the web scan; the suspect-image check works without it).

**Sandbox buyer for testing payments:** use any Personal account from Testing Tools > Sandbox Accounts in the PayPal developer dashboard. Judges: a ready-to-use buyer login is provided in the submission notes.

## Try it
1. **Sell my art:** upload an image.
2. **License art:** choose it, type "6 month regional ad campaign", pay with the sandbox buyer. A signed license appears.
3. **Protect my art:** upload a resized or mirrored copy under "check a suspect image". It is flagged, with a report and an invoice option.
4. **Earnings:** the sale appears in the grid.

**Live agent demo** (second terminal, with the server running):
```bash
python demo/agent.py "I need a cat character for a coffee shop ad, 6 months, regional" --budget 80
python demo/agent.py "Same task" --budget 10          # budget guard blocks the purchase
```

## Limits (honest)
Sentinel detects reposts, resizes, mirrored and lightly edited copies. It does not detect redrawn work or style imitation, and style alone is generally not infringement. Findings are leads for a human to verify, not legal conclusions. Payments are sandbox only.

## Layout
`core.py` logic, `api.py` HTTP API and static site, `mcp_server.py` MCP tools, `static/` UI, `demo/` agents and seed images.

License: MIT
