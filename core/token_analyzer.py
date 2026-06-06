import httpx
import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, List
from loguru import logger
from config.settings import settings
from core.solana_scanner import TokenInfo

class AnalysisResult:
    def __init__(self, rsi: float, volume_spike_ratio: float, momentum_score: float, confidence_score: float):
        self.rsi = rsi
        self.volume_spike_ratio = volume_spike_ratio
        self.momentum_score = momentum_score
        self.confidence_score = confidence_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rsi": self.rsi,
            "volume_spike_ratio": self.volume_spike_ratio,
            "momentum_score": self.momentum_score,
            "confidence_score": self.confidence_score
        }

async def get_ohlcv(mint_address: str, timeframe: str = "1m", limit: int = 100) -> Dict[str, List[float]]:
    """
    Fetch OHLCV data from Birdeye API.
    Returns lists of: closes, volumes
    """
    url = f"https://public-api.birdeye.so/defi/history_price?address={mint_address}&address_type=token&type={timeframe}&time_to=0"
    headers = {
        "x-chain": "solana"
    }
    if settings.BIRDEYE_API_KEY and settings.BIRDEYE_API_KEY != "your_birdeye_api_key_here":
        headers["X-API-KEY"] = settings.BIRDEYE_API_KEY
    else:
        # Fallback to mock data for local testing
        import random
        closes = [100.0 + random.uniform(-2, 5) for _ in range(limit)]
        # Add a volume spike at the end
        volumes = [random.uniform(500, 2000) for _ in range(limit - 1)]
        volumes.append(random.uniform(6000, 10000))  # 5x volume spike!
        return {"closes": closes, "volumes": volumes}
        
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json().get("data", {}).get("items", [])
                closes = [float(item.get("value", 0.0)) for item in data]
                volumes = [float(item.get("volume", 0.0)) for item in data]
                closes.reverse()
                volumes.reverse()
                return {"closes": closes, "volumes": volumes}
    except Exception as e:
        logger.error(f"Error fetching OHLCV from Birdeye: {e}")
        
    return {"closes": [1.0] * limit, "volumes": [100.0] * limit}

def calculate_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period:
        return 50.0
    try:
        df = pd.DataFrame(closes, columns=["close"])
        rsi_series = ta.rsi(df["close"], length=period)
        if rsi_series is not None and not rsi_series.empty:
            val = rsi_series.iloc[-1]
            if pd.isna(val):
                return 50.0
            return float(val)
    except Exception as e:
        logger.error(f"Error calculating RSI: {e}")
    return 50.0

def calculate_volume_spike(volumes: List[float]) -> float:
    if len(volumes) < 5:
        return 1.0
    try:
        latest_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
        if avg_volume == 0.0:
            return 1.0
        return latest_volume / avg_volume
    except Exception as e:
        logger.error(f"Error calculating volume spike: {e}")
    return 1.0

def calculate_price_momentum(closes: List[float]) -> float:
    if len(closes) < 26:
        return 50.0
    try:
        df = pd.DataFrame(closes, columns=["close"])
        macd_df = ta.macd(df["close"])
        if macd_df is not None and not macd_df.empty:
            macd_line = macd_df.iloc[-1, 0]
            signal_line = macd_df.iloc[-1, 1]
            histogram = macd_df.iloc[-1, 2]
            
            score = 50.0
            if macd_line > signal_line:
                score += 25.0
            if histogram > 0:
                score += 25.0
            return score
    except Exception as e:
        logger.error(f"Error calculating price momentum: {e}")
    return 50.0

async def analyze_token(token_info: TokenInfo) -> AnalysisResult:
    """
    Main analysis function.
    """
    logger.info(f"Analyzing technicals for: {token_info.address}")
    
    chart_data = await get_ohlcv(token_info.address)
    closes = chart_data["closes"]
    volumes = chart_data["volumes"]
    
    rsi = calculate_rsi(closes)
    volume_spike = calculate_volume_spike(volumes)
    momentum = calculate_price_momentum(closes)
    
    # Calculate overall confidence score (0-100)
    volume_points = min(volume_spike / 5.0, 1.0) * 40.0
    
    if 45.0 <= rsi <= 70.0:
        rsi_points = 30.0
    elif rsi > 70.0:
        rsi_points = 15.0
    else:
        rsi_points = min(rsi / 45.0, 1.0) * 20.0
        
    momentum_points = (momentum / 100.0) * 30.0
    
    confidence_score = volume_points + rsi_points + momentum_points
    
    logger.info(f"Analysis complete. RSI: {rsi:.1f}, Volume Spike: {volume_spike:.2f}x, Momentum Score: {momentum:.1f}, Confidence: {confidence_score:.1f}")
    return AnalysisResult(rsi=rsi, volume_spike_ratio=volume_spike, momentum_score=momentum, confidence_score=confidence_score)
