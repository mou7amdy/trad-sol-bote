import asyncio
import json
import base64
import httpx
import websockets
from typing import Any, Callable, Dict, List, Optional
from loguru import logger
from config.settings import settings

RAYDIUM_PROGRAM_ID = "675k1g2EPJ8gdd9gBNjafBNwbPCixgNbbAbMsG6iyK4H"

# Raydium AMM v4 initialize2 layout offsets for coin/pc mint
# Layout: [u8 discriminator(1)] + [InitializeInstruction2 body]
# coin_mint is at offset 41 (32 bytes), pc_mint at offset 73 (32 bytes)
_COIN_MINT_OFFSET = 41
_PC_MINT_OFFSET = 73
# Known stable-coin mints to exclude (we want the NEW token, not the pair)
_STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",   # Wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", # mSOL
}


class TokenInfo:
    def __init__(
        self,
        address: str,
        symbol: str,
        name: str,
        price: float = 0.0,
        liquidity_usd: float = 0.0,
        market_cap: float = 0.0,
        dex_source: str = "Raydium_AMM",
        detection_latency_ms: float = 0.0,
    ) -> None:
        self.address = address
        self.symbol = symbol
        self.name = name
        self.price = price
        self.liquidity_usd = liquidity_usd
        self.market_cap = market_cap
        self.dex_source = dex_source
        self.detection_latency_ms = detection_latency_ms
        self.buy_sell_ratio: float = 1.0
        self.volume_5m: float = 0.0
        self.top10_holders_pct: float = 0.0
        self.lp_burned: bool = False
        self.mint_revoked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "liquidity_usd": self.liquidity_usd,
            "market_cap": self.market_cap,
            "dex_source": self.dex_source,
            "detection_latency_ms": self.detection_latency_ms,
        }


def _parse_mint_from_transaction(
    transaction_data: Dict[str, Any]
) -> Optional[str]:
    """
    Fix #1 — extract the real mint address from a Raydium pool creation
    transaction returned by Helius.

    Strategy:
    1. Walk the accountKeys list in the transaction message.
    2. For each instruction whose programId is RAYDIUM_PROGRAM_ID, read
       `data` (base58/base64 encoded), decode, and extract coin_mint /
       pc_mint from the known layout offsets.
    3. Return whichever mint is NOT a known stable-coin; if both are
       unknown, prefer coin_mint.

    Falls back to None if parsing fails (caller skips the event).
    """
    try:
        meta = transaction_data.get("meta", {})
        message = transaction_data.get("transaction", {}).get("message", {})
        account_keys: List[str] = message.get("accountKeys", [])

        for ix in message.get("instructions", []):
            program_idx = ix.get("programIdIndex", -1)
            if program_idx < 0 or program_idx >= len(account_keys):
                continue
            if account_keys[program_idx] != RAYDIUM_PROGRAM_ID:
                continue

            raw_data_b58: str = ix.get("data", "")
            if not raw_data_b58:
                continue

            # Helius returns instruction data as base58; decode it
            try:
                import base58  # type: ignore[import]
                raw_bytes: bytes = base58.b58decode(raw_data_b58)
            except Exception:
                # Fallback: try base64
                try:
                    raw_bytes = base64.b64decode(raw_data_b58)
                except Exception:
                    continue

            if len(raw_bytes) < _PC_MINT_OFFSET + 32:
                continue

            coin_mint_bytes = raw_bytes[_COIN_MINT_OFFSET: _COIN_MINT_OFFSET + 32]
            pc_mint_bytes = raw_bytes[_PC_MINT_OFFSET: _PC_MINT_OFFSET + 32]

            import base58 as _b58  # type: ignore[import]
            coin_mint = _b58.b58encode(coin_mint_bytes).decode()
            pc_mint = _b58.b58encode(pc_mint_bytes).decode()

            if coin_mint not in _STABLE_MINTS:
                return coin_mint
            if pc_mint not in _STABLE_MINTS:
                return pc_mint
            # Both stable — likely not a new listing; skip
            return None

    except Exception as e:
        logger.error(f"Error parsing mint from transaction: {e}")

    return None


async def _fetch_transaction(signature: str) -> Optional[Dict[str, Any]]:
    """Fetch full transaction details from the Helius RPC."""
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(settings.SOLANA_RPC_URL, json=payload)
            if response.status_code == 200:
                data = response.json()
                return data.get("result")
    except Exception as e:
        logger.error(f"Error fetching transaction {signature}: {e}")
    return None


async def get_token_info(mint_address: str) -> "TokenInfo":
    """Fetch token info from DexScreener (free, no API key)."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_address}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                pairs = data.get("pairs") or []
                solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                if not solana_pairs:
                    return TokenInfo(address=mint_address, symbol="UNKNOWN", name="Unknown Token")
                best = max(solana_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
                base = best.get("baseToken", {})
                return TokenInfo(
                    address=mint_address,
                    symbol=base.get("symbol", "UNKNOWN"),
                    name=base.get("name", "Unknown Token"),
                    price=float(best.get("priceUsd", 0) or 0),
                    liquidity_usd=float(best.get("liquidity", {}).get("usd", 0) or 0),
                    market_cap=float(best.get("marketCap", 0) or 0) or float(best.get("fdv", 0) or 0),
                )
    except Exception as e:
        logger.error(f"Error fetching token info from DexScreener: {e}")

    return TokenInfo(address=mint_address, symbol="UNKNOWN", name="Unknown Token")


async def get_top_holders(mint_address: str) -> float:
    """Fetch top 10 holders percentage using Helius RPC."""
    url = settings.SOLANA_RPC_URL
    if "api.mainnet-beta.solana.com" in url:
        # Public RPC often rate-limits these calls; return a safe default
        return 15.0

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint_address],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                value: List[Dict[str, Any]] = (
                    response.json().get("result", {}).get("value", [])
                )
                supply_payload = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "getTokenSupply",
                    "params": [mint_address],
                }
                supply_response = await client.post(url, json=supply_payload)
                if supply_response.status_code == 200:
                    total_supply = float(
                        supply_response.json()
                        .get("result", {})
                        .get("value", {})
                        .get("amount", 1.0)
                    )
                    top10_sum = sum(
                        float(acc.get("amount", 0.0)) for acc in value[:10]
                    )
                    return (top10_sum / total_supply) * 100.0
    except Exception as e:
        logger.error(f"Error fetching top holders from RPC: {e}")

    return 15.0


async def monitor_new_listings(
    callback: Callable[["TokenInfo"], Any]
) -> None:
    """
    WebSocket listener for new Raydium pool creation events.
    Falls back to mock generation if credentials are default/placeholders.
    """
    wss_url = settings.SOLANA_WSS_URL
    is_placeholder = (
        not wss_url
        or wss_url.startswith("wss://api.mainnet-beta")
        or "your_" in wss_url
    )

    if is_placeholder:
        logger.warning(
            "Using placeholder WebSocket URL. "
            "Launching MOCK pool listener for local verification..."
        )
        import random

        mock_mints = [
            "DezXAZ8z7PnrFcPykJziH6WGHe1qZrUGLGQc1Y7daB2k",
            "HeLPr5cvjUA9Z6tKjgDQ3C6J58P3PZ63G6oYp98Zmint",
            "MOCKsf8v7PnrFcPykJziH6WGHe1qZrUGLGQc1Y7damint",
        ]
        while True:
            await asyncio.sleep(5.0)
            mock_addr = random.choice(mock_mints)
            token_info = TokenInfo(
                address=mock_addr,
                symbol="MOCK",
                name="Mock Raydium Listing",
                price=0.001,
                liquidity_usd=15000.0,
                market_cap=120000.0,
            )
            logger.info(f"[MOCK SCANNER] Detected mock pool for: {mock_addr}")
            asyncio.create_task(callback(token_info))
        return

    logger.info(f"Connecting to Helius WebSocket at: {wss_url}")
    while True:
        try:
            async with websockets.connect(wss_url) as websocket:
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [RAYDIUM_PROGRAM_ID]},
                        {"commitment": "finalized"},
                    ],
                }
                await websocket.send(json.dumps(subscribe_msg))
                logger.info(
                    "Successfully subscribed to Helius logs for Raydium Program."
                )

                async for message in websocket:
                    data = json.loads(message)
                    value = (
                        data.get("params", {})
                        .get("result", {})
                        .get("value", {})
                    )
                    logs: List[str] = value.get("logs", [])
                    is_initialize = any(
                        "initialize2" in log for log in logs
                    )
                    if not is_initialize:
                        continue

                    signature: Optional[str] = value.get("signature")
                    if not signature:
                        continue

                    logger.info(
                        f"New pool creation detected — signature: {signature}"
                    )

                    # Fix #1: fetch full tx and parse the real mint address
                    tx_data = await _fetch_transaction(signature)
                    if tx_data is None:
                        logger.warning(
                            f"Could not fetch tx {signature}, skipping."
                        )
                        continue

                    mint_address = _parse_mint_from_transaction(tx_data)
                    if not mint_address:
                        logger.warning(
                            f"Could not parse mint from tx {signature}, skipping."
                        )
                        continue

                    logger.info(f"Extracted mint address: {mint_address}")
                    token_info = await get_token_info(mint_address)
                    asyncio.create_task(callback(token_info))

        except websockets.exceptions.ConnectionClosed:
            logger.warning(
                "Helius WebSocket connection closed. Reconnecting in 5 s..."
            )
            await asyncio.sleep(5.0)
        except Exception as e:
            logger.error(f"Error in WebSocket listener: {e}")
            await asyncio.sleep(5.0)
