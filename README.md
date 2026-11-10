# License Desk

**Agent-ready licensing for artists, paid through PayPal.**

> **Live demo:** https://license-desk-81yg.onrender.com
> (Free hosting sleeps when idle, so the first load can take about a minute. Everything runs in the **PayPal sandbox**, so no real money moves.)
>
> **Demo video:** [ADD YOUTUBE LINK]

Artists say their work trained AI models without permission, and invisible protection filters have been shown to be removable. License Desk takes a different approach: make it easy to pay. An artist uploads work and sets terms once. Buyers, and AI agents, describe how they want to use it in plain language, get an exact price, pay through PayPal, and receive a signed, verifiable license. When someone skips that step, the artist can find the copy and invoice for a retroactive license instead of only sending a takedown.

## Features
- **Artist storefront:** upload a work, set a starting price and a floor, and decide whether AI-training use is allowed.
- **AI licensing agent:** turns "a 6 month regional ad campaign" into a license tier and duration. The LLM never sets prices. Code computes them and the server recomputes at checkout.
- **PayPal checkout:** Orders v2 create and capture, with idempotency keys and server-side pricing.
- **Signed licenses:** HMAC-signed receipts that anyone can verify through `/receipts/verify` or the MCP tool.
- **MCP server for agents:** five tools (`search_licenses`, `get_quote`, `purchase_license`, `complete_purchase`, `verify_receipt`).
- **Budget-capped buyer agent:** a live LLM agent buys a license end to end over MCP. Its budget and the artist's rules are enforced in code, so the model cannot overspend or override them. A person approves the payment in PayPal.
- **Sentinel (copy detection):**
  - *Hosted site:* runs a reverse image search (Google Lens via SerpApi) on the artwork itself. Titles and descriptions are never used.
  - *Anywhere:* the artist can upload a suspect image directly.
  - Matches are compared to the original with perceptual hashing, which catches resizes, recompression, mirroring and light edits. Results are labeled **Near-identical copy**, **Edited copy**, **Similar composition** or **Not similar**, each with a plain-English report.
  - Copies can be invoiced through PayPal for a retroactive license. **Similar compositions get a report but no invoice**, because a shared pose or trend is not copying.
- **Earnings dashboard (AG Grid):** license sales and copy invoices, with paid and awaiting-payment status. A "Check for payments" button asks PayPal for updates (webhooks are planned).

## PayPal APIs used (sandbox)
- **Orders v2:** create, get, capture, with `PayPal-Request-Id` idempotency keys.
- **Invoicing v2:** create, send, and get status. If Invoicing is not enabled on the app, it falls back to a PayPal payment link.
- **OAuth2** client credentials.

## AI and other tools used
- An OpenAI-compatible LLM (tested with Groq and Google Gemini) for the licensing agent and the buyer agent, with retries, an optional backup provider and a rule-based fallback.
- Model Context Protocol (MCP) Python SDK for the agent tools.
- SerpApi (Google Lens) for reverse image search.
- Perceptual hashing implemented with Pillow (our own code, not an AI model).
- AG Grid Community for the dashboard. Hosted on Render.

## Try it on the hosted demo
1. Open the live demo. Go to **License art**, click **License this** on an artwork, type "6 month regional ad campaign", and click **Get price**.
2. Click **Pay with PayPal** and approve with a sandbox **Personal** buyer account. A signed license appears. (Judges: a ready-to-use buyer login is in the submission notes.)
3. Open **Earnings** to see the sale.
4. Open **Protect my art**, pick *Takiyasha the Witch and the Skeleton Spectre*, and under "check a suspect image" upload `demo/samples/suspect_takiyasha.jpg` from this repo. It is flagged as a copy, with a report and an invoice option.
5. **Scan the web** uses reverse image search and depends on a free SerpApi quota, so it may be unavailable.

## Run it locally (about 10 minutes)
Requires Python 3.10+.
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in the keys below
uvicorn api:app --reload
```
Open http://localhost:8000.

**Keys**
1. **PayPal sandbox app** (required): developer.paypal.com, Apps & Credentials, Sandbox. Put the Client ID and Secret in `.env`. Tick **Invoicing** in the app's features to enable invoices.
2. **An LLM key** (optional; without one, quotes use built-in rules). Example: Groq, with `LLM_BASE_URL=https://api.groq.com/openai/v1`. A backup provider can go in the `LLM2_*` settings.
3. **SerpApi key** (optional): only for the web scan, which also needs a public `PUBLIC_URL`. The suspect-image check works without it.

Sandbox buyer accounts are under Testing Tools > Sandbox Accounts in the PayPal developer dashboard.

## Live agent demo (local)
With the server running, in a second terminal:
```bash
python demo/agent.py "I need a cheerful purple-haired character illustration for a poster, 6 months, regional" --budget 80
python demo/agent.py "Same request" --budget 10          # the budget guard blocks the purchase
```
When it prints the PayPal link, open it and approve as the sandbox buyer. The agent then finishes and verifies the license. Any MCP-capable client can also use the tools by running `python mcp_server.py`.

## Limits (honest)
- Sentinel finds reposts, resizes, mirrored and lightly edited copies. It does not detect redrawn work or style imitation, and style alone is generally not infringement. Findings are leads for a person to verify, not legal conclusions.
- Spending limits are enforced in the buyer agent we provide, not yet on the server for every MCP client.
- Payments are sandbox only. The free host resets its data on restart; sample artworks reload automatically.

## Layout
`core.py` logic, `api.py` HTTP API and static site, `mcp_server.py` MCP tools, `static/` UI, `demo/` agents, seed images and samples.

License: MIT
