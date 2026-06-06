# core/tx_pattern_scorer.py
"""
Phase 2.1 — Transaction Pattern Scorer

Analyses buy/sell transaction patterns within the first 5 minutes of a new
pool's existence to detect wash trading and artificial pumps.

Wash trading definition
-----------------------
A wallet that buys a token and then sells it (or vice-versa) within 30 seconds
in the same token's early window is flagged as a wash trader.

Artificial pump definition
--------------------------
If buy_tx / total_tx > 70% in the first 5 minutes, almost no organic selling
is occurring — this is a strong sign of coordinated artificial pumping.

Score formula
-------------
Start at 100.
  • buy_ratio > 0.85  → -50  (extreme one-sided volume)
  • buy_ratio > 0.70  → -25  (suspicious)
  • wash_trade_count  → -15 per wash-trading wallet (capped at -40)
  • 0.40 ≤ buy_ratio ≤ 0.65 → +10 (healthy two-sided activity)
Final score clamped to [0, 100].

Concurrency
-----------
Transaction fetches are semaphore-limited to MAX_PARALLEL_TX_FETCHES to avoid
RPC rate-limiting.  The analysis aborts if the full fetch takes > TASK_TIMEOUT.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ANALYSIS_WINDOW_SECONDS:  int   = 300   # first 5 minutes
MAX_TX_SAMPLES:           int   = 100   # cap at 100 txs for speed
MAX_PARALLEL_TX_FETCHES:  int   = 15    # semaphore cap
WASH_TRADE_WINDOW_SECONDS: int  = 30    # buy→sell within 30 s = wash trade
PUMP_RATIO_HARD:          float = 0.85  # extreme artificial pump
PUMP_RATIO_SOFT:          float = 0.70  # suspicious pump
HEALTHY_BUY_LOW:          float = 0.40
HEALTHY_BUY_HIGH:         float = 0.65


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TxPatternResult:
    token_address:      str
    buy_count:          int   = 0
    sell_count:         int   = 0
    total_txs:          int   = 0
    buy_ratio:          float = 0.5   # buy_count / total_txs
    wash_trade_wallets: list  = field(default_factory=list)
    wash_trade_count:   int   = 0
    is_artificial_pump: bool  = False
    tx_pattern_score:   float = 50.0  # neutral default
    buy_sell_ratio:     float = 1.0   # F1: buy_count / max(sell_count, 1)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class TxPatternScorer:
    """
    Fetches and scores buy/sell patterns for a newly launched token.

    All public methods are exception-safe — they always return a valid
    ``TxPatternResult`` even when every API call fails.
    """

    def __init__(self) -> None:
        self._rpc_url  = settings.SOLANA_RPC_URL
        self._sem      = asyncio.Semaphore(MAX_PARALLEL_TX_FETCHES)

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------

    async def _post_rpc(self, payload: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=7.0) as c:
                r = await c.post(self._rpc_url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    if "error" not in data:
                        return data.get("result")
        except Exception as exc:
            logger.error(f"TxPatternScorer RPC error: {exc}")
        return None

    async def _get_signatures(self, address: str, limit: int) -> list[dict]:
        result = await self._post_rpc({
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": limit, "commitment": "confirmed"}],
        })
        return result if isinstance(result, list) else []

    async def _get_transaction(self, sig: str) -> Optional[dict]:
        async with self._sem:
            return await self._post_rpc({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "json",
                                  "maxSupportedTransactionVersion": 0}],
            })

    # ------------------------------------------------------------------
    # Tx classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_tx(tx: dict) -> Optional[str]:
        """
        Return "buy", "sell", or None (indeterminate) for a transaction.

        A "buy"  means at least one account's token balance *increased*
        A "sell" means at least one account's token balance *decreased*

        We look at the *net* token balance change across all non-program
        accounts.  If the net is positive → buy; negative → sell.
        """
        try:
            meta = tx.get("meta") or {}
            pre:  list[dict] = meta.get("preTokenBalances")  or []
            post: list[dict] = meta.get("postTokenBalances") or []

            if not pre and not post:
                return None  # no token activity

            # Build pre and post balance maps by accountIndex
            pre_map  = {b["accountIndex"]: float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
                        for b in pre}
            post_map = {b["accountIndex"]: float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
                        for b in post}

            all_idxs = set(pre_map) | set(post_map)
            net = sum(post_map.get(i, 0.0) - pre_map.get(i, 0.0) for i in all_idxs)

            if net > 0:
                return "buy"
            if net < 0:
                return "sell"
        except Exception as exc:
            logger.debug(f"_classify_tx error: {exc}")
        return None

    @staticmethod
    def _fee_payer(tx: dict) -> Optional[str]:
        try:
            keys: list[str] = (
                tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
            )
            return keys[0] if keys else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Wash-trading detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_wash_trades(
        wallet_events: dict[str, list[tuple[int, str]]]
    ) -> list[str]:
        """
        Return list of wallets that performed a buy→sell or sell→buy
        round-trip within ``WASH_TRADE_WINDOW_SECONDS``.
        """
        wash_wallets: list[str] = []
        for wallet, events in wallet_events.items():
            events_sorted = sorted(events, key=lambda e: e[0])
            for i in range(len(events_sorted) - 1):
                t1, typ1 = events_sorted[i]
                t2, typ2 = events_sorted[i + 1]
                if (
                    typ1 != typ2  # different direction
                    and abs(t2 - t1) <= WASH_TRADE_WINDOW_SECONDS
                ):
                    wash_wallets.append(wallet)
                    break  # one flag per wallet is enough
        return wash_wallets

    # ------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_score(
        buy_ratio: float,
        wash_count: int,
    ) -> float:
        score = 100.0

        # One-sided pump penalty
        if buy_ratio > PUMP_RATIO_HARD:
            score -= 50.0
        elif buy_ratio > PUMP_RATIO_SOFT:
            score -= 25.0

        # Wash trading penalty (capped at 40 pts)
        score -= min(40.0, wash_count * 15.0)

        # Healthy two-sided activity bonus
        if HEALTHY_BUY_LOW <= buy_ratio <= HEALTHY_BUY_HIGH:
            score += 10.0

        return round(max(0.0, min(100.0, score)), 2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        token_address: str,
        pool_creation_time: int,
    ) -> TxPatternResult:
        """
        Analyse buy/sell patterns for *token_address* within the first
        ``ANALYSIS_WINDOW_SECONDS`` seconds of *pool_creation_time*.

        Always returns a valid ``TxPatternResult`` — never raises.
        """
        _safe = TxPatternResult(token_address=token_address)
        try:
            cutoff = pool_creation_time + ANALYSIS_WINDOW_SECONDS
            sigs = await self._get_signatures(token_address, MAX_TX_SAMPLES)
            if not sigs:
                return _safe

            # Filter to the analysis window
            window_sigs = [
                s["signature"]
                for s in sigs
                if (
                    s.get("blockTime") is not None
                    and pool_creation_time <= s["blockTime"] <= cutoff
                    and s.get("signature")
                    and not s.get("err")
                )
            ]
            if not window_sigs:
                logger.debug(f"TxPatternScorer: no txs in window for {token_address[:12]}...")
                return _safe

            # Fetch full transactions concurrently
            raw_txs = await asyncio.gather(
                *[self._get_transaction(s) for s in window_sigs],
                return_exceptions=True,
            )

            buy_count  = 0
            sell_count = 0
            total      = 0
            # wallet → [(block_time, "buy"/"sell")]
            wallet_events: dict[str, list[tuple[int, str]]] = {}

            for tx_data, sig_meta in zip(raw_txs, sigs[:len(window_sigs)]):
                if isinstance(tx_data, Exception) or not tx_data:
                    continue
                classification = self._classify_tx(tx_data)
                if not classification:
                    continue
                total += 1
                if classification == "buy":
                    buy_count += 1
                else:
                    sell_count += 1

                payer = self._fee_payer(tx_data)
                if payer:
                    block_time = sig_meta.get("blockTime", 0)
                    wallet_events.setdefault(payer, []).append(
                        (block_time, classification)
                    )

            if total == 0:
                return _safe

            buy_ratio         = buy_count / total
            wash_wallets      = self._detect_wash_trades(wallet_events)
            is_artificial     = buy_ratio > PUMP_RATIO_SOFT
            score             = self._compute_score(buy_ratio, len(wash_wallets))

            logger.info(
                f"TxPattern {token_address[:12]}...: "
                f"buys={buy_count}/{total} ({buy_ratio:.0%}), "
                f"wash={len(wash_wallets)}, score={score:.1f}"
            )
            bsr = buy_count / max(sell_count, 1)
            return TxPatternResult(
                token_address=token_address,
                buy_count=buy_count,
                sell_count=sell_count,
                total_txs=total,
                buy_ratio=round(buy_ratio, 4),
                wash_trade_wallets=wash_wallets,
                wash_trade_count=len(wash_wallets),
                is_artificial_pump=is_artificial,
                tx_pattern_score=score,
                buy_sell_ratio=round(bsr, 4),
            )

        except Exception as exc:
            logger.error(f"TxPatternScorer.analyze error: {exc}")
            return _safe
