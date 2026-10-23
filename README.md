# License Desk

Licensing for artists in the age of AI. Artists list work with terms; AI agents discover it over **MCP**, describe their intended use in plain language, get an exact price, pay through **PayPal**, and receive a signed license receipt.

## How it works
1. **Discover**: agent calls `search_licenses` (MCP) or `GET /artworks`.
2. **Quote**: a licensing agent (any OpenAI-compatible LLM) maps intent to a license tier and duration. Prices are computed by code, never by the LLM.
3. **Pay**: `POST /orders` creates a PayPal Orders v2 order (idempotent, price recomputed server-side). The human buyer approves in PayPal.
4. **Receipt**: after capture, an HMAC-signed license receipt is issued and can be verified by anyone.

## Setup (about 10 minutes)
1. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`), then `pip install -r requirements.txt`
2. Go to developer.paypal.com, log in, open **Apps & Credentials**, switch to **Sandbox**, create an app. Copy Client ID and Secret.
3. Under **Testing Tools > Sandbox Accounts** you get a Business and a Personal account. The Personal one is your test buyer.
4. `cp .env.example .env` and fill in the PayPal keys and `RECEIPT_SECRET`. For the LLM, create a free key at aistudio.google.com and set `LLM_API_KEY` (or leave empty for the keyword fallback).
5. Run the API: `uvicorn api:app --reload` and open http://localhost:8000/docs
6. In another terminal run the buyer agent:
   `python demo/buyer_agent.py "Use in a 6 month regional ad campaign" --budget 40`
   Log in with the sandbox **Personal** account when the browser opens. The agent finishes the purchase and prints the verified license.

### MCP
`python mcp_server.py` runs the MCP server over stdio. Point any MCP client at that command.

## Roadmap
Sentinel (web detection with Tavily + vision model, converting confirmed copies into retroactive license invoices), PayPal webhooks, AG Grid artist dashboard, OpenAPI-generated SDK.

License: MIT
