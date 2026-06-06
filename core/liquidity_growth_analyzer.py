# core/liquidity_growth_analyzer.py
"""
Phase 2.2 — Liquidity Growth Analyzer

Fetches the first N one-minute OHLCV candles for a newly launched token and
classifies the liquidity growth pattern as organic or artificial.

Growth patterns
---------------
ORGANIC          — Volume grows gradually, price is stable or rising.
                   Score: 70–100
CONSISTENT       — Volume is steady (low variance). Healthy for new tokens.
                   Score: 60–80
ARTIFICIAL_SPIKE — First candle volume >> subsequent candles (>40% of total
                   volume in candle[0]).  Classic pump-then-dump signature.
                   Score: 10–30
FLATLINE         — Volume is very low and constant after the initial candle.
                   Score: 35–55
DECLINING        — Volume consistently falls over time.
                   Score: 25–45
INSUFFICIENT_DATA — Fewer than 2 candles available (token too new).
                   Score: 50 (neutral)

Data source
-----------
Primary:  Birdeye OHLCV API  (requires BIRDEYE_API_KEY)
Fallback: DexScreener pairs API (free, no key required)
"""

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OHLCV_CANDLES:            int   = 10      # how many 1-minute candles to fetch
MIN_CANDLES_FOR_ANALYSIS: int   = 2
SPIKE_THRESHOLD:          float = 0.40    # first candle > 40% of total vol
DECLINE_THRESHOLD:        float = 0.50    # last candle < 50% of first candle
CONSISTENCY_CV_MAX:       float = 0.40    # coefficient of variation ≤ 40% = consistent


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LiquidityCandle:
    timestamp: int
    open:  float
    high:  float
    low:   float
    close: float
    volume: float


@dataclass
class LiquidityGrowthResult:
    token_address:          str
    candles:                list  = field(default_factory=list)
    candle_count:           int   = 0
    total_volume:           float = 0.0
    first_candle_vol_pct:   float = 0.0   # first candle as % of total volume
    volume_cv:              float = 0.0   # coefficient of variation
    growth_pattern:         str   = "INSUFFICIENT_DATA"
    growth_rate_pct:        float = 0.0   # (last_close - first_open) / first_open * 100
    liquidity_growth_score: float = 50.0  # neutral default


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class LiquidityGrowthAnalyzer:
    """
    Analyses liquidity/volume growth patterns from OHLCV candle data.

    All public methods are exception-safe — they always return a valid
    ``LiquidityGrowthResult`` even when every API call fails.
    """

    def __init__(self) -> None:
        self._birdeye_key = getattr(settings, "BIRDEYE_API_KEY", "")

    def _has_birdeye(self) -> bool:
        return bool(
            self._birdeye_key
            and not self._birdeye_key.startswith("your_")
        )

    # ------------------------------------------------------------------
    # Data fetchers
    # ------------------------------------------------------------------

    async def _fetch_birdeye_ohlcv(self, token_address: str) -> list[LiquidityCandle]:
        """Fetch 1-minute OHLCV candles from Birdeye."""
        url = (
            "https://public-api.birdeye.so/defi/ohlcv"
            f"?address={token_address}&type=1m&limit={OHLCV_CANDLES}"
        )
        headers = {"x-chain": "solana", "X-API-KEY": self._birdeye_key}
        try:
            async with httpx.AsyncClient(timeout=7.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 429:
                    logger.warning("Birdeye OHLCV rate-limited.")
                    return []
                if resp.status_code != 200:
                    logger.warning(f"Birdeye OHLCV HTTP {resp.status_code}")
                    return []
                data = resp.json()
                items: list[dict] = (
                    data.get("data", {}).get("items", [])
                    if isinstance(data.get("data"), dict)
                    else []
                )
                candles = []
                for item in items:
                    try:
                        candles.append(LiquidityCandle(
                            timestamp=int(item.get("unixTime", 0)),
                            open=float(item.get("o", 0) or 0),
                            high=float(item.get("h", 0) or 0),
                            low=float(item.get("l", 0) or 0),
                            close=float(item.get("c", 0) or 0),
                            volume=float(item.get("v", 0) or 0),
                        ))
                    except (TypeError, ValueError):
                        continue
                logger.debug(f"Birdeye returned {len(candles)} candles for {token_address[:12]}...")
                return candles
        except Exception as exc:
            logger.error(f"_fetch_birdeye_ohlcv error: {exc}")
        return []

    async def _fetch_dexscreener_ohlcv(self, token_address: str) -> list[LiquidityCandle]:
        """
        Approximate volume history from DexScreener pair data.
        DexScreener doesn't offer candle-level data on the free API, so we
        synthesise a single candle from the pair's current liquidity and
        24h volume for comparison context.
        """
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                data    = resp.json()
                pairs: list[dict] = data.get("pairs") or []
                # Use the pair with the highest liquidity as the primary
                pairs = sorted(
                    [p for p in pairs if p.get("chainId") == "solana"],
                    key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0),
                    reverse=True,
                )
                if not pairs:
                    return []
                p   = pairs[0]
                vol = float((p.get("volume") or {}).get("h1", 0) or 0)
                price_usd = float(p.get("priceUsd") or 0)
                if vol > 0 and price_usd > 0:
                    # Return a pseudo-candle representing the last hour
                    return [LiquidityCandle(
                        timestamp=0,
                        open=price_usd, high=price_usd, low=price_usd,
                        close=price_usd, volume=vol,
                    )]
        except Exception as exc:
            logger.error(f"_fetch_dexscreener_ohlcv error: {exc}")
        return []

    # ------------------------------------------------------------------
    # Pattern classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_pattern(
        volumes: list[float],
        first_vol_pct: float,
        cv: float,
    ) -> str:
        """Classify volume growth pattern from candle data."""
        if len(volumes) < MIN_CANDLES_FOR_ANALYSIS:
            return "INSUFFICIENT_DATA"

        if first_vol_pct > SPIKE_THRESHOLD:
            return "ARTIFICIAL_SPIKE"

        last_vol  = volumes[-1]
        first_vol = volumes[0] if volumes[0] > 0 else 1.0
        decline   = last_vol < first_vol * (1 - DECLINE_THRESHOLD)

        if decline:
            return "DECLINING"

        if cv <= CONSISTENCY_CV_MAX:
            # Low variance — check if overall trend is up
            mid_vol = statistics.median(volumes)
            if last_vol >= mid_vol:
                return "ORGANIC"
            return "CONSISTENT"

        # High variance but no initial spike → organic volatility
        if last_vol > first_vol:
            return "ORGANIC"
        return "FLATLINE"

    @staticmethod
    def _score_pattern(pattern: str, cv: float, first_vol_pct: float) -> float:
        """Convert growth pattern to a 0–100 score."""
        base_scores = {
            "ORGANIC":           85.0,
            "CONSISTENT":        70.0,
            "FLATLINE":          45.0,
            "DECLINING":         35.0,
            "ARTIFICIAL_SPIKE":  15.0,
            "INSUFFICIENT_DATA": 50.0,
        }
        score = base_scores.get(pattern, 50.0)

        # Fine-tune for ARTIFICIAL_SPIKE severity
        if pattern == "ARTIFICIAL_SPIKE":
            # The more concentrated the first candle, the worse it is
            excess = (first_vol_pct - SPIKE_THRESHOLD) / (1.0 - SPIKE_THRESHOLD)
            score = max(0.0, score - excess * 10.0)

        # Organic bonus for very consistent volume
        if pattern in ("ORGANIC", "CONSISTENT") and cv < 0.20:
            score = min(100.0, score + 10.0)

        return round(score, 2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, token_address: str) -> LiquidityGrowthResult:
        """
        Analyse the liquidity growth pattern for *token_address*.

        Always returns a valid ``LiquidityGrowthResult`` — never raises.
        """
        _safe = LiquidityGrowthResult(token_address=token_address)
        try:
            # Fetch candles — Birdeye if configured, otherwise DexScreener
            if self._has_birdeye():
                candles = await self._fetch_birdeye_ohlcv(token_address)
            else:
                candles = []

            if not candles:
                candles = await self._fetch_dexscreener_ohlcv(token_address)

            if not candles:
                logger.debug(
                    f"LiquidityGrowthAnalyzer: no candles for {token_address[:12]}..."
                )
                return _safe

            # Sort oldest → newest
            candles.sort(key=lambda c: c.timestamp)

            volumes     = [c.volume for c in candles]
            total_vol   = sum(volumes)
            first_vol   = volumes[0] if volumes else 0.0
            first_pct   = first_vol / total_vol if total_vol > 0 else 0.0
            cv          = (
                statistics.stdev(volumes) / statistics.mean(volumes)
                if len(volumes) >= 2 and statistics.mean(volumes) > 0
                else 0.0
            )

            pattern = self._classify_pattern(volumes, first_pct, cv)
            score   = self._score_pattern(pattern, cv, first_pct)

            # Growth rate: (last_close - first_open) / first_open * 100
            growth_rate = 0.0
            if candles[0].open > 0:
                growth_rate = (
                    (candles[-1].close - candles[0].open) / candles[0].open * 100.0
                )

            logger.info(
                f"LiquidityGrowth {token_address[:12]}...: "
                f"candles={len(candles)}, pattern={pattern}, "
                f"firstPct={first_pct:.0%}, cv={cv:.2f}, score={score:.1f}"
            )
            return LiquidityGrowthResult(
                token_address=token_address,
                candles=candles,
                candle_count=len(candles),
                total_volume=round(total_vol, 4),
                first_candle_vol_pct=round(first_pct, 4),
                volume_cv=round(cv, 4),
                growth_pattern=pattern,
                growth_rate_pct=round(growth_rate, 2),
                liquidity_growth_score=score,
            )

        except Exception as exc:
            logger.error(f"LiquidityGrowthAnalyzer.analyze error: {exc}")
            return _safe
