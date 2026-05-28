"""
Finance tool — portfolio tracking, market signals, spending alerts.
Primary: Alpaca (market data + portfolio)
Watchlist and alert thresholds configured in config.yaml
"""
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from tools.registry import ToolBase


class FinanceTool(ToolBase):
    name = "finance_read"
    description = (
        "Get portfolio value, positions, market data, and financial alerts. "
        "Check stock prices, portfolio P&L, set price alerts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["portfolio", "price", "news", "pulse"],
                "description": "Action: portfolio=full holdings, price=single ticker, news=financial news, pulse=brief market summary",
            },
            "symbol": {
                "type": "string",
                "description": "Ticker symbol for price action (e.g. AAPL, BTC/USD)",
            },
        },
        "required": ["action"],
    }

    def __init__(self, alpaca_key: str = "", alpaca_secret: str = ""):
        self._key = alpaca_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret = alpaca_secret or os.environ.get("ALPACA_SECRET_KEY", "")
        self._base = "https://paper-api.alpaca.markets"  # swap to live when ready

    @property
    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
        }

    async def run(self, action: str, symbol: Optional[str] = None) -> str:
        if not self._key:
            return "[finance] Alpaca API key not configured"

        if action == "portfolio":
            return await self._portfolio()
        if action == "price" and symbol:
            return await self._price(symbol)
        if action == "news":
            return await self._news(symbol)
        if action == "pulse":
            return await self.get_pulse()
        return "Specify action: portfolio | price <symbol> | news | pulse"

    async def _portfolio(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            acct_resp = await client.get(f"{self._base}/v2/account", headers=self._headers)
            pos_resp = await client.get(f"{self._base}/v2/positions", headers=self._headers)

        if acct_resp.status_code != 200:
            return f"Error fetching account: {acct_resp.status_code}"

        acct = acct_resp.json()
        equity = float(acct.get("equity", 0))
        pl = float(acct.get("unrealized_pl", 0))
        pl_pct = (pl / (equity - pl) * 100) if equity != pl else 0

        lines = [
            f"Portfolio: ${equity:,.2f} | P&L: ${pl:+,.2f} ({pl_pct:+.1f}%)",
        ]

        if pos_resp.status_code == 200:
            positions = pos_resp.json()
            for p in sorted(positions, key=lambda x: abs(float(x.get("unrealized_pl", 0))), reverse=True)[:10]:
                sym = p.get("symbol", "?")
                qty = p.get("qty", "?")
                pl_pos = float(p.get("unrealized_pl", 0))
                pct = float(p.get("unrealized_plpc", 0)) * 100
                lines.append(f"  {sym}: {qty} shares | {pl_pos:+,.2f} ({pct:+.1f}%)")

        return "\n".join(lines)

    async def _price(self, symbol: str) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
                headers=self._headers,
            )
        if resp.status_code == 200:
            trade = resp.json().get("trade", {})
            price = trade.get("p", "?")
            return f"{symbol}: ${price}"
        return f"Could not fetch price for {symbol}: {resp.status_code}"

    async def _news(self, symbol: Optional[str]) -> str:
        params = {"limit": 5}
        if symbol:
            params["symbols"] = symbol
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://data.alpaca.markets/v1beta1/news",
                headers=self._headers,
                params=params,
            )
        if resp.status_code == 200:
            articles = resp.json().get("news", [])
            lines = []
            for a in articles:
                lines.append(f"[{a.get('created_at', '')[:10]}] {a.get('headline', '')}")
            return "\n".join(lines) or "No news found."
        return f"Error {resp.status_code}"

    async def get_pulse(self) -> str:
        """Brief market summary for context injection."""
        if not self._key:
            return ""
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(f"{self._base}/v2/account", headers=self._headers)
            if resp.status_code == 200:
                acct = resp.json()
                equity = float(acct.get("equity", 0))
                pl = float(acct.get("unrealized_pl", 0))
                direction = "▲" if pl >= 0 else "▼"
                return f"Portfolio ${equity:,.0f} {direction}{abs(pl):,.0f} today"
        except Exception:
            pass
        return ""

    async def get_alerts(self) -> list[dict]:
        """Called by anticipator for significant price/portfolio moves."""
        if not self._key:
            return []
        alerts = []
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                pos_resp = await client.get(f"{self._base}/v2/positions", headers=self._headers)
            if pos_resp.status_code == 200:
                for p in pos_resp.json():
                    pct = abs(float(p.get("unrealized_intraday_plpc", 0))) * 100
                    if pct >= 5:
                        sym = p.get("symbol", "?")
                        direction = "▲" if float(p.get("unrealized_intraday_plpc", 0)) > 0 else "▼"
                        alerts.append({
                            "symbol": sym,
                            "priority": "high" if pct >= 8 else "medium",
                            "message": f"{sym} {direction}{pct:.1f}% today",
                            "action": "Review position",
                        })
        except Exception:
            pass
        return alerts
