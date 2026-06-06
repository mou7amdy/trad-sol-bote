# core/rug_detector.py
"""
Enhancement 3 — Rug Pull Pattern Recognition

Combines on-chain holder concentration, price-history volatility (used as a
liquidity-drop proxy), and token age vs. liquidity ratio into a probabilistic
rug score.

NOTE on liquidity proxy
-----------------------
Birdeye's ``defi/history_price`` (5 m candles) is used to detect sudden price
collapses as a *proxy* for liquidity removal events, because the liquidity
time-series endpoint requires a paid Birdeye tier.  A >20 % price drop in a
single 5-minute candle is flagged as a suspicious drop.  This produces some
false positives on high-volatility tokens, but errs on the safe side.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from loguru import logger

from config.settings import settings


@dataclass
class RugAnalysis:
    token_address: str
    rug_probability: float            # 0.0 – 1.0
    risk_flags: list[str] = field(default_factory=list)  # triggered red flags
    pattern_score: float = 100.0     # 0–100 (lower = more suspicious)
    recommendation: str = "SAFE"     # "SAFE" | "CAUTION" | "AVOID" | "RUG"


class RugDetector:
    """
    Analyses a Solana token for rug-pull patterns using Birdeye and Helius.

    Every individual check method is fully exception-safe and returns a safe
    default dict on failure so the master ``analyze_rug_risk`` always has data
    to aggregate, even if every API call fails.
    """

    def __init__(self) -> None:
        self._rpc_url: str = settings.SOLANA_RPC_URL
        self._birdeye_key: str = getattr(settings, "BIRDEYE_API_KEY", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _birdeye_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"x-chain": "solana"}
        if self._birdeye_key and not self._birdeye_key.startswith("your_"):
            headers["X-API-KEY"] = self._birdeye_key
        return headers

    async def _post_rpc(self, payload: dict) -> Optional[Any]:
        """POST a JSON-RPC request and return the ``result`` field."""
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        logger.warning(
                            f"RPC error [{payload.get('method')}]: {data['error']}"
                        )
                        return None
                    return data.get("result")
                logger.warning(
                    f"HTTP {resp.status_code} from RPC [{payload.get('method')}]"
                )
        except Exception as exc:
            logger.error(f"RugDetector RPC error [{payload.get('method')}]: {exc}")
        return None

    async def _get_birdeye(self, url: str) -> Optional[dict]:
        """GET a Birdeye endpoint and return the ``data`` field."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._birdeye_headers())
                if resp.status_code == 200:
                    return resp.json().get("data")
                logger.warning(f"HTTP {resp.status_code} from Birdeye [{url}]")
        except Exception as exc:
            logger.error(f"RugDetector Birdeye GET error: {exc}")
        return None

    # ------------------------------------------------------------------
    # Check 1 — Price-drop proxy for liquidity removal
    # ------------------------------------------------------------------

    async def check_liquidity_removal_pattern(self, token_address: str) -> dict:
        """
        Fetch 5-minute price candles from Birdeye and detect sudden drops
        of more than 20 % between consecutive candles.

        A large price collapse is used as a proxy for liquidity removal because
        the Birdeye free tier does not expose a liquidity time-series endpoint.

        Returns::

            {"suspicious_drops": int, "max_drop_pct": float}
        """
        _safe = {"suspicious_drops": 0, "max_drop_pct": 0.0}
        url = (
            f"https://public-api.birdeye.so/defi/history_price"
            f"?address={token_address}&address_type=token&type=5m"
        )
        try:
            data = await self._get_birdeye(url)
            if not data:
                return _safe

            items: list[dict] = data.get("items", [])
            if len(items) < 2:
                return _safe

            values: list[float] = [
                float(item.get("value", 0.0)) for item in items
            ]
            suspicious_drops = 0
            max_drop_pct = 0.0

            for i in range(1, len(values)):
                prev = values[i - 1]
                curr = values[i]
                if prev <= 0.0:
                    continue
                drop_pct = ((prev - curr) / prev) * 100.0
                if drop_pct > 20.0:
                    suspicious_drops += 1
                    if drop_pct > max_drop_pct:
                        max_drop_pct = drop_pct

            logger.debug(
                f"Price-drop proxy for {token_address}: "
                f"drops={suspicious_drops}, max_drop={max_drop_pct:.1f}%"
            )
            return {
                "suspicious_drops": suspicious_drops,
                "max_drop_pct": round(max_drop_pct, 2),
            }
        except Exception as exc:
            logger.error(f"check_liquidity_removal_pattern error: {exc}")
            return _safe

    # ------------------------------------------------------------------
    # Check 2 — Token distribution (holder concentration)
    # ------------------------------------------------------------------

    async def check_token_distribution(self, token_address: str) -> dict:
        """
        Fetch the token's largest accounts via Helius ``getTokenLargestAccounts``
        and calculate top-1 and top-3 holder concentration percentages.

        Returns::

            {"top1_pct": float, "top3_pct": float, "distribution_score": float}
        """
        _safe = {"top1_pct": 0.0, "top3_pct": 0.0, "distribution_score": 100.0}

        largest_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_address],
        }
        supply_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getTokenSupply",
            "params": [token_address],
        }

        try:
            largest_result, supply_result = await asyncio.gather(
                self._post_rpc(largest_payload),
                self._post_rpc(supply_payload),
            )

            if not largest_result or not supply_result:
                return _safe

            accounts: list[dict] = largest_result.get("value", [])
            # getTokenSupply returns {"value": {"amount": "...", ...}}
            total_supply_str: str = (
                supply_result.get("value", {}).get("amount", "1") or "1"
            )
            total_supply = float(total_supply_str)
            if total_supply <= 0.0:
                total_supply = 1.0

            # Sort descending by raw token amount
            sorted_accts = sorted(
                accounts,
                key=lambda a: float(a.get("amount", 0)),
                reverse=True,
            )

            top1_amount = float(sorted_accts[0].get("amount", 0)) if sorted_accts else 0.0
            top3_amount = sum(
                float(a.get("amount", 0)) for a in sorted_accts[:3]
            )

            top1_pct = round((top1_amount / total_supply) * 100.0, 2)
            top3_pct = round((top3_amount / total_supply) * 100.0, 2)

            # Distribution score: 100 = perfectly spread, 0 = extreme concentration
            distribution_score = max(
                0.0,
                100.0
                - top1_pct * 1.5
                - max(0.0, top3_pct - top1_pct) * 0.5,
            )

            logger.debug(
                f"Token distribution for {token_address}: "
                f"top1={top1_pct:.1f}%, top3={top3_pct:.1f}%, "
                f"dist_score={distribution_score:.1f}"
            )
            return {
                "top1_pct": top1_pct,
                "top3_pct": top3_pct,
                "distribution_score": round(distribution_score, 2),
            }

        except Exception as exc:
            logger.error(f"check_token_distribution error: {exc}")
            return _safe

    # ------------------------------------------------------------------
    # Check 3 — Token age vs. current liquidity
    # ------------------------------------------------------------------

    async def check_contract_age_vs_liquidity(
        self, token_address: str, liquidity_usd: float
    ) -> dict:
        """
        Detect artificial pump patterns: a very new token (< 60 minutes old)
        paired with unusually high liquidity (> $100 k) is flagged as
        suspicious — this pattern is consistent with wash-trading or pre-seeded
        liquidity designed to attract buyers before a rug.

        Returns::

            {"token_age_minutes": int, "liquidity_ratio": float, "suspicious": bool}
        """
        _safe = {"token_age_minutes": 0, "liquidity_ratio": 0.0, "suspicious": False}

        sig_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 1000, "commitment": "finalized"}],
        }
        try:
            result = await self._post_rpc(sig_payload)
            if not result or not isinstance(result, list):
                return _safe

            # Newest-first → oldest is the last element
            oldest = result[-1]
            block_time: Optional[int] = oldest.get("blockTime")
            if not block_time:
                return _safe

            token_age_seconds = time.time() - block_time
            token_age_minutes = max(1, int(token_age_seconds / 60))

            # USD of liquidity per minute of the token's existence
            liquidity_ratio = round(liquidity_usd / token_age_minutes, 2)

            # Suspicious: < 1 hour old AND > $100 k liquidity
            suspicious = token_age_minutes < 60 and liquidity_usd > 100_000.0

            logger.debug(
                f"Age vs liquidity for {token_address}: "
                f"age={token_age_minutes}min, liq=${liquidity_usd:,.0f}, "
                f"ratio={liquidity_ratio:.1f}, suspicious={suspicious}"
            )
            return {
                "token_age_minutes": token_age_minutes,
                "liquidity_ratio": liquidity_ratio,
                "suspicious": suspicious,
            }

        except Exception as exc:
            logger.error(f"check_contract_age_vs_liquidity error: {exc}")
            return _safe

    # ------------------------------------------------------------------
    # Master aggregator
    # ------------------------------------------------------------------

    async def analyze_rug_risk(
        self, token_address: str, token_info: Any  # TokenInfo
    ) -> RugAnalysis:
        """
        Run all 3 rug-pull checks concurrently via ``asyncio.gather`` and
        aggregate the results into a single ``RugAnalysis``.

        Always returns a valid ``RugAnalysis`` — never raises.
        """
        liquidity_usd: float = float(getattr(token_info, "liquidity_usd", 0.0))

        try:
            liq_data, dist_data, age_data = await asyncio.gather(
                self.check_liquidity_removal_pattern(token_address),
                self.check_token_distribution(token_address),
                self.check_contract_age_vs_liquidity(token_address, liquidity_usd),
            )
        except Exception as exc:
            logger.error(f"analyze_rug_risk gather failed for {token_address}: {exc}")
            return RugAnalysis(
                token_address=token_address,
                rug_probability=0.5,
                risk_flags=["Analysis failed — result uncertain"],
                pattern_score=50.0,
                recommendation="CAUTION",
            )

        # ── Aggregate scoring ──────────────────────────────────────────
        pattern_score: float = 100.0
        risk_flags: list[str] = []

        # Distribution checks
        top1_pct: float = dist_data.get("top1_pct", 0.0)
        top3_pct: float = dist_data.get("top3_pct", 0.0)

        if top1_pct > 50.0:
            risk_flags.append(f"Top holder owns {top1_pct:.1f}% (>50%)")
            pattern_score -= 35.0
        elif top1_pct > 30.0:
            risk_flags.append(f"Top holder owns {top1_pct:.1f}% (>30%)")
            pattern_score -= 15.0

        if top3_pct > 80.0:
            risk_flags.append(f"Top 3 holders own {top3_pct:.1f}% (>80%)")
            pattern_score -= 25.0

        # Price-drop / liquidity proxy checks
        suspicious_drops: int = liq_data.get("suspicious_drops", 0)
        max_drop_pct: float = liq_data.get("max_drop_pct", 0.0)

        if suspicious_drops > 2:
            risk_flags.append(
                f"Multiple sudden price drops detected "
                f"({suspicious_drops} drops, max {max_drop_pct:.1f}%)"
            )
            pattern_score -= 30.0
        elif suspicious_drops > 0:
            risk_flags.append(
                f"Sudden price drop detected ({max_drop_pct:.1f}%)"
            )
            pattern_score -= 15.0

        # Age vs. liquidity
        if age_data.get("suspicious"):
            age_min = age_data.get("token_age_minutes", 0)
            risk_flags.append(
                f"New token ({age_min}min old) with unusually high liquidity"
            )
            pattern_score -= 20.0

        pattern_score = max(0.0, pattern_score)
        rug_probability = round((100.0 - pattern_score) / 100.0, 4)

        if rug_probability < 0.2:
            recommendation = "SAFE"
        elif rug_probability < 0.4:
            recommendation = "CAUTION"
        elif rug_probability < 0.7:
            recommendation = "AVOID"
        else:
            recommendation = "RUG"

        logger.info(
            f"RugAnalysis for {token_address}: "
            f"prob={rug_probability:.2f}, score={pattern_score:.1f}, "
            f"verdict={recommendation}, flags={risk_flags}"
        )

        return RugAnalysis(
            token_address=token_address,
            rug_probability=rug_probability,
            risk_flags=risk_flags,
            pattern_score=pattern_score,
            recommendation=recommendation,
        )
