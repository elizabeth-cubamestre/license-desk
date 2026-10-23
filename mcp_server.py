"""MCP server so any MCP-capable AI agent can discover, quote, buy and verify licenses.
Run: python mcp_server.py   (stdio transport)"""
from mcp.server.fastmcp import FastMCP
import core

core.init()
mcp = FastMCP("license-desk")

@mcp.tool()
def search_licenses(query: str = "") -> list:
    """Find artworks available for licensing."""
    return core.list_artworks(query)

@mcp.tool()
def get_quote(artwork_id: str, intent: str) -> dict:
    """Describe how you want to use the work in plain language; returns tier, duration and exact USD price."""
    return core.quote(artwork_id, intent)

@mcp.tool()
def purchase_license(artwork_id: str, tier: str, months: int) -> dict:
    """Create a PayPal order. The human buyer must open approve_url and approve before complete_purchase."""
    return core.create_order(artwork_id, tier, months)

@mcp.tool()
def complete_purchase(order_id: str) -> dict:
    """Capture the approved PayPal order and return the signed license receipt."""
    return core.capture_order(order_id)

@mcp.tool()
def verify_receipt(receipt: str) -> dict:
    """Check that a license receipt is authentic and unexpired."""
    return core.verify(receipt)

if __name__ == "__main__":
    mcp.run()
