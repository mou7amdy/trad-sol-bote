import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiosqlite
import httpx
from loguru import logger

import config.settings as _cfg

BACKTEST_DB_PATH = _cfg.BASE_DIR / "data" / "backtest.db"

HISTORICAL_TOKENS_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_tokens (
    mint TEXT,
    symbol TEXT,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    initial_liquidity REAL,
    initial_mcap REAL,
    initial_price REAL,
    initial_volume_1h REAL,
    buy_sell_ratio REAL,
    tx_count_1h INTEGER,
    price_change_5m REAL,
    price_change_1h REAL,
    security_score REAL,
    top10_holders_pct REAL,
    lp_burned INTEGER,
    mint_revoked INTEGER,
    price_1h REAL,
    price_6h REAL,
    price_24h REAL,
    max_price_24h REAL,
    rug_pulled INTEGER,
    label_pump INTEGER,
    label_rug INTEGER,
    label_entry_minutes INTEGER
);
"""


@dataclass
class TokenSnapshot:
    mint: str
    symbol: str
    initial_liquidity: float = 0.0
    initial_mcap: float = 0.0
    initial_price: float = 0.0
    initial_volume_1h: float = 0.0
    buy_sell_ratio: float = 1.0
    tx_count_1h: int = 0
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    security_score: float = 50.0
    top10_holders_pct: float = 0.0
    lp_burned: bool = False
    mint_revoked: bool = False
    price_1h: float = 0.0
    price_6h: float = 0.0
    price_24h: float = 0.0
    max_price_24h: float = 0.0
    rug_pulled: bool = False
    label_pump: int = 0
    label_rug: int = 0
    label_entry_minutes: int = 0


class HistoricalDataCollector:
    BASE_URL = "https://api.dexscreener.com/latest/dex"

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._session: Optional[httpx.AsyncClient] = None
        self._seen_mints: set[str] = set()

    async def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            BACKTEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(str(BACKTEST_DB_PATH))
            self._db.row_factory = aiosqlite.Row
            await self._db.execute(HISTORICAL_TOKENS_SCHEMA)
            await self._db.commit()
        return self._db

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session is None:
            self._session = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self._session

    async def fetch_new_pairs(self, chain: str = "solana") -> list[dict]:
        session = await self._ensure_session()
        url = f"{self.BASE_URL}/pairs/{chain}"
        try:
            resp = await session.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                filtered = []
                for p in pairs:
                    txns = p.get("txns", {})
                    h24 = txns.get("h24", {})
                    buys = int(h24.get("buys", 0))
                    sells = int(h24.get("sells", 0))
                    total_tx_24h = buys + sells
                    liq = float(p.get("liquidity", {}).get("usd", 0))
                    if liq > 5000 and total_tx_24h > 100:
                        filtered.append(p)
                logger.info(f"fetch_new_pairs: {len(filtered)}/{len(pairs)} passed filters")
                return filtered
            else:
                logger.warning(f"DexScreener pairs HTTP {resp.status_code}")
        except Exception as exc:
            logger.error(f"fetch_new_pairs error: {exc}")
        return []

    async def fetch_pair_detail(self, pair_address: str) -> dict:
        session = await self._ensure_session()
        url = f"{self.BASE_URL}/pairs/solana/{pair_address}"
        try:
            resp = await session.get(url)
            if resp.status_code == 200:
                return resp.json().get("pair", {})
        except Exception as exc:
            logger.error(f"fetch_pair_detail error: {exc}")
        return {}

    async def record_snapshot(self, snap: TokenSnapshot) -> None:
        db = await self._ensure_db()
        mint = snap.mint
        if mint in self._seen_mints:
            return
        self._seen_mints.add(mint)
        await db.execute(
            """
            INSERT OR REPLACE INTO historical_tokens
                (mint, symbol, initial_liquidity, initial_mcap, initial_price,
                 initial_volume_1h, buy_sell_ratio, tx_count_1h,
                 price_change_5m, price_change_1h, security_score,
                 top10_holders_pct, lp_burned, mint_revoked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mint, snap.symbol,
                snap.initial_liquidity, snap.initial_mcap, snap.initial_price,
                snap.initial_volume_1h, snap.buy_sell_ratio, snap.tx_count_1h,
                snap.price_change_5m, snap.price_change_1h, snap.security_score,
                snap.top10_holders_pct, int(snap.lp_burned), int(snap.mint_revoked),
            ),
        )
        await self._db.commit()

    async def update_outcomes(self, mint: str, snap: TokenSnapshot) -> None:
        db = await self._ensure_db()
        await db.execute(
            """
            UPDATE historical_tokens SET
                price_1h = ?, price_6h = ?, price_24h = ?,
                max_price_24h = ?, rug_pulled = ?,
                label_pump = ?, label_rug = ?, label_entry_minutes = ?
            WHERE mint = ?
            """,
            (
                snap.price_1h, snap.price_6h, snap.price_24h,
                snap.max_price_24h, int(snap.rug_pulled),
                snap.label_pump, snap.label_rug, snap.label_entry_minutes,
                mint,
            ),
        )
        await self._db.commit()

    async def collect_dataset(self, n_tokens: int = 5000) -> int:
        saved = 0
        page = 0
        max_pages = n_tokens // 50 + 10

        while saved < n_tokens and page < max_pages:
            pairs = await self.fetch_new_pairs()
            if not pairs:
                logger.info("No new pairs found, waiting 30s...")
                await asyncio.sleep(30)
                continue

            for p in pairs:
                if saved >= n_tokens:
                    break

                mint = p.get("baseToken", {}).get("address", "")
                if not mint or mint in self._seen_mints:
                    continue

                symbol = p.get("baseToken", {}).get("symbol", "UNKNOWN")
                snap = TokenSnapshot(mint=mint, symbol=symbol)
                snap.initial_price = float(p.get("priceUsd", 0))
                snap.initial_liquidity = float(p.get("liquidity", {}).get("usd", 0))
                snap.initial_volume_1h = float(p.get("volume", {}).get("h24", 0))
                snap.initial_mcap = float(p.get("marketCap", 0))
                snap.buy_sell_ratio = 1.0
                snap.tx_count_1h = 0

                txns = p.get("txns", {})
                h1 = txns.get("h1", {})
                snap.price_change_5m = float(p.get("priceChange", {}).get("m5", 0))
                snap.price_change_1h = float(p.get("priceChange", {}).get("h1", 0))

                await self.record_snapshot(snap)
                saved += 1

                if saved % 100 == 0:
                    logger.info(f"Collected {saved}/{n_tokens} tokens...")
                    await asyncio.sleep(1)

            page += 1
            await asyncio.sleep(2)

        logger.info(f"Collection complete: {saved} tokens saved")
        return saved

    async def label_outcomes(self) -> int:
        db = await self._ensure_db()
        async with db.execute(
            "SELECT mint, initial_price, price_24h, max_price_24h FROM historical_tokens "
            "WHERE label_pump = 0 AND initial_price > 0"
        ) as cur:
            rows = await cur.fetchall()

        labeled = 0
        for row in rows:
            mint = row["mint"]
            initial = float(row["initial_price"])
            price_24h = float(row["price_24h"] or 0)
            max_24h = float(row["max_price_24h"] or 0)

            label_pump = 1 if max_24h >= initial * 2.0 else 0
            label_rug = 1 if (initial > 0 and price_24h <= initial * 0.1) else 0

            entry_minutes = 0
            if max_24h > initial:
                ratio = max_24h / initial
                if ratio >= 2.0:
                    entry_minutes = max(1, int(5 * (ratio / 2.0)))

            await db.execute(
                "UPDATE historical_tokens SET label_pump=?, label_rug=?, label_entry_minutes=? WHERE mint=?",
                (label_pump, label_rug, entry_minutes, mint),
            )
            labeled += 1

        await db.commit()
        logger.info(f"Labeled {labeled} tokens")
        return labeled

    async def stop(self) -> None:
        if self._session:
            await self._session.aclose()
        if self._db:
            await self._db.close()

    async def __aenter__(self) -> "HistoricalDataCollector":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()
