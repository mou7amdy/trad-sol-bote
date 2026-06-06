# core/multi_source_detector.py
"""
Phase 1.1 — Multi-Source DEX Pool Detector

Simultaneously monitors five Solana DEX WebSocket streams:
  ┌──────────────────┬──────────────────────────────────────────────────────┐
  │ Raydium AMM v4   │ 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8       │
  │ Raydium CPMM     │ CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C       │
  │ Orca Whirlpool   │ whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc        │
  │ Meteora DLMM     │ LBUZKhRxPF3XUpBCjp4YzTKgLLjLsrqdeXydxgRt2Pgs      │
  │ Meteora Dyn AMM  │ Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkSX2zQi      │
  └──────────────────┴──────────────────────────────────────────────────────┘

Design guarantees
-----------------
• Each source runs in its own asyncio task with exponential-backoff reconnect.
• Deduplication: same mint seen within 5 minutes from any source is ignored.
• Mint extraction uses DEX-specific logic first, then a universal
  ``postTokenBalances`` fallback that works for any AMM.
• Source-level latency (on-chain blockTime → detection wall-clock) is tracked
  per DEX for monitoring.
• Callback signature: ``async def cb(token_info: TokenInfo, pool: DetectedPool)``
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

import httpx
import websockets
from loguru import logger

from config.settings import settings
from core.solana_scanner import (
    TokenInfo,
    _STABLE_MINTS,
    _parse_mint_from_transaction,
    get_token_info,
)

# ---------------------------------------------------------------------------
# DEX registry
# ---------------------------------------------------------------------------

_DEX_CONFIGS: dict[str, dict[str, Any]] = {
    "Raydium_AMM": {
        "program_id":       "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
        "creation_markers": ["initialize2"],
        "flag":             "ENABLE_RAYDIUM_AMM_DETECTION",
    },
    "Raydium_CPMM": {
        "program_id":       "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
        "creation_markers": ["Instruction: Initialize"],
        "flag":             "ENABLE_RAYDIUM_CPMM_DETECTION",
    },
    "Orca_Whirlpool": {
        "program_id":       "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "creation_markers": ["Instruction: InitializePool"],
        "flag":             "ENABLE_ORCA_DETECTION",
    },
    "Meteora_DLMM": {
        "program_id":       "LBUZKhRxPF3XUpBCjp4YzTKgLLjLsrqdeXydxgRt2Pgs",
        "creation_markers": ["Instruction: InitializeLbPair"],
        "flag":             "ENABLE_METEORA_DETECTION",
    },
    "Meteora_AMM": {
        "program_id":       "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkSX2zQi",
        "creation_markers": ["initialize_permissionless_pool", "Instruction: Initialize"],
        "flag":             "ENABLE_METEORA_DETECTION",
    },
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DetectedPool:
    """Rich metadata attached to every new pool detection event."""
    mint_address:  str
    dex_name:      str
    signature:     str
    block_time:    int          # on-chain Unix timestamp (seconds)
    detected_at:   float       # time.monotonic() at detection
    latency_ms:    float       # wall-clock ms from blockTime to detection
    pool_address:  str  = ""
    token_info:    Any  = None  # TokenInfo — filled after Birdeye fetch


@dataclass
class SourceStats:
    """Running statistics for a single DEX source."""
    dex_name:          str
    pools_detected:    int   = 0
    latency_ms_sum:    float = 0.0
    connection_errors: int   = 0
    is_connected:      bool  = False
    last_detected_at:  Optional[float] = None

    @property
    def avg_latency_ms(self) -> float:
        if self.pools_detected == 0:
            return 0.0
        return self.latency_ms_sum / self.pools_detected


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class MultiSourceDetector:
    """
    Concurrent multi-DEX pool detector.

    Usage
    -----
    ::

        detector = MultiSourceDetector()
        await detector.start(handle_new_token)

    The callback receives ``(token_info: TokenInfo, pool: DetectedPool)``.
    """

    _DEDUP_TTL: float = 300.0   # seconds — ignore same mint within this window
    _MAX_BACKOFF: float = 60.0  # seconds — cap on reconnection wait

    def __init__(self) -> None:
        self._wss_url: str = settings.SOLANA_WSS_URL
        self._rpc_url: str = settings.SOLANA_RPC_URL
        # mint_address → monotonic timestamp of first detection
        self._seen: dict[str, float] = {}
        # per-DEX stats
        self._stats: dict[str, SourceStats] = {
            name: SourceStats(dex_name=name) for name in _DEX_CONFIGS
        }

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_source_stats(self) -> list[SourceStats]:
        """Return a snapshot of per-source latency and detection counts."""
        return list(self._stats.values())

    def get_stats_summary(self) -> str:
        lines = []
        for s in self._stats.values():
            status = "🟢" if s.is_connected else "🔴"
            lines.append(
                f"{status} {s.dex_name}: {s.pools_detected} pools, "
                f"avg {s.avg_latency_ms:.0f}ms latency, "
                f"{s.connection_errors} errors"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _is_duplicate(self, mint: str) -> bool:
        """
        Return True if *mint* was already seen within ``_DEDUP_TTL`` seconds.
        Also evicts stale entries on every call.
        """
        now = time.monotonic()
        stale = [m for m, ts in self._seen.items() if now - ts > self._DEDUP_TTL]
        for m in stale:
            del self._seen[m]
        if mint in self._seen:
            return True
        self._seen[mint] = now
        return False

    # ------------------------------------------------------------------
    # RPC helper
    # ------------------------------------------------------------------

    async def _fetch_transaction(self, signature: str) -> Optional[dict]:
        """Fetch a full transaction from the configured Helius RPC endpoint."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "json", "maxSupportedTransactionVersion": 0},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(self._rpc_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        logger.warning(
                            f"RPC error fetching tx {signature[:20]}...: {data['error']}"
                        )
                        return None
                    return data.get("result")
                logger.warning(f"HTTP {resp.status_code} fetching tx {signature[:20]}...")
        except Exception as exc:
            logger.error(f"_fetch_transaction error: {exc}")
        return None

    # ------------------------------------------------------------------
    # Mint extraction (DEX-specific + universal fallback)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_mint(tx_data: dict, dex_name: str) -> Optional[str]:
        """
        Extract the new token mint address from a pool-creation transaction.

        Strategy
        --------
        1. Raydium AMM: use the known byte-offset instruction parser.
        2. All DEXes (including Raydium fallback): scan ``postTokenBalances``
           for mints that are not known stable-coins.  When exactly one
           non-stable mint appears, that is the new token.
        """
        # Raydium AMM-specific fast path
        if dex_name == "Raydium_AMM":
            try:
                mint = _parse_mint_from_transaction(tx_data)
                if mint:
                    return mint
            except Exception:
                pass  # fall through to universal path

        # Universal path: postTokenBalances scan
        try:
            meta = tx_data.get("meta") or {}
            post: list[dict] = meta.get("postTokenBalances") or []
            all_mints = {b["mint"] for b in post if b.get("mint")}
            new_mints = all_mints - _STABLE_MINTS

            if len(new_mints) == 1:
                return new_mints.pop()
            if len(new_mints) > 1:
                # Multiple candidates — pick lexicographically first for determinism.
                # In practice this happens when two new tokens are created in the same tx.
                logger.debug(
                    f"[{dex_name}] Multiple non-stable mints: {new_mints} — picking first"
                )
                return sorted(new_mints)[0]
        except Exception as exc:
            logger.error(f"_extract_mint universal path error [{dex_name}]: {exc}")

        return None

    # ------------------------------------------------------------------
    # Per-DEX persistent listener
    # ------------------------------------------------------------------

    async def _listen_dex(
        self,
        dex_name: str,
        program_id: str,
        creation_markers: list[str],
        callback: Callable[..., Coroutine],
    ) -> None:
        """
        Persistently listen to *one* DEX WebSocket stream.
        Reconnects with exponential backoff on any failure.
        """
        stats = self._stats[dex_name]
        backoff = 1.0

        while True:
            try:
                logger.info(f"[{dex_name}] Connecting to WebSocket...")
                async with websockets.connect(
                    self._wss_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**23,  # 8 MiB — Solana log events can be large
                ) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [program_id]},
                            {"commitment": "confirmed"},  # "confirmed" is faster than "finalized"
                        ],
                    }))
                    stats.is_connected = True
                    backoff = 1.0   # reset on clean connection
                    logger.info(f"[{dex_name}] ✅ Subscribed — listening for pool events.")

                    async for raw in ws:
                        recv_wall = time.time()
                        recv_mono = time.monotonic()
                        try:
                            msg = json.loads(raw)
                            value: dict = (
                                msg.get("params", {})
                                .get("result", {})
                                .get("value", {})
                            )
                            if not value:
                                continue

                            logs: list[str] = value.get("logs") or []

                            # Match ANY of the creation markers against ANY log line
                            is_creation = any(
                                marker in log
                                for marker in creation_markers
                                for log in logs
                            )
                            if not is_creation:
                                continue

                            sig: str = value.get("signature", "")
                            if not sig:
                                continue

                            logger.info(
                                f"[{dex_name}] Pool-creation event — sig={sig[:20]}..."
                            )

                            # Fetch full transaction to extract mint
                            tx = await self._fetch_transaction(sig)
                            if not tx:
                                continue

                            mint = self._extract_mint(tx, dex_name)
                            if not mint:
                                logger.debug(
                                    f"[{dex_name}] Could not extract mint from {sig[:20]}..."
                                )
                                continue

                            if self._is_duplicate(mint):
                                logger.debug(
                                    f"[{dex_name}] Dup mint {mint[:12]}... — skipping"
                                )
                                continue

                            # Latency = wall time of detection minus on-chain blockTime
                            block_time: int = int(tx.get("blockTime") or recv_wall)
                            latency_ms = max(0.0, (recv_wall - block_time) * 1000.0)

                            stats.pools_detected += 1
                            stats.latency_ms_sum += latency_ms
                            stats.last_detected_at = recv_mono

                            logger.info(
                                f"[{dex_name}] 🆕 mint={mint[:12]}... "
                                f"latency={latency_ms:.0f}ms"
                            )

                            # Enrich with Birdeye and dispatch to callback
                            token_info = await get_token_info(mint)
                            token_info.dex_source = dex_name
                            token_info.detection_latency_ms = latency_ms

                            pool = DetectedPool(
                                mint_address=mint,
                                dex_name=dex_name,
                                signature=sig,
                                block_time=block_time,
                                detected_at=recv_mono,
                                latency_ms=latency_ms,
                                token_info=token_info,
                            )

                            asyncio.create_task(callback(token_info, pool))

                        except Exception as exc:
                            logger.error(f"[{dex_name}] Error processing message: {exc}")

            except websockets.exceptions.ConnectionClosed as exc:
                stats.is_connected = False
                logger.warning(
                    f"[{dex_name}] Connection closed ({exc.code}). "
                    f"Reconnecting in {backoff:.1f}s..."
                )
            except Exception as exc:
                stats.is_connected = False
                stats.connection_errors += 1
                logger.error(
                    f"[{dex_name}] Fatal error: {exc}. "
                    f"Reconnecting in {backoff:.1f}s..."
                )

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, self._MAX_BACKOFF)

    # ------------------------------------------------------------------
    # Mock listener (placeholder credentials)
    # ------------------------------------------------------------------

    async def _run_mock(self, callback: Callable[..., Coroutine]) -> None:
        """Emit synthetic pool events when WebSocket credentials are placeholders."""
        import random
        MOCK_POOLS = [
            ("DezXAZ8z7PnrFcPykJziH6WGHe1qZrUGLGQc1Y7daB2k", "Raydium_AMM"),
            ("HeLPr5cvjUA9Z6tKjgDQ3C6J58P3PZ63G6oYp98Zmint",  "Orca_Whirlpool"),
            ("MOCKsf8v7PnrFcPykJziH6WGHe1qZrUGLGQc1Y7damint", "Meteora_DLMM"),
        ]
        logger.warning("MultiSourceDetector: running in MOCK mode (placeholder WSS URL).")
        while True:
            await asyncio.sleep(5.0)
            mint, dex = random.choice(MOCK_POOLS)
            latency = random.uniform(150.0, 600.0)
            token_info = TokenInfo(
                address=mint, symbol="MOCK", name="Mock Token",
                price=0.001, liquidity_usd=20_000.0, market_cap=150_000.0,
                dex_source=dex, detection_latency_ms=latency,
            )
            pool = DetectedPool(
                mint_address=mint, dex_name=dex, signature="mock_sig",
                block_time=int(time.time()), detected_at=time.monotonic(),
                latency_ms=latency, token_info=token_info,
            )
            asyncio.create_task(callback(token_info, pool))

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def start(self, callback: Callable[..., Coroutine]) -> None:
        """
        Start all enabled DEX listeners concurrently.
        Falls back to mock mode when credentials are not configured.

        This coroutine runs forever (never returns under normal operation).
        """
        is_mock = (
            not self._wss_url
            or "api.mainnet-beta.solana.com" in self._wss_url
            or "your_" in self._wss_url
        )
        if is_mock:
            await self._run_mock(callback)
            return

        active_dex_names: list[str] = []
        tasks: list[Coroutine] = []
        for dex_name, cfg in _DEX_CONFIGS.items():
            flag = cfg.get("flag", "")
            if flag and not getattr(settings, flag, True):
                logger.info(f"[{dex_name}] Disabled via feature flag {flag!r}.")
                continue
            active_dex_names.append(dex_name)
            tasks.append(
                self._listen_dex(
                    dex_name=dex_name,
                    program_id=cfg["program_id"],
                    creation_markers=cfg["creation_markers"],
                    callback=callback,
                )
            )

        if not tasks:
            logger.error("MultiSourceDetector: no DEX sources enabled — nothing to do.")
            return

        logger.info(
            f"MultiSourceDetector starting {len(tasks)} source(s): "
            f"{active_dex_names}"
        )
        # run_exceptions=True so one crashed listener never kills the others
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for dex_name, exc in zip(active_dex_names, results):
            if isinstance(exc, Exception):
                logger.error(f"[{dex_name}] Listener exited with exception: {exc}")
