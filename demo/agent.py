"""Live buyer agent. An LLM decides which License Desk tools to call (over MCP), under a budget enforced in code.
Run from the project folder:  python demo/agent.py "I need a cat character for a 6 month regional ad" --budget 80"""
import argparse, asyncio, json, os, sys, time, webbrowser
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
p = argparse.ArgumentParser()
p.add_argument("task"); p.add_argument("--budget", type=float, required=True); p.add_argument("--max-steps", type=int, default=14)
a = p.parse_args()
llm = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"], timeout=60, max_retries=0)
MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
SYSTEM = f"""You are a purchasing agent that licenses artwork for a client. Budget: ${a.budget:.2f} maximum.
Steps: search_licenses to find suitable work; get_quote with the client's plain-language use; if the price fits the budget,
purchase_license with exactly the quoted artwork_id, tier and months; then complete_purchase with the order_id (a human
approves the payment in PayPal, the tool waits); then verify_receipt. If a work is refused or too expensive, try another
or stop and explain. Never exceed the budget. Finish with a short plain-English summary of what you did and paid."""

def text(res): return "\n".join(c.text for c in res.content if hasattr(c, "text"))

def chat(msgs, tools):
    for _ in range(4):
        try: return llm.chat.completions.create(model=MODEL, messages=msgs, tools=tools, temperature=0).choices[0].message
        except RateLimitError: print("  (LLM rate limited, waiting 20s)"); time.sleep(20)
    sys.exit("LLM rate limit. Try again in a few minutes.")

async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")], env=dict(os.environ), cwd=str(ROOT))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
                 for t in (await s.list_tools()).tools]
        quotes, refused = {}, set()

        async def call(name, args):
            if name == "purchase_license":  # hard guardrails live in code, not in the prompt
                key = (args.get("artwork_id"), args.get("tier"), args.get("months"))
                if key in refused: return "BLOCKED: the artist's rules forbid this use."
                if key not in quotes: return "BLOCKED: call get_quote for exactly this artwork, tier and months first."
                if quotes[key] > a.budget: return f"BLOCKED by budget guard: ${quotes[key]:.2f} exceeds the ${a.budget:.2f} limit."
            if name == "complete_purchase":  # wait for the human to approve in PayPal
                for _ in range(60):
                    out = text(await s.call_tool(name, args))
                    if "COMPLETED" in out: return out
                    await asyncio.sleep(3)
                return "Timed out waiting for the human to approve payment."
            out = text(await s.call_tool(name, args))
            try:
                d = json.loads(out)
                if name == "get_quote":
                    k = (d["artwork_id"], d["tier"], d["months"])
                    (refused.add(k) if d.get("refused") else quotes.__setitem__(k, d["amount"]))
                if name == "purchase_license":
                    print(f"\n  >> HUMAN APPROVAL NEEDED. Opening PayPal sandbox:\n     {d['approve_url']}\n"); webbrowser.open(d["approve_url"])
            except Exception: pass
            return out

        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": a.task}]
        for _ in range(a.max_steps):
            m = chat(msgs, tools)
            if not m.tool_calls:
                print("\nAGENT SUMMARY:", m.content); return
            msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [t.model_dump(exclude_none=True) for t in m.tool_calls]})
            for t in m.tool_calls:
                args = json.loads(t.function.arguments or "{}")
                print(f"AGENT -> {t.function.name}({json.dumps(args)})")
                out = await call(t.function.name, args)
                print("   <-", out[:260].replace("\n", " "))
                msgs.append({"role": "tool", "tool_call_id": t.id, "content": out})
        print("Stopped: step limit reached.")

asyncio.run(main())
