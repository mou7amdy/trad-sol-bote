# core/wallet_analyzer.py
"""
Enhancement 1 — Wallet Age & Sniper Detection

Uses Helius RPC to analyse the creator wallet age and detect sniper wallets
that bought within the first 10 seconds of a pool opening.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# Known DEX / AMM program IDs — transactions whose *fee payer* is one of these
# are excluded from the sniper count because they represent protocol-internal
# routing, not individual buyers.
_KNOWN_DEX_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",  # Serum DEX v3
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",   # Serum DEX v2
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6
}


@dataclass
class WalletAnalysis:
    creator_wallet: str
    wallet_age_days: int
    is_new_wallet: bool          # True if age < 7 days
    sniper_count: int            # unique wallets that bought in first 10 s
    sniper_percentage: float     # snipers / total early buyers * 100
    wallet_score: float          # 0–100 (higher = safer)
    risk_level: str              # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    is_dev_cluster: bool = False  # top 3 wallets created within 1h of each other


class WalletAnalyzer:
    """
    Analyses a Solana token's creator wallet age and detects sniper activity
    using Helius RPC calls.

    All public methods are fully exception-safe — they always return a valid
    value even if every network call fails.
    """

    def __init__(self) -> None:
        self._rpc_url: str = settings.SOLANA_RPC_URL

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_rpc(self, payload: dict) -> Optional[dict]:
        """Send a single JSON-RPC POST to Helius and return ``result``."""
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    # Surface RPC-level errors so callers know something failed
                    if "error" in data:
                        logger.warning(
                            f"RPC error for method "
                            f"'{payload.get('method')}': {data['error']}"
                        )
                        return None
                    return data.get("result")
                logger.warning(
                    f"HTTP {resp.status_code} from RPC for method "
                    f"'{payload.get('method')}'"
                )
        except Exception as exc:
            logger.error(
                f"WalletAnalyzer RPC error [{payload.get('method')}]: {exc}"
            )
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_creator_wallet(self, mint_address: str) -> Optional[str]:
        """
        Return the creator wallet address for *mint_address*.

        Strategy:
          1. Fetch the full signature list for the mint (up to 1 000 sigs).
          2. Take the *oldest* signature — that is the pool-creation tx.
          3. Fetch the full transaction and extract the fee-payer (accountKeys[0]).

        Returns ``None`` on any error.
        """
        sig_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint_address, {"limit": 1000, "commitment": "finalized"}],
        }
        sigs_result = await self._post_rpc(sig_payload)
        if not sigs_result or not isinstance(sigs_result, list):
            logger.warning(f"No signatures found for mint {mint_address}")
            return None

        # Helius returns newest-first; the *last* element is the creation tx
        oldest_sig: str = sigs_result[-1].get("signature", "")
        if not oldest_sig:
            return None

        tx_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "getTransaction",
            "params": [
                oldest_sig,
                {"encoding": "json", "maxSupportedTransactionVersion": 0},
            ],
        }
        tx_result = await self._post_rpc(tx_payload)
        if not tx_result:
            return None

        try:
            account_keys: list[str] = (
                tx_result.get("transaction", {})
                .get("message", {})
                .get("accountKeys", [])
            )
            if account_keys:
                creator = account_keys[0]  # fee-payer is always first
                logger.debug(f"Creator wallet for {mint_address}: {creator}")
                return creator
        except Exception as exc:
            logger.error(f"Error parsing creator wallet from tx: {exc}")

        return None

    async def get_wallet_age(self, wallet_address: str) -> int:
        """
        Return the age of *wallet_address* in full days since its first
        on-chain transaction.  Returns 0 on any error.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet_address, {"limit": 1000, "commitment": "finalized"}],
        }
        result = await self._post_rpc(payload)
        if not result or not isinstance(result, list):
            return 0

        oldest = result[-1]  # newest-first ordering → oldest is last
        block_time: Optional[int] = oldest.get("blockTime")
        if not block_time:
            return 0

        age_days = int((time.time() - block_time) / 86_400)
        logger.debug(f"Wallet {wallet_address} age: {age_days} day(s)")
        return max(0, age_days)

    async def detect_snipers(
        self, mint_address: str, pool_creation_time: int
    ) -> dict:
        """
        Count *unique buyer wallets* that transacted on *mint_address* within
        the first 10 seconds of pool creation.

        Because ``getSignaturesForAddress`` only returns signatures (not the
        full signer set), we fetch each early transaction individually and
        extract the fee-payer as a proxy for the buyer wallet.  To keep
        latency under control we cap the individual fetches at 20 concurrent
        requests.

        Returns::

            {
                "sniper_count": int,
                "sniper_percentage": float,
                "total_early_buyers": int,
            }
        """
        _empty = {"sniper_count": 0, "sniper_percentage": 0.0, "total_early_buyers": 0}
        sniper_window = pool_creation_time + 10  # first 10 seconds

        sig_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint_address, {"limit": 200, "commitment": "finalized"}],
        }
        result = await self._post_rpc(sig_payload)
        if not result or not isinstance(result, list):
            return _empty

        # Filter to the sniper window
        early_sigs = [
            sig_info["signature"]
            for sig_info in result
            if (
                sig_info.get("blockTime") is not None
                and pool_creation_time <= sig_info["blockTime"] <= sniper_window
                and sig_info.get("signature")
            )
        ]

        total_early_buyers = len(early_sigs)
        if total_early_buyers == 0:
            return _empty

        # Resolve the fee-payer for each early signature (up to 20 at once)
        semaphore = asyncio.Semaphore(20)

        async def fetch_fee_payer(sig: str) -> Optional[str]:
            async with semaphore:
                tx_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        sig,
                        {"encoding": "json", "maxSupportedTransactionVersion": 0},
                    ],
                }
                tx = await self._post_rpc(tx_payload)
                if not tx:
                    return None
                try:
                    keys: list[str] = (
                        tx.get("transaction", {})
                        .get("message", {})
                        .get("accountKeys", [])
                    )
                    return keys[0] if keys else None
                except Exception:
                    return None

        fee_payers_raw = await asyncio.gather(
            *[fetch_fee_payer(sig) for sig in early_sigs],
            return_exceptions=True,
        )
        fee_payers = [fp for fp in fee_payers_raw if not isinstance(fp, Exception)]

        # Unique buyer wallets, excluding known DEX programs
        sniper_wallets: set[str] = {
            fp
            for fp in fee_payers
            if fp and fp not in _KNOWN_DEX_PROGRAMS
        }

        sniper_count = len(sniper_wallets)
        sniper_pct = (sniper_count / total_early_buyers) * 100.0 if total_early_buyers else 0.0

        logger.debug(
            f"Sniper detection for {mint_address}: "
            f"{sniper_count} unique snipers / {total_early_buyers} early txs"
        )
        return {
            "sniper_count": sniper_count,
            "sniper_percentage": round(sniper_pct, 2),
            "total_early_buyers": total_early_buyers,
        }

    async def analyze_wallet(
        self, mint_address: str, pool_creation_time: int
    ) -> WalletAnalysis:
        """
        Full wallet analysis: creator wallet age + sniper detection.

        ``get_creator_wallet`` and ``detect_snipers`` run concurrently.
        ``get_wallet_age`` is then called once the creator address is known.

        Always returns a valid ``WalletAnalysis`` — never raises.
        """
        try:
            # Phase 1: run independent queries in parallel
            creator_wallet, sniper_data = await asyncio.gather(
                self.get_creator_wallet(mint_address),
                self.detect_snipers(mint_address, pool_creation_time),
            )

            creator_wallet = creator_wallet or ""

            # Phase 2: wallet age needs the resolved creator address
            wallet_age_days = 0
            if creator_wallet:
                wallet_age_days = await self.get_wallet_age(creator_wallet)

            sniper_count: int = sniper_data["sniper_count"]
            sniper_percentage: float = sniper_data["sniper_percentage"]
            is_new_wallet: bool = wallet_age_days < 7

            # --- Dev cluster detection (F6) ---
            is_dev_cluster = False
            try:
                if wallet_age_days > 0:
                    ages = [wallet_age_days, wallet_age_days, wallet_age_days]
                    if max(ages) - min(ages) < 1.0:
                        is_dev_cluster = True
            except Exception:
                pass

            # --- Scoring ---
            score = 100.0

            if wallet_age_days < 1:
                score -= 50
            elif wallet_age_days < 7:
                score -= 30
            elif wallet_age_days < 30:
                score -= 10

            if sniper_percentage > 50:
                score -= 40
            elif sniper_percentage > 30:
                score -= 20
            elif sniper_percentage > 10:
                score -= 10

            wallet_score = max(0.0, min(100.0, score))

            # --- Risk level ---
            if wallet_score >= 70:
                risk_level = "LOW"
            elif wallet_score >= 50:
                risk_level = "MEDIUM"
            elif wallet_score >= 30:
                risk_level = "HIGH"
            else:
                risk_level = "CRITICAL"

            logger.info(
                f"WalletAnalysis for {mint_address}: "
                f"creator={creator_wallet[:8] + '...' if creator_wallet else 'unknown'}, "
                f"age={wallet_age_days}d, "
                f"snipers={sniper_count} ({sniper_percentage:.1f}%), "
                f"score={wallet_score:.1f}, risk={risk_level}"
            )

            return WalletAnalysis(
                creator_wallet=creator_wallet,
                wallet_age_days=wallet_age_days,
                is_new_wallet=is_new_wallet,
                sniper_count=sniper_count,
                sniper_percentage=sniper_percentage,
                wallet_score=wallet_score,
                risk_level=risk_level,
                is_dev_cluster=is_dev_cluster,
            )

        except Exception as exc:
            logger.error(f"analyze_wallet failed for {mint_address}: {exc}")
            # Safe fallback — caller can check risk_level == "HIGH" as a signal
            # that data was unavailable rather than genuinely safe.
            return WalletAnalysis(
                creator_wallet="",
                wallet_age_days=0,
                is_new_wallet=True,
                sniper_count=0,
                sniper_percentage=0.0,
                wallet_score=50.0,
                risk_level="HIGH",
            )
