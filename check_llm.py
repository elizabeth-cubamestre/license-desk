"""Lists the models your LLM key can use, then tests one call. Run: python check_llm.py"""
import os
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()
c = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
try:
    names = [m.id for m in c.models.list()]
    print("Models available to your key:\n ", "\n  ".join(names))
except Exception as e:
    print("Could not list models:", type(e).__name__, str(e)[:300])
m = os.environ.get("LLM_MODEL", "")
try:
    r = c.chat.completions.create(model=m, messages=[{"role": "user", "content": "Say OK"}], max_tokens=10)
    print(f"\nSUCCESS with LLM_MODEL={m!r}:", r.choices[0].message.content)
except Exception as e:
    print(f"\nFAILED with LLM_MODEL={m!r}:", type(e).__name__, str(e)[:300])
