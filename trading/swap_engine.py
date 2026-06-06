# trading/swap_engine.py
"""
Jupiter v6 Swap Engine — auto-buy and auto-sell.

100 % free APIs:
  • Jupiter v6 Quote API  — https://quote-api.jup.ag/v6/quote
  • Jupiter v6 Swap API   — https://quote-api.jup.ag/v6/swap
  • DexScreener           — https://api.dexscreener.com/latest/dex/tokens/{mint}
  • GeckoTerminal (backup)— https://api.geckoterminal.com/api/v2/…
  • Helius RPC            — getRecentPrioritizationFees

Design goals:
  • Execute buy in < 200 ms from signal arrival
  • Idempotency via per-mint asyncio.Lock + active_trades set
  • Fractional Kelly position sizing
  • 4-tier take-profit ladder + trailing stop + emergency stop + time stop
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple

import httpx
from loguru import logger
from solders.keypair import Keypair  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore

from config.settings import settings, runtime_state
from core.shared_state import shared_state

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOL_MINT        = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000
JUP_QUOTE_URL   = "https://quote-api.jup.ag/v6/quote"
JUP_SWAP_URL    = "https://quote-api.jup.ag/v6/swap"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
GECKO_URL       = (
    "https://api.geckoterminal.com/api/v2/networks/solana/pools/{address}"
)

# Take-profit ladder: (multiplier, fraction_of_remaining_to_sell)
TP_LADDER: List[Tuple[float, float]] = [
    (2.0,  0.25),   # at 2x → sell 25 %
    (5.0,  0.25),   # at 5x → sell 25 %
    (10.0, 0.25),   # at 10x → sell 25 %
    # remaining 25 % is the trailing leg — handled by trailing stop
]

# Trailing stop: {min_profit_multiplier: stop_at_fraction_below_highest}
TRAILING_STOP_TIERS = [
    (4.00, 0.85),  # profit ≥ 300 % (4x)   → stop at -15 % from high
    (2.00, 0.80),  # profit ≥ 100 % (2x)   → stop at -20 %
    (1.50, 0.75),  # profit ≥  50 % (1.5x) → stop at -25 %
    (1.00, 0.70),  # any profit             → stop at -30 %
]

EMERGENCY_STOP_DROP_PCT    = 40.0    # -40 % from entry → sell all
EMERGENCY_STOP_WINDOW_SECS = 60.0   # only if drop happens within this window
TIME_STOP_MINUTES          = 30     # hold > 30 min with < 50 % profit → sell
TIME_STOP_MIN_PROFIT_PCT   = 50.0

MAX_PRIORITY_FEE_LAMPORTS  = int(0.005 * LAMPORTS_PER_SOL)   # 0.005 SOL cap
PRIORITY_FEE_PERCENTILE    = 75    # 75th percentile of recent fees
PRICE_POLL_INTERVAL        = 5.0   # seconds between price checks
ACTIVE_TRADE_TTL           = 120.0 # auto-clear from active_trades after 2 min

# Retry parameters
MAX_RETRIES = 3
RETRY_BACKOFF = [0.5, 1.5, 4.0]   # seconds


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BuyResult:
    success: bool
    mint_address: str
    symbol: str
    entry_price: float
    entry_sol: float
    tokens_received: float
    tx_signature: Optional[str]
    error: Optional[str]
    signal_score: float
    trade_id: int = 0               # set after portfolio_tracker.record_buy


@dataclass
class SellResult:
    success: bool
    mint_address: str
    exit_price: float
    exit_sol: float
    exit_reason: str
    tx_signature: Optional[str]
    error: Optional[str]
    fees_sol: float = 0.0


@dataclass
class PriceData:
    price_usd: float
    liquidity_usd: float
    holder_count: int = 0
    source: str = "dexscreener"


# ---------------------------------------------------------------------------
# SwapEngine
# ---------------------------------------------------------------------------

class SwapEngine:
    """
    Handles buy and sell execution via Jupiter v6.

    Initialise once; inject dependencies after DB connection is ready.
    """

    def __init__(self) -> None:
        self._keypair: Optional[Keypair] = None
        self._session: Optional[httpx.AsyncClient] = None

        # Idempotency
        self._active_trades: Set[str]                   = set()
        self._mint_locks: Dict[str, asyncio.Lock]       = {}
        self._mint_lock_master                          = asyncio.Lock()

        # Monitoring tasks
        self._monitor_tasks: Dict[str, asyncio.Task]    = {}

        # Alert callback (injected)
        self._alert_fn: Optional[Callable[[str], Coroutine]] = None

        # Portfolio tracker + circuit breaker (injected)
        self._portfolio_tracker = None
        self._circuit_breaker   = None

        # Backup RPC list
        self._rpc_urls: List[str] = [
            settings.SOLANA_RPC_URL,
            "https://api.mainnet-beta.solana.com",
            "https://rpc.ankr.com/solana",
        ]
        self._current_rpc_idx = 0

    # ── lifecycle ──────────────────────────────────────────────────────────

    def inject(
        self,
        portfolio_tracker: Any,
        circuit_breaker:   Any,
        alert_fn:          Callable[[str], Coroutine],
    ) -> None:
        self._portfolio_tracker = portfolio_tracker
        self._circuit_breaker   = circuit_breaker
        self._alert_fn          = alert_fn

    async def start(self) -> None:
        """Open HTTP session and load keypair from settings."""
        self._session = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Content-Type": "application/json"},
        )
        self._keypair = self._load_keypair()
        logger.info("SwapEngine started.")

    async def stop(self) -> None:
        for task in self._monitor_tasks.values():
            task.cancel()
        if self._session:
            await self._session.aclose()
        logger.info("SwapEngine stopped.")

    # ── keypair ────────────────────────────────────────────────────────────

    def _load_keypair(self) -> Optional[Keypair]:
        """Load private key from settings (set via .env only)."""
        pk = getattr(settings, "WALLET_PRIVATE_KEY", "")
        if not pk:
            logger.warning(
                "WALLET_PRIVATE_KEY not set — swap execution disabled."
            )
            return None
        try:
            pk = pk.strip()
            if pk.startswith("["):
                # JSON byte array
                byte_list = json.loads(pk)
                return Keypair.from_bytes(bytes(byte_list))
            else:
                # Base58 string
                from base58 import b58decode  # type: ignore
                return Keypair.from_bytes(b58decode(pk))
        except Exception as exc:
            logger.error(f"Failed to load keypair: {exc}")
            return None

    @property
    def wallet_pubkey(self) -> Optional[str]:
        return str(self._keypair.pubkey()) if self._keypair else None

    # ── idempotency ────────────────────────────────────────────────────────

    async def _get_mint_lock(self, mint: str) -> asyncio.Lock:
        async with self._mint_lock_master:
            if mint not in self._mint_locks:
                self._mint_locks[mint] = asyncio.Lock()
            return self._mint_locks[mint]

    async def _add_active_trade(self, mint: str) -> None:
        self._active_trades.add(mint)
        # Auto-remove after TTL
        async def _remove():
            await asyncio.sleep(ACTIVE_TRADE_TTL)
            self._active_trades.discard(mint)
        asyncio.create_task(_remove())

    # ── RPC helpers ────────────────────────────────────────────────────────

    @property
    def _rpc_url(self) -> str:
        return self._rpc_urls[self._current_rpc_idx % len(self._rpc_urls)]

    def _switch_rpc(self) -> None:
        self._current_rpc_idx += 1
        logger.warning(f"SwapEngine: switched to RPC {self._rpc_url}")

    async def _rpc_call(self, method: str, params: Any) -> Any:
        """Generic JSON-RPC call with latency measurement and fallback."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(MAX_RETRIES):
            try:
                t0 = time.monotonic()
                resp = await self._session.post(self._rpc_url, json=payload)
                latency_ms = (time.monotonic() - t0) * 1000
                if self._circuit_breaker:
                    await self._circuit_breaker.on_rpc_latency(latency_ms)
                data = resp.json()
                return data.get("result")
            except Exception as exc:
                logger.warning(
                    f"RPC call {method} attempt {attempt+1} failed: {exc}"
                )
                if attempt < MAX_RETRIES - 1:
                    self._switch_rpc()
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    # ── priority fee ───────────────────────────────────────────────────────

    async def _get_priority_fee_lamports(self) -> int:
        """
        Fetch recent prioritization fees and return the 75th-percentile
        value + 25 % buffer, capped at MAX_PRIORITY_FEE_LAMPORTS.
        """
        try:
            result = await self._rpc_call("getRecentPrioritizationFees", [])
            if not result or not isinstance(result, list):
                return 1_000_000   # default 0.001 SOL
            fees = sorted(int(r.get("prioritizationFee", 0)) for r in result)
            if not fees:
                return 1_000_000
            # 75th percentile
            idx = max(0, int(len(fees) * PRIORITY_FEE_PERCENTILE / 100) - 1)
            p75 = fees[idx]
            buffered = int(p75 * 1.25)
            return min(buffered, MAX_PRIORITY_FEE_LAMPORTS)
        except Exception as exc:
            logger.warning(f"Priority fee fetch failed: {exc} — using default")
            return 1_000_000

    # ── SOL balance ────────────────────────────────────────────────────────

    async def get_sol_balance(self) -> float:
        """Return wallet SOL balance, or 0.0 on error."""
        if not self._keypair:
            return 0.0
        try:
            result = await self._rpc_call(
                "getBalance", [str(self._keypair.pubkey())]
            )
            if result and "value" in result:
                return result["value"] / LAMPORTS_PER_SOL
        except Exception as exc:
            logger.error(f"getBalance failed: {exc}")
        return 0.0

    # ── token decimals ────────────────────────────────────────────────────

    async def _get_token_decimals(self, mint: str) -> int:
        try:
            result = await self._rpc_call("getTokenSupply", [mint])
            if result and "value" in result:
                return int(result["value"].get("decimals", 6))
        except Exception as exc:
            logger.warning(f"Failed to fetch token decimals for {mint}: {exc}")
        return 6

    # ── position sizing ────────────────────────────────────────────────────

    async def _calc_position_size_sol(self) -> float:
        """
        Fractional Kelly criterion:
            win_rate  = 0.40
            win_size  = 0.20  (avg 20 % gain)
            loss_size = 0.05  (avg  5 % loss)
            kelly = (0.40*0.20 - 0.60*0.05) / 0.20 = 0.25
            position = portfolio_sol * kelly * 0.25   (quarter-Kelly)
        """
        win_rate  = 0.40
        win_size  = 0.20
        loss_size = 0.05
        kelly = (win_rate * win_size - (1 - win_rate) * loss_size) / win_size
        quarter_kelly = kelly * 0.25

        balance = await self.get_sol_balance()
        position = balance * quarter_kelly

        position = max(position, settings.MIN_POSITION_SIZE_SOL)
        position = min(position, runtime_state.max_position_size_sol)
        return round(position, 4)

    # ── Jupiter API helpers ────────────────────────────────────────────────

    async def _jup_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int,
    ) -> Optional[Dict]:
        """Fetch a Jupiter v6 swap quote."""
        params = {
            "inputMint":          input_mint,
            "outputMint":         output_mint,
            "amount":             amount_lamports,
            "slippageBps":        slippage_bps,
            "onlyDirectRoutes":   "true",
            "asLegacyTransaction": "false",
        }
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._session.get(
                    JUP_QUOTE_URL, params=params
                )
                if resp.status_code == 200:
                    return resp.json()
                body = resp.text
                logger.warning(
                    f"Jupiter quote HTTP {resp.status_code}: {body[:200]}"
                )
            except Exception as exc:
                logger.warning(f"Jupiter quote attempt {attempt+1}: {exc}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
        return None

    async def _jup_swap(
        self, quote: Dict, priority_fee: int
    ) -> Optional[str]:
        """
        Build, sign and send a Jupiter v6 swap transaction.
        Returns the base64-encoded signed transaction bytes (for sending).
        """
        if not self._keypair or not self._session:
            return None

        payload = {
            "quoteResponse":            quote,
            "userPublicKey":            str(self._keypair.pubkey()),
            "wrapAndUnwrapSol":         True,
            "prioritizationFeeLamports": priority_fee,
            "dynamicComputeUnitLimit":  True,
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = await self._session.post(
                    JUP_SWAP_URL, json=payload
                )
                if resp.status_code != 200:
                    body = resp.text
                    logger.warning(
                        f"Jupiter swap HTTP {resp.status_code}: {body[:200]}"
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_BACKOFF[attempt])
                    continue
                data = resp.json()
                tx_b64 = data.get("swapTransaction")
                if not tx_b64:
                    logger.error("Jupiter swap: missing swapTransaction field")
                    return None

                # Sign the transaction
                tx_bytes = base64.b64decode(tx_b64)
                tx       = VersionedTransaction.from_bytes(tx_bytes)
                signed   = self._keypair.sign_versioned_transaction(tx)
                signed_b64 = base64.b64encode(bytes(signed)).decode()

                # Send via RPC
                sig = await self._send_raw_transaction(signed_b64)
                return sig
            except Exception as exc:
                logger.error(f"Jupiter swap attempt {attempt+1}: {exc}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF[attempt])

        return None

    async def _send_raw_transaction(self, signed_tx_b64: str) -> Optional[str]:
        """sendTransaction via RPC, returns signature string."""
        result = await self._rpc_call(
            "sendTransaction",
            [signed_tx_b64, {"encoding": "base64", "skipPreflight": False}],
        )
        if isinstance(result, str):
            return result
        logger.error(f"sendTransaction unexpected result: {result}")
        return None

    # ── price data ─────────────────────────────────────────────────────────

    async def _fetch_price(self, mint: str) -> Optional[PriceData]:
        """Fetch current price from DexScreener; fall back to GeckoTerminal."""
        # Primary: DexScreener
        try:
            url = DEXSCREENER_URL.format(mint=mint)
            resp = await self._session.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    p = pairs[0]
                    price_usd = float(p.get("priceUsd") or 0)
                    liq_usd   = float(
                        (p.get("liquidity") or {}).get("usd") or 0
                    )
                    return PriceData(
                        price_usd=price_usd,
                        liquidity_usd=liq_usd,
                        source="dexscreener",
                    )
        except Exception as exc:
            logger.debug(f"DexScreener price fetch failed for {mint}: {exc}")

        # Backup: GeckoTerminal (uses pool address — first attempt with mint)
        try:
            url = GECKO_URL.format(address=mint)
            resp = await self._session.get(url)
            if resp.status_code == 200:
                data = resp.json()
                attrs = (data.get("data") or {}).get("attributes") or {}
                price = float(attrs.get("base_token_price_usd") or 0)
                liq   = float(attrs.get("reserve_in_usd") or 0)
                if price > 0:
                    return PriceData(
                        price_usd=price,
                        liquidity_usd=liq,
                        source="geckoterminal",
                    )
        except Exception as exc:
            logger.debug(f"GeckoTerminal price fetch failed for {mint}: {exc}")

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  BUY EXECUTION
    # ══════════════════════════════════════════════════════════════════════

    async def execute_buy(
        self,
        mint_address: str,
        symbol: str,
        signal_score: float,
    ) -> BuyResult:
        """
        Execute a buy for *mint_address* using Jupiter v6.

        Steps:
          1. Idempotency guard (skip if already active)
          2. Safety checks (circuit breaker, max positions, balance)
          3. Position size calculation (fractional Kelly)
          4. Jupiter quote → swap → send
          5. Record in portfolio_tracker

        Returns BuyResult (check .success before proceeding).
        """
        # ── 1. Idempotency ───────────────────────────────────────────────
        if mint_address in self._active_trades:
            return BuyResult(
                success=False, mint_address=mint_address, symbol=symbol,
                entry_price=0, entry_sol=0, tokens_received=0,
                tx_signature=None, error="Already in active_trades",
                signal_score=signal_score,
            )

        mint_lock = await self._get_mint_lock(mint_address)
        if mint_lock.locked():
            return BuyResult(
                success=False, mint_address=mint_address, symbol=symbol,
                entry_price=0, entry_sol=0, tokens_received=0,
                tx_signature=None, error="Mint lock busy",
                signal_score=signal_score,
            )

        async with mint_lock:
            # ── 2. Safety checks ─────────────────────────────────────────
            if not shared_state.autobuy_enabled:
                return BuyResult(
                    success=False, mint_address=mint_address, symbol=symbol,
                    entry_price=0, entry_sol=0, tokens_received=0,
                    tx_signature=None, error="Auto-buy disabled",
                    signal_score=signal_score,
                )

            if self._circuit_breaker and self._circuit_breaker.is_active():
                return BuyResult(
                    success=False, mint_address=mint_address, symbol=symbol,
                    entry_price=0, entry_sol=0, tokens_received=0,
                    tx_signature=None,
                    error=f"Circuit breaker active: {self._circuit_breaker.get_state().reason}",
                    signal_score=signal_score,
                )

            if self._portfolio_tracker:
                open_count = await self._portfolio_tracker.count_open_trades()
                if open_count >= settings.MAX_OPEN_POSITIONS:
                    return BuyResult(
                        success=False, mint_address=mint_address, symbol=symbol,
                        entry_price=0, entry_sol=0, tokens_received=0,
                        tx_signature=None,
                        error=f"Max positions reached ({open_count}/{settings.MAX_OPEN_POSITIONS})",
                        signal_score=signal_score,
                    )

            if not self._keypair:
                return BuyResult(
                    success=False, mint_address=mint_address, symbol=symbol,
                    entry_price=0, entry_sol=0, tokens_received=0,
                    tx_signature=None, error="Keypair not loaded",
                    signal_score=signal_score,
                )

            position_sol = await self._calc_position_size_sol()
            balance      = await self.get_sol_balance()

            if balance < position_sol + 0.01:   # 0.01 SOL reserve for fees
                position_sol = balance - 0.01
            if position_sol < settings.MIN_POSITION_SIZE_SOL:
                return BuyResult(
                    success=False, mint_address=mint_address, symbol=symbol,
                    entry_price=0, entry_sol=0, tokens_received=0,
                    tx_signature=None,
                    error=f"Insufficient balance ({balance:.4f} SOL)",
                    signal_score=signal_score,
                )

            # ── 3. Execute buy ────────────────────────────────────────────
            await self._add_active_trade(mint_address)
            amount_lamports = int(position_sol * LAMPORTS_PER_SOL)

            try:
                priority_fee = await self._get_priority_fee_lamports()
                quote        = await self._jup_quote(
                    SOL_MINT, mint_address,
                    amount_lamports, slippage_bps=2000,
                )
                if not quote:
                    self._active_trades.discard(mint_address)
                    return BuyResult(
                        success=False, mint_address=mint_address, symbol=symbol,
                        entry_price=0, entry_sol=position_sol,
                        tokens_received=0, tx_signature=None,
                        error="Jupiter quote failed", signal_score=signal_score,
                    )

                out_amount = int(quote.get("outAmount", 0))
                if out_amount == 0:
                    self._active_trades.discard(mint_address)
                    return BuyResult(
                        success=False, mint_address=mint_address, symbol=symbol,
                        entry_price=0, entry_sol=position_sol,
                        tokens_received=0, tx_signature=None,
                        error="Quote returned 0 tokens", signal_score=signal_score,
                    )

                # Optimistic entry_price estimate
                entry_price_usd = 0.0
                price_data = await self._fetch_price(mint_address)
                if price_data:
                    entry_price_usd = price_data.price_usd

                sig = await self._jup_swap(quote, priority_fee)

                if not sig:
                    self._active_trades.discard(mint_address)
                    return BuyResult(
                        success=False, mint_address=mint_address, symbol=symbol,
                        entry_price=entry_price_usd, entry_sol=position_sol,
                        tokens_received=0, tx_signature=None,
                        error="Transaction send failed", signal_score=signal_score,
                    )

                token_decimals = int(quote.get("outputDecimals", 0))
                if token_decimals == 0:
                    token_decimals = await self._get_token_decimals(mint_address)
                tokens_received = out_amount / (10 ** token_decimals)

                result = BuyResult(
                    success=True,
                    mint_address=mint_address,
                    symbol=symbol,
                    entry_price=entry_price_usd,
                    entry_sol=position_sol,
                    tokens_received=tokens_received,
                    tx_signature=sig,
                    error=None,
                    signal_score=signal_score,
                )

                # ── 4. Record in portfolio ────────────────────────────────
                if self._portfolio_tracker:
                    trade_id = await self._portfolio_tracker.record_buy(
                        mint_address=mint_address,
                        symbol=symbol,
                        entry_price=entry_price_usd,
                        entry_sol=position_sol,
                        tokens_received=tokens_received,
                        signal_score=signal_score,
                        tx_signature=sig,
                    )
                    result.trade_id = trade_id

                # ── 5. Start sell monitoring ──────────────────────────────
                if result.trade_id > 0:
                    self._monitor_tasks[mint_address] = asyncio.create_task(
                        self._monitor_position(
                            trade_id=result.trade_id,
                            mint_address=mint_address,
                            symbol=symbol,
                            entry_price=entry_price_usd,
                            position_sol=position_sol,
                            tokens_received=tokens_received,
                            token_decimals=token_decimals,
                        )
                    )

                logger.info(
                    f"SwapEngine BUY ✅ {symbol} {position_sol:.4f} SOL "
                    f"→ {tokens_received:.2f} tokens  sig={sig[:12]}…"
                )
                return result

            except Exception as exc:
                self._active_trades.discard(mint_address)
                logger.error(f"execute_buy error for {mint_address}: {exc}", exc_info=True)
                return BuyResult(
                    success=False, mint_address=mint_address, symbol=symbol,
                    entry_price=0, entry_sol=position_sol, tokens_received=0,
                    tx_signature=None, error=str(exc), signal_score=signal_score,
                )

    # ══════════════════════════════════════════════════════════════════════
    #  SELL EXECUTION
    # ══════════════════════════════════════════════════════════════════════

    async def execute_sell(
        self,
        trade_id: int,
        mint_address: str,
        symbol: str,
        token_amount: float,
        token_decimals: int,
        exit_reason: str,
        status: str = "closed",
    ) -> SellResult:
        """
        Sell *token_amount* tokens back to SOL via Jupiter v6.
        """
        if not shared_state.autosell_enabled:
            return SellResult(
                success=False, mint_address=mint_address,
                exit_price=0, exit_sol=0,
                exit_reason=exit_reason, tx_signature=None,
                error="Auto-sell disabled",
            )

        if not self._keypair:
            return SellResult(
                success=False, mint_address=mint_address,
                exit_price=0, exit_sol=0,
                exit_reason=exit_reason, tx_signature=None,
                error="Keypair not loaded",
            )

        raw_amount = int(token_amount * (10 ** token_decimals))
        if raw_amount <= 0:
            return SellResult(
                success=False, mint_address=mint_address,
                exit_price=0, exit_sol=0,
                exit_reason=exit_reason, tx_signature=None,
                error="Zero token amount",
            )

        try:
            priority_fee = await self._get_priority_fee_lamports()
            # Use higher slippage for emergency/trailing sells (30 %)
            slippage = 3000 if "emergency" in exit_reason.lower() else 2500

            quote = await self._jup_quote(
                mint_address, SOL_MINT, raw_amount, slippage_bps=slippage
            )
            if not quote:
                return SellResult(
                    success=False, mint_address=mint_address,
                    exit_price=0, exit_sol=0,
                    exit_reason=exit_reason, tx_signature=None,
                    error="Jupiter quote failed",
                )

            out_lamports = int(quote.get("outAmount", 0))
            exit_sol     = out_lamports / LAMPORTS_PER_SOL

            sig = await self._jup_swap(quote, priority_fee)

            if not sig:
                return SellResult(
                    success=False, mint_address=mint_address,
                    exit_price=0, exit_sol=0,
                    exit_reason=exit_reason, tx_signature=None,
                    error="Transaction send failed",
                )

            # Estimate exit price from price feed
            price_data = await self._fetch_price(mint_address)
            exit_price_usd = price_data.price_usd if price_data else 0.0

            # Estimate fees (prioritization fee in SOL)
            fees_sol = priority_fee / LAMPORTS_PER_SOL

            result = SellResult(
                success=True,
                mint_address=mint_address,
                exit_price=exit_price_usd,
                exit_sol=exit_sol,
                exit_reason=exit_reason,
                tx_signature=sig,
                error=None,
                fees_sol=fees_sol,
            )

            if self._portfolio_tracker:
                await self._portfolio_tracker.record_sell(
                    trade_id=trade_id,
                    exit_price=exit_price_usd,
                    exit_sol=exit_sol,
                    exit_reason=exit_reason,
                    status=status,
                    fees_sol=fees_sol,
                    tx_signature=sig,
                )

            logger.info(
                f"SwapEngine SELL ✅ {symbol} reason={exit_reason} "
                f"→ {exit_sol:.4f} SOL  sig={sig[:12]}…"
            )
            return result

        except Exception as exc:
            logger.error(f"execute_sell error for {mint_address}: {exc}", exc_info=True)
            return SellResult(
                success=False, mint_address=mint_address,
                exit_price=0, exit_sol=0,
                exit_reason=exit_reason, tx_signature=None,
                error=str(exc),
            )

    # ══════════════════════════════════════════════════════════════════════
    #  POSITION MONITORING  (runs as a background asyncio Task per trade)
    # ══════════════════════════════════════════════════════════════════════

    async def _monitor_position(
        self,
        trade_id: int,
        mint_address: str,
        symbol: str,
        entry_price: float,
        position_sol: float,
        tokens_received: float,
        token_decimals: int = 6,
    ) -> None:
        """
        Poll price every PRICE_POLL_INTERVAL seconds and trigger sell logic.

        Sell triggers (in evaluation order):
          1. Emergency stop-loss   (-40 % from entry in < 60 s)
          2. Liquidity emergency   (liquidity drops > 50 %)
          3. Take-profit ladder    (2x, 5x, 10x partial sells)
          4. Time stop             (> 30 min, < 50 % profit)
          5. Trailing stop         (% below highest_price, tier-based)
        """
        logger.info(
            f"Monitor started: {symbol} trade_id={trade_id} "
            f"entry=${entry_price:.8f}"
        )
        entry_liq_usd: float  = 0.0
        entry_time            = time.monotonic()
        highest_price         = entry_price
        remaining_tokens      = tokens_received
        initial_liq_fetched   = False

        while True:
            await asyncio.sleep(PRICE_POLL_INTERVAL)

            # Re-read trade to check if it was externally closed
            if self._portfolio_tracker:
                trade = await self._portfolio_tracker.get_trade(trade_id)
                if not trade or trade.status != "open":
                    logger.info(f"Monitor: trade_id={trade_id} closed externally — stopping monitor.")
                    break

            price_data = await self._fetch_price(mint_address)
            if not price_data or price_data.price_usd <= 0:
                continue  # retry next tick

            current_price = price_data.price_usd
            current_liq   = price_data.liquidity_usd

            # Capture initial liquidity baseline
            if not initial_liq_fetched and current_liq > 0:
                entry_liq_usd      = current_liq
                initial_liq_fetched = True

            # Track highest price
            if current_price > highest_price:
                highest_price = current_price

            # Update portfolio
            if self._portfolio_tracker and remaining_tokens > 0:
                current_value = remaining_tokens * current_price  # in USD; approximate
                # Convert to SOL estimate using entry rate
                sol_value = position_sol * (current_price / entry_price) if entry_price > 0 else 0
                await self._portfolio_tracker.update_price(
                    trade_id, current_price, sol_value, highest_price
                )

            # ── 1. Emergency: price drop > 40 % from entry in first 60 s ──
            elapsed = time.monotonic() - entry_time
            if entry_price > 0:
                drop_pct = (entry_price - current_price) / entry_price * 100.0
                if drop_pct >= EMERGENCY_STOP_DROP_PCT and elapsed < EMERGENCY_STOP_WINDOW_SECS:
                    logger.warning(
                        f"Monitor {symbol}: EMERGENCY STOP "
                        f"drop={drop_pct:.1f}% in {elapsed:.0f}s"
                    )
                    await self._do_sell_all(
                        trade_id, mint_address, symbol,
                        remaining_tokens, token_decimals,
                        "emergency_stop_loss", current_price, entry_price,
                        position_sol, status="emergency_sold",
                    )
                    break

            # ── 2. Emergency: liquidity drop > 50 % ──────────────────────
            if entry_liq_usd > 0 and current_liq > 0:
                liq_drop_pct = (entry_liq_usd - current_liq) / entry_liq_usd * 100.0
                if liq_drop_pct >= 50.0:
                    logger.warning(
                        f"Monitor {symbol}: LIQUIDITY EMERGENCY "
                        f"liq_drop={liq_drop_pct:.1f}%"
                    )
                    await self._do_sell_all(
                        trade_id, mint_address, symbol,
                        remaining_tokens, token_decimals,
                        "emergency_liquidity_drop", current_price, entry_price,
                        position_sol, status="emergency_sold",
                    )
                    break

            # ── 3. Take-profit ladder ─────────────────────────────────────
            if self._portfolio_tracker:
                trade = await self._portfolio_tracker.get_trade(trade_id)
                rungs_hit = trade.tp_rungs_hit if trade else []
            else:
                rungs_hit = []

            sold_ladder = False
            for rung_mult, rung_frac in TP_LADDER:
                if rung_mult in rungs_hit:
                    continue
                if entry_price > 0 and current_price >= entry_price * rung_mult:
                    sell_tokens = remaining_tokens * rung_frac
                    sell_sol_est = position_sol * rung_mult * rung_frac
                    logger.info(
                        f"Monitor {symbol}: TP rung {rung_mult}x hit "
                        f"— selling {sell_tokens:.2f} tokens"
                    )
                    sell_result = await self.execute_sell(
                        trade_id=trade_id,
                        mint_address=mint_address,
                        symbol=symbol,
                        token_amount=sell_tokens,
                        token_decimals=token_decimals,
                        exit_reason=f"take_profit_{rung_mult}x",
                    )
                    if sell_result.success:
                        remaining_tokens -= sell_tokens
                        if self._portfolio_tracker:
                            await self._portfolio_tracker.record_tp_rung_hit(
                                trade_id, rung_mult
                            )
                        await self._notify_sell(
                            symbol, entry_price, current_price,
                            sell_result.exit_sol, sell_result.exit_sol - position_sol * rung_frac,
                            f"Take Profit {rung_mult}x",
                        )
                    sold_ladder = True

            # ── 4. Time stop ──────────────────────────────────────────────
            hold_minutes = elapsed / 60.0
            if hold_minutes > TIME_STOP_MINUTES and entry_price > 0:
                profit_pct = (current_price - entry_price) / entry_price * 100.0
                if profit_pct < TIME_STOP_MIN_PROFIT_PCT:
                    logger.info(
                        f"Monitor {symbol}: TIME STOP "
                        f"held={hold_minutes:.0f}m profit={profit_pct:.1f}%"
                    )
                    await self._do_sell_all(
                        trade_id, mint_address, symbol,
                        remaining_tokens, token_decimals,
                        "time_stop", current_price, entry_price,
                        position_sol,
                    )
                    break

            # ── 5. Trailing stop ──────────────────────────────────────────
            if entry_price > 0 and highest_price > 0:
                profit_mult = current_price / entry_price
                stop_floor  = self._trailing_stop_floor(profit_mult)
                if stop_floor and current_price < highest_price * stop_floor:
                    drop_from_high = (highest_price - current_price) / highest_price * 100.0
                    logger.info(
                        f"Monitor {symbol}: TRAILING STOP "
                        f"drop={drop_from_high:.1f}% from high"
                    )
                    await self._do_sell_all(
                        trade_id, mint_address, symbol,
                        remaining_tokens, token_decimals,
                        "trailing_stop", current_price, entry_price,
                        position_sol,
                    )
                    break

            # Exit if all tokens sold via ladder
            if remaining_tokens <= 0:
                logger.info(f"Monitor {symbol}: all tokens sold via ladder — stopping.")
                break

        self._active_trades.discard(mint_address)
        self._monitor_tasks.pop(mint_address, None)
        logger.info(f"Monitor stopped: {symbol} trade_id={trade_id}")

    def _trailing_stop_floor(self, profit_mult: float) -> Optional[float]:
        """
        Return the stop-loss floor fraction of highest_price
        for the current profit multiplier, or None if not in profit.
        """
        if profit_mult < 1.0:
            return None   # still at a loss — trailing stop not applied
        for min_mult, floor in TRAILING_STOP_TIERS:
            if profit_mult >= min_mult:
                return floor
        return None

    async def _do_sell_all(
        self,
        trade_id: int,
        mint_address: str,
        symbol: str,
        token_amount: float,
        token_decimals: int,
        exit_reason: str,
        current_price: float,
        entry_price: float,
        position_sol: float,
        status: str = "closed",
    ) -> None:
        """Sell remaining position and send alert."""
        result = await self.execute_sell(
            trade_id=trade_id,
            mint_address=mint_address,
            symbol=symbol,
            token_amount=token_amount,
            token_decimals=token_decimals,
            exit_reason=exit_reason,
            status=status,
        )
        if result.success:
            pnl_sol = result.exit_sol - position_sol
            pnl_pct = (pnl_sol / position_sol * 100.0) if position_sol > 0 else 0.0
            await self._notify_sell(
                symbol, entry_price, current_price,
                result.exit_sol, pnl_sol, exit_reason,
                pnl_pct=pnl_pct,
            )
        else:
            await self._alert(
                f"⚠️ *SELL FAILED*\nToken: {symbol}\n"
                f"Reason: {exit_reason}\nError: {result.error}"
            )

    async def _notify_sell(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        exit_sol: float,
        pnl_sol: float,
        exit_reason: str,
        pnl_pct: float = 0.0,
    ) -> None:
        """Format and send the sell Telegram alert."""
        daily_pnl = 0.0
        if self._portfolio_tracker:
            daily_pnl = await self._portfolio_tracker.get_daily_pnl()

        pnl_emoji = "🟢" if pnl_sol >= 0 else "🔴"

        msg = (
            f"🔴 *AUTO-SELL: {exit_reason.replace('_', ' ').upper()}*\n"
            f"Token: *{symbol}*\n"
            f"Entry: `${entry_price:.8f}`\n"
            f"Exit:  `${exit_price:.8f}`\n"
            f"{pnl_emoji} P&L: `{pnl_sol:+.4f} SOL` ({pnl_pct:+.1f}%)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Portfolio Today: `{daily_pnl:+.4f} SOL`"
        )
        await self._alert(msg)

    async def _alert(self, text: str) -> None:
        if self._alert_fn:
            try:
                await self._alert_fn(text)
            except Exception as exc:
                logger.error(f"SwapEngine alert failed: {exc}")

    # ── Buy alert formatter ────────────────────────────────────────────────

    @staticmethod
    def format_buy_alert(
        symbol: str,
        sol_amount: float,
        entry_price: float,
        score: float,
    ) -> str:
        return (
            f"🟢 *AUTO-BUY EXECUTED*\n"
            f"Token: *{symbol}*\n"
            f"Amount: `{sol_amount:.4f} SOL`\n"
            f"Entry: `${entry_price:.8f}`\n"
            f"Signal Score: `{score:.0f}/100`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Take Profit Targets:\n"
            f"├── 2x  → sell 25%\n"
            f"├── 5x  → sell 25%\n"
            f"├── 10x → sell 25%\n"
            f"└── trailing stop → 25%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ Stop Loss: -40% emergency\n"
            f"⏱️ Time Stop: 30 min"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

swap_engine = SwapEngine()
