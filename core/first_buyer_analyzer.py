# core/first_buyer_analyzer.py
"""
Phase 1.3 — First-Buyer / Smart-Money Analyzer

Identifies "smart money" among the first buyers of a newly launched token by
examining each early buyer's historical transaction success rate.

Algorithm
---------
1. Collect the first N buyer wallets from the token's earliest signatures
   (up to ``MAX_BUYERS`` wallets, within the first ``BUY_WINDOW_SECONDS``).
   Fee-payer of each transaction = buyer wallet proxy.

2. For each buyer wallet, retrieve up to ``MAX_WALLET_SIGNATURES`` recent
   transaction signatures.

3. For each signature, parse the raw SOL balance delta to determine:
   • SOL spent (buy)  — SOL pre-balance > post-balance, token received
   • SOL received (sell) — SOL post-balance > pre-balance, token sent

4. A wallet is classified as "smart money" when its ``profitable_ratio``
   (profitable round-trips / total round-trips) exceeds
   ``SMART_MONEY_WIN_THRESHOLD``.

5. ``smart_money_score`` = min(100, smart_money_count / analyzed × 100 × BOOST)
   where BOOST amplifies the score when the percentage is high.

Concurrency
-----------
All wallet analyses run concurrently behind an ``asyncio.Semaphore`` capped at
``MAX_PARALLEL_WALLETS`` to avoid flooding the RPC endpoint.
Helius enhanced-API parsing is used when ``HELIUS_API_KEY`` is configured;
raw RPC is used as a fallback.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
MAX_BUYERS:               int   = 15    # analyse at most 15 first buyers
BUY_WINDOW_SECONDS:       int   = 120   # only count buys within first 2 min
MAX_WALLET_SIGNATURES:    int   = 80    # recent txs to examine per wallet
MAX_PARALLEL_WALLETS:     int   = 8     # semaphore cap
WALLET_TASK_TIMEOUT:      float = 4.0   # seconds per individual wallet
SMART_MONEY_WIN_THRESHOLD: float = 0.40 # ≥40% profitable ratio = smart money
SOL_LAMPORTS:             int   = 1_000_000_000

# Known DEX / AMM program IDs — fee payers that are DEX programs are excluded
_DEX_PROGRAM_IDS: frozenset[str] = frozenset({
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "LBUZKhRxPF3XUpBCjp4YzTKgLLjLsrqdeXydxgRt2Pgs",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkSX2zQi",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BuyerProfile:
    """Analysis result for a single early buyer wallet."""
    wallet:          str
    total_swaps:     int   = 0
    profitable_swaps: int  = 0
    profitable_ratio: float = 0.0   # profitable_swaps / total_swaps
    is_smart_money:  bool  = False
    error:           bool  = False   # True if analysis failed


@dataclass
class FirstBuyerAnalysis:
    """Aggregated smart-money score for a token's first buyers."""
    token_address:        str
    first_buyers:         list[str] = field(default_factory=list)
    buyer_profiles:       list[BuyerProfile] = field(default_factory=list)
    smart_money_count:    int   = 0
    smart_money_pct:      float = 0.0   # % of analysed wallets that are smart
    analyzed_wallets:     int   = 0
    smart_money_score:    float = 0.0   # 0–100 signal score
    data_source:          str   = "rpc"


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class FirstBuyerAnalyzer:
    """
    Identifies smart money among the first buyers of a new token.

    All public methods are exception-safe — they always return a valid
    ``FirstBuyerAnalysis`` even when all API calls fail.
    """

    def __init__(self) -> None:
        self._rpc_url:    str = settings.SOLANA_RPC_URL
        self._helius_key: str = getattr(settings, "HELIUS_API_KEY", "")
        self._semaphore   = asyncio.Semaphore(MAX_PARALLEL_WALLETS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _has_helius(self) -> bool:
        return bool(
            self._helius_key
            and not self._helius_key.startswith("your_")
        )

    async def _post_rpc(self, payload: dict) -> Optional[dict]:
        """POST a JSON-RPC request and return the ``result`` field."""
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        logger.debug(f"RPC error: {data['error']}")
                        return None
                    return data.get("result")
                logger.warning(f"RPC HTTP {resp.status_code}")
        except Exception as exc:
            logger.error(f"_post_rpc error: {exc}")
        return None

    async def _get_signatures(self, address: str, limit: int = 100) -> list[dict]:
        """Return up to *limit* recent signatures for *address*."""
        result = await self._post_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": limit, "commitment": "confirmed"}],
        })
        if isinstance(result, list):
            return result
        return []

    async def _get_transaction(self, signature: str) -> Optional[dict]:
        """Fetch a full parsed transaction."""
        return await self._post_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "json", "maxSupportedTransactionVersion": 0},
            ],
        })

    # ------------------------------------------------------------------
    # Step 1 — Collect first buyers
    # ------------------------------------------------------------------

    async def _get_first_buyers(
        self, token_address: str, pool_creation_time: int
    ) -> list[str]:
        """
        Return wallets that transacted on *token_address* within
        ``BUY_WINDOW_SECONDS`` of pool creation.

        Each transaction's fee-payer (accountKeys[0]) is used as the buyer
        proxy.  Known DEX program IDs are excluded.
        """
        sigs = await self._get_signatures(token_address, limit=200)
        if not sigs:
            return []

        cutoff = pool_creation_time + BUY_WINDOW_SECONDS
        early_sigs = [
            s["signature"]
            for s in sigs
            if (
                s.get("blockTime") is not None
                and pool_creation_time <= s["blockTime"] <= cutoff
                and s.get("signature")
            )
        ]

        if not early_sigs:
            return []

        # Resolve fee-payers concurrently (semaphore-limited)
        sem = asyncio.Semaphore(15)

        async def fee_payer(sig: str) -> Optional[str]:
            async with sem:
                tx = await self._get_transaction(sig)
                if not tx:
                    return None
                try:
                    keys: list[str] = (
                        tx.get("transaction", {})
                        .get("message", {})
                        .get("accountKeys", [])
                    )
                    if keys and keys[0] not in _DEX_PROGRAM_IDS:
                        return keys[0]
                except Exception:
                    pass
                return None

        raw_results = await asyncio.gather(
            *[fee_payer(s) for s in early_sigs[:MAX_BUYERS * 2]],
            return_exceptions=True,
        )
        results = [r for r in raw_results if not isinstance(r, Exception)]

        # Unique wallets, preserving order, up to MAX_BUYERS
        seen: set[str] = set()
        buyers: list[str] = []
        for w in results:
            if w and w not in seen:
                seen.add(w)
                buyers.append(w)
                if len(buyers) >= MAX_BUYERS:
                    break

        logger.debug(
            f"First buyers for {token_address[:12]}...: "
            f"{len(buyers)} unique wallets from {len(early_sigs)} early txs"
        )
        return buyers

    # ------------------------------------------------------------------
    # Step 2 — Analyse individual wallet P&L
    # ------------------------------------------------------------------

    async def _analyse_wallet_helius(self, wallet: str) -> BuyerProfile:
        """
        Use Helius Enhanced API to get parsed swap transactions for *wallet*.
        Returns a BuyerProfile based on SOL net flows.
        """
        url = (
            f"https://api.helius.xyz/v0/addresses/{wallet}/transactions"
            f"?api-key={self._helius_key}&type=SWAP&limit=100"
        )
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                txs: list[dict] = resp.json() or []

        except Exception as exc:
            logger.debug(f"Helius enhanced API error for {wallet[:12]}...: {exc}")
            return await self._analyse_wallet_rpc(wallet)

        total = len(txs)
        if total == 0:
            return BuyerProfile(wallet=wallet, total_swaps=0, profitable_ratio=0.0)

        profitable = 0
        for tx in txs:
            # Helius Enhanced gives a ``nativeTransfers`` list with direction
            native: list[dict] = tx.get("nativeTransfers") or []
            # SOL received by this wallet in this tx
            sol_in = sum(
                t.get("amount", 0)
                for t in native
                if t.get("toUserAccount") == wallet
            )
            sol_out = sum(
                t.get("amount", 0)
                for t in native
                if t.get("fromUserAccount") == wallet
            )
            # Net SOL positive = profitable swap (sold for more than bought)
            if sol_in > sol_out:
                profitable += 1

        ratio = profitable / total
        return BuyerProfile(
            wallet=wallet,
            total_swaps=total,
            profitable_swaps=profitable,
            profitable_ratio=round(ratio, 4),
            is_smart_money=ratio >= SMART_MONEY_WIN_THRESHOLD,
        )

    async def _analyse_wallet_rpc(self, wallet: str) -> BuyerProfile:
        """
        Analyse wallet P&L using raw Solana RPC transactions.

        For each recent transaction:
        • preBalance  > postBalance → SOL left the wallet (buy / fee)
        • postBalance > preBalance  → SOL entered the wallet (sell / receive)
        We look for *both* patterns across two adjacent txs to detect a
        round-trip.  As a simpler but useful heuristic, we track whether this
        wallet's recent tx history shows more SOL-in txs than SOL-out txs.
        """
        sigs = await self._get_signatures(wallet, limit=MAX_WALLET_SIGNATURES)
        if not sigs:
            return BuyerProfile(wallet=wallet, error=True)

        # Only analyse the last 30 days
        cutoff_ts = time.time() - 30 * 86_400
        recent_sigs = [
            s["signature"]
            for s in sigs
            if (s.get("blockTime") or 0) >= cutoff_ts and s.get("signature")
        ][:50]  # cap to 50 to keep latency down

        if not recent_sigs:
            return BuyerProfile(wallet=wallet, total_swaps=0)

        # Fetch transactions concurrently (semaphore already held by caller)
        sem = asyncio.Semaphore(10)

        async def fetch_tx(sig: str) -> Optional[dict]:
            async with sem:
                return await self._get_transaction(sig)

        txs_raw = await asyncio.gather(*[fetch_tx(s) for s in recent_sigs], return_exceptions=True)
        txs = [t for t in txs_raw if not isinstance(t, Exception)]

        total_swaps = 0
        profitable = 0

        for tx in txs:
            if not tx:
                continue
            meta = tx.get("meta") or {}
            pre_bals:  list[int] = meta.get("preBalances")  or []
            post_bals: list[int] = meta.get("postBalances") or []
            acct_keys: list[str] = (
                tx.get("transaction", {}).get("message", {}).get("accountKeys", []) or []
            )

            # Find the index of our wallet in the account-key list
            try:
                idx = acct_keys.index(wallet)
            except ValueError:
                continue  # wallet not directly in this tx

            if idx >= len(pre_bals) or idx >= len(post_bals):
                continue

            pre  = pre_bals[idx]
            post = post_bals[idx]
            delta = post - pre  # lamports — fee included

            # Token balance changes (non-zero = a swap likely occurred)
            pre_tok  = meta.get("preTokenBalances")  or []
            post_tok = meta.get("postTokenBalances") or []
            involved_in_swap = bool(pre_tok or post_tok)

            if not involved_in_swap:
                continue  # plain SOL transfer — skip

            total_swaps += 1
            # Net positive (excluding tiny fee — 5 000 lamports)
            if delta > 5_000:
                profitable += 1

        if total_swaps == 0:
            return BuyerProfile(wallet=wallet, total_swaps=0)

        ratio = profitable / total_swaps
        return BuyerProfile(
            wallet=wallet,
            total_swaps=total_swaps,
            profitable_swaps=profitable,
            profitable_ratio=round(ratio, 4),
            is_smart_money=ratio >= SMART_MONEY_WIN_THRESHOLD,
        )

    async def _analyse_wallet(self, wallet: str) -> BuyerProfile:
        """Wrapper that chooses Helius or RPC path and enforces timeout."""
        async with self._semaphore:
            try:
                if self._has_helius():
                    coro = self._analyse_wallet_helius(wallet)
                else:
                    coro = self._analyse_wallet_rpc(wallet)
                return await asyncio.wait_for(coro, timeout=WALLET_TASK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"Wallet analysis timed out for {wallet[:12]}...")
                return BuyerProfile(wallet=wallet, error=True)
            except Exception as exc:
                logger.error(f"_analyse_wallet error for {wallet[:12]}...: {exc}")
                return BuyerProfile(wallet=wallet, error=True)

    # ------------------------------------------------------------------
    # Score calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_score(smart_pct: float) -> float:
        """
        Convert smart-money percentage to a 0–100 signal score.

        The score is non-linear: a large proportion of smart money is
        a very strong signal (amplified by a quadratic boost above 30%).
        """
        if smart_pct <= 0:
            return 5.0
        if smart_pct >= 70:
            return 100.0
        if smart_pct >= 30:
            # 30% → 60 pts, 70% → 100 pts (quadratic)
            frac = (smart_pct - 30) / 40.0
            return 60.0 + frac ** 0.8 * 40.0
        # 0–30%: linear 5 → 60
        return 5.0 + (smart_pct / 30.0) * 55.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        token_address: str,
        pool_creation_time: int,
    ) -> FirstBuyerAnalysis:
        """
        Run the full first-buyer smart-money analysis.

        Always returns a valid ``FirstBuyerAnalysis`` — never raises.
        """
        _safe = FirstBuyerAnalysis(
            token_address=token_address,
            smart_money_score=10.0,
            data_source="unavailable",
        )
        try:
            # Step 1: collect first buyer wallets
            buyers = await self._get_first_buyers(token_address, pool_creation_time)
            if not buyers:
                logger.info(
                    f"FirstBuyerAnalyzer: no early buyers found for {token_address[:12]}..."
                )
                return _safe

            # Step 2: analyse each buyer concurrently
            raw_profiles = await asyncio.gather(
                *[self._analyse_wallet(w) for w in buyers],
                return_exceptions=True,
            )
            profiles: list[BuyerProfile] = [
                p for p in raw_profiles if not isinstance(p, Exception)
            ]

            # Step 3: aggregate
            valid_profiles = [p for p in profiles if not p.error]
            analyzed = len(valid_profiles)
            sm_count = sum(1 for p in valid_profiles if p.is_smart_money)
            sm_pct   = (sm_count / analyzed * 100.0) if analyzed else 0.0
            score    = self._compute_score(sm_pct)

            logger.info(
                f"FirstBuyerAnalysis for {token_address[:12]}...: "
                f"buyers={len(buyers)}, analyzed={analyzed}, "
                f"smart_money={sm_count} ({sm_pct:.1f}%), score={score:.1f}"
            )

            return FirstBuyerAnalysis(
                token_address=token_address,
                first_buyers=buyers,
                buyer_profiles=valid_profiles,
                smart_money_count=sm_count,
                smart_money_pct=round(sm_pct, 2),
                analyzed_wallets=analyzed,
                smart_money_score=round(score, 2),
                data_source="helius" if self._has_helius() else "rpc",
            )

        except Exception as exc:
            logger.error(
                f"FirstBuyerAnalyzer.analyze failed for {token_address[:12]}...: {exc}"
            )
            return _safe
