import httpx
from typing import Any, Dict, Optional, Tuple
from loguru import logger
from config.settings import settings


class SecurityResult:
    def __init__(
        self,
        score: float,
        passed: bool,
        fail_reason: str = "",
        scan_status: str = "complete",
    ) -> None:
        self.score = score
        self.passed = passed
        self.fail_reason = fail_reason
        # "complete" | "uncertain" — uncertain means at least one API call failed
        self.scan_status = scan_status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "fail_reason": self.fail_reason,
            "scan_status": self.scan_status,
        }


async def check_honeypot(
    token_address: str,
) -> Tuple[Optional[bool], float, float]:
    """
    Check if the token is a honeypot via Honeypot.is API.

    Returns: (is_honeypot, buy_tax, sell_tax)
      - is_honeypot is None when the check could not be completed (API error /
        timeout / unexpected status).  Callers MUST treat None as uncertain —
        never assume safe on failure.
    """
    url = f"https://api.honeypot.is/v2/IsHoneypot?address={token_address}"
    headers: Dict[str, str] = {}
    if settings.HONEYPOT_API_KEY and not settings.HONEYPOT_API_KEY.startswith("your_"):
        headers["X-API-KEY"] = settings.HONEYPOT_API_KEY

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                honeypot_info = data.get("honeypotResult", {})
                is_hp_raw = honeypot_info.get("isHoneypot")
                is_hp: Optional[bool] = bool(is_hp_raw) if is_hp_raw is not None else None
                simulation = data.get("simulationResult", {})
                buy_tax: float = float(simulation.get("buyTax", 0.0))
                sell_tax: float = float(simulation.get("sellTax", 0.0))
                return is_hp, buy_tax, sell_tax
            else:
                logger.warning(
                    f"Honeypot API returned status code {response.status_code} "
                    f"for {token_address}. Marking as uncertain."
                )
                return None, 0.0, 0.0
    except Exception as e:
        logger.error(
            f"Exception checking honeypot for {token_address}: {e}. "
            "Marking result as uncertain."
        )
        return None, 0.0, 0.0


async def check_goplus(token_address: str) -> Dict[str, Any]:
    """
    Check token security via GoPlus Labs API for Solana (Chain ID 1399811149).
    """
    url = (
        f"https://api.gopluslabs.io/api/v1/token_security/1399811149"
        f"?contract_addresses={token_address}"
    )
    headers: Dict[str, str] = {}
    if settings.GOPLUS_API_KEY and not settings.GOPLUS_API_KEY.startswith("your_"):
        headers["Authorization"] = f"Bearer {settings.GOPLUS_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                result_map: Dict[str, Any] = data.get("result", {})
                token_data: Dict[str, Any] = result_map.get(
                    token_address.lower(),
                    result_map.get(token_address, {}),
                )
                return token_data
            else:
                logger.warning(
                    f"GoPlus API returned status code {response.status_code} "
                    f"for {token_address}."
                )
    except Exception as e:
        logger.error(f"Error checking GoPlus security for {token_address}: {e}")

    return {}


async def check_liquidity_lock(token_address: str) -> Tuple[bool, float]:
    """
    Check if the token's liquidity is locked via Birdeye API.

    Returns: (locked, lock_duration_days)
    Defaults to (False, 0.0) on any failure or missing API key —
    never assume locked when the result is unknown.
    """
    if not settings.BIRDEYE_API_KEY or settings.BIRDEYE_API_KEY.startswith("your_"):
        logger.warning(
            "BIRDEYE_API_KEY is not configured. Liquidity lock check skipped — "
            "defaulting to unlocked (uncertain)."
        )
        return False, 0.0

    url = f"https://public-api.birdeye.so/defi/token_security?address={token_address}"
    headers: Dict[str, str] = {
        "x-chain": "solana",
        "X-API-KEY": settings.BIRDEYE_API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                sec_data: Dict[str, Any] = data.get("data", {})
                locked: bool = bool(
                    sec_data.get("is_liquidity_locked", False)
                    or sec_data.get("lp_locked", False)
                )
                lock_duration: float = float(
                    sec_data.get("lp_lock_duration_days", 0.0)
                )
                return locked, lock_duration
            else:
                logger.warning(
                    f"Birdeye Security API returned status code {response.status_code} "
                    f"for {token_address}. Defaulting to unlocked."
                )
    except Exception as e:
        logger.error(
            f"Error checking liquidity lock for {token_address}: {e}. "
            "Defaulting to unlocked."
        )

    return False, 0.0


async def full_security_scan(token_address: str) -> SecurityResult:
    """
    Performs full security checks on a token.
    Fails if: honeypot=True, sell_tax > 15%, top10 holder concentration > 80%,
    or mint authority is not renounced.
    Returns a SecurityResult with score out of 100 and scan_status.
    """
    logger.info(f"Starting security scan for: {token_address}")

    is_hp, buy_tax, sell_tax = await check_honeypot(token_address)
    goplus_data = await check_goplus(token_address)
    lp_locked, _lp_lock_duration = await check_liquidity_lock(token_address)

    fail_reasons: list[str] = []
    score: float = 100.0
    scan_status: str = "complete"

    # 1. Honeypot check — None means the API call failed (uncertain)
    if is_hp is None:
        fail_reasons.append("Honeypot check inconclusive (API error)")
        score -= 30
        scan_status = "uncertain"
    elif is_hp:
        fail_reasons.append("Honeypot detected")
        score -= 50

    # 2. Tax checks
    if buy_tax > 15.0:
        fail_reasons.append(f"Buy tax too high ({buy_tax}%)")
        score -= 15
    if sell_tax > 15.0:
        fail_reasons.append(f"Sell tax too high ({sell_tax}%)")
        score -= 20

    # 3. Holder concentration
    top10_rate_str: Any = goplus_data.get("top10_holder_rate", "0.0")
    try:
        top10_pct: float = float(top10_rate_str)
        if 0.0 < top10_pct <= 1.0:
            top10_pct *= 100.0
    except (ValueError, TypeError):
        top10_pct = 0.0

    if top10_pct > 80.0:
        fail_reasons.append(
            f"Top 10 holders concentration too high ({top10_pct:.1f}%)"
        )
        score -= 25

    # 4. Mintable / renounced authority check
    is_mintable: bool = goplus_data.get("is_mintable", "0") == "1"
    owner_address: str = goplus_data.get("owner_address", "")
    if is_mintable and owner_address and owner_address != "11111111111111111111111111111111":
        fail_reasons.append("Mint authority not renounced")
        score -= 20

    # 5. Liquidity lock check
    if not lp_locked:
        score -= 15

    # Final pass/fail — uncertain scans never auto-pass
    final_score: float = max(0.0, score)
    passed: bool = (
        len(fail_reasons) == 0
        and final_score >= 70
        and scan_status == "complete"
    )
    fail_reason_str: str = "; ".join(fail_reasons) if fail_reasons else ""

    logger.info(
        f"Security scan completed. Status: {scan_status}, Passed: {passed}, "
        f"Score: {final_score}, Reason: {fail_reason_str}"
    )
    return SecurityResult(
        score=final_score,
        passed=passed,
        fail_reason=fail_reason_str,
        scan_status=scan_status,
    )
