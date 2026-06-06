# core/social_scanner.py
"""
Enhancement 2 — Social Signals (Twitter / X)

Scans Twitter v2 API for mentions of a token and produces sentiment,
velocity, viral-reach, and influencer scores.

Gracefully degrades to a neutral zero-score result when:
  - TWITTER_BEARER_TOKEN is missing or is still the placeholder value.
  - The Twitter API is unreachable or returns an error.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import httpx
from loguru import logger

from config.settings import settings

# ---------------------------------------------------------------------------
# Keyword lists for sentiment scoring
# ---------------------------------------------------------------------------
_POSITIVE_KEYWORDS = frozenset(
    {"moon", "gem", "buy", "bullish", "pump", "100x", "launch", "alpha", "ape", "send"}
)
_NEGATIVE_KEYWORDS = frozenset(
    {"rug", "scam", "dump", "honeypot", "avoid", "rugpull", "exit", "caution", "bearish", "fraud"}
)

# Thresholds
_INFLUENCER_FOLLOWERS_THRESHOLD: int = 10_000
_VIRAL_LIKE_THRESHOLD: int = 100


@dataclass
class SocialSignals:
    token_symbol: str
    token_address: str
    mention_count_1h: int        # total mentions in the last hour
    mention_velocity: float      # mentions per minute over the last 15 min
    sentiment_score: float       # 0–100 (higher = more positive)
    has_viral_tweet: bool        # any tweet with > 100 likes in the last hour
    influencer_mentioned: bool   # any author with > 10 k followers mentioned it
    social_score: float          # 0–100 composite score


class SocialScanner:
    """
    Fetches and analyses Twitter/X data for a Solana token.

    All methods are fully exception-safe — callers never receive an exception;
    they receive a neutral ``SocialSignals`` instead.
    """

    _TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

    def __init__(self) -> None:
        self._bearer_token: str = getattr(settings, "TWITTER_BEARER_TOKEN", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _has_valid_token(self) -> bool:
        """Return True only when a non-placeholder bearer token is configured."""
        return bool(
            self._bearer_token
            and not self._bearer_token.startswith("your_")
        )

    @staticmethod
    def _neutral_signals(token_symbol: str, token_address: str) -> SocialSignals:
        """Return a zero-score neutral result used as a safe fallback."""
        return SocialSignals(
            token_symbol=token_symbol,
            token_address=token_address,
            mention_count_1h=0,
            mention_velocity=0.0,
            sentiment_score=50.0,
            has_viral_tweet=False,
            influencer_mentioned=False,
            social_score=0.0,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_twitter(
        self, query: str, minutes_back: int = 60
    ) -> list[dict]:
        """
        Search recent tweets via Twitter API v2.

        Parameters
        ----------
        query        : Twitter search query string.
        minutes_back : How many minutes back to search (default 60 = 1 h).

        Returns a list of tweet dicts.  Each dict has an extra
        ``_author_followers`` key injected from the users expansion.
        Returns ``[]`` on any error — never raises.
        """
        if not self._has_valid_token():
            logger.debug("TWITTER_BEARER_TOKEN not configured — skipping Twitter search.")
            return []

        start_time = (
            datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "query": query,
            "max_results": 100,
            "tweet.fields": "public_metrics,author_id,created_at,text",
            "expansions": "author_id",
            "user.fields": "public_metrics",
            "start_time": start_time,
        }
        headers = {"Authorization": f"Bearer {self._bearer_token}"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    self._TWITTER_SEARCH_URL, params=params, headers=headers
                )
                if resp.status_code == 200:
                    body = resp.json()

                    # Twitter v2 returns {"data": null} when there are no results
                    raw_tweets = body.get("data")
                    tweets: list[dict] = raw_tweets if isinstance(raw_tweets, list) else []

                    # Build a user-id → user-object map from the expansions
                    users_by_id: dict[str, dict] = {
                        u["id"]: u
                        for u in body.get("includes", {}).get("users", [])
                        if "id" in u
                    }

                    # Attach follower count directly to each tweet for easy access
                    for tweet in tweets:
                        author_id = tweet.get("author_id", "")
                        user_obj = users_by_id.get(author_id, {})
                        tweet["_author_followers"] = (
                            user_obj
                            .get("public_metrics", {})
                            .get("followers_count", 0)
                        )

                    logger.debug(
                        f"Twitter search '{query}': {len(tweets)} tweet(s) found"
                    )
                    return tweets

                elif resp.status_code == 429:
                    logger.warning("Twitter API rate limit hit — returning empty result.")
                else:
                    logger.warning(
                        f"Twitter API returned HTTP {resp.status_code} "
                        f"for query '{query}': {resp.text[:200]}"
                    )
        except Exception as exc:
            logger.error(f"Twitter search error for '{query}': {exc}")

        return []

    async def analyze_social(
        self, token_symbol: str, token_address: str
    ) -> SocialSignals:
        """
        Full social analysis for a token.

        Searches for ``$SYMBOL OR <first-8-chars-of-address>``, then
        computes mention velocity, sentiment, viral/influencer flags, and a
        composite ``social_score`` (0–100).

        Always returns a ``SocialSignals`` — never raises.
        """
        if not self._has_valid_token():
            logger.info(
                f"No Twitter token — returning neutral SocialSignals for {token_symbol}"
            )
            return self._neutral_signals(token_symbol, token_address)

        query = f"${token_symbol} OR {token_address[:8]}"
        try:
            tweets = await self.search_twitter(query, minutes_back=60)
        except Exception as exc:
            logger.error(f"analyze_social: search raised unexpectedly: {exc}")
            return self._neutral_signals(token_symbol, token_address)

        if not tweets:
            return self._neutral_signals(token_symbol, token_address)

        now = datetime.now(timezone.utc)
        cutoff_15m = now - timedelta(minutes=15)

        mention_count_1h: int = len(tweets)
        tweets_last_15m: int = 0
        positive_count: int = 0
        negative_count: int = 0
        has_viral_tweet: bool = False
        influencer_mentioned: bool = False

        for tweet in tweets:
            text_lower: str = tweet.get("text", "").lower()
            metrics: dict = tweet.get("public_metrics") or {}
            like_count: int = int(metrics.get("like_count", 0))
            author_followers: int = int(tweet.get("_author_followers", 0))
            created_at_str: str = tweet.get("created_at", "")

            # ── 15-minute velocity window ──────────────────────────────
            if created_at_str:
                try:
                    tweet_dt = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )
                    if tweet_dt >= cutoff_15m:
                        tweets_last_15m += 1
                except (ValueError, TypeError):
                    pass

            # ── Sentiment (one point per tweet, not per keyword) ───────
            if any(kw in text_lower for kw in _POSITIVE_KEYWORDS):
                positive_count += 1
            if any(kw in text_lower for kw in _NEGATIVE_KEYWORDS):
                negative_count += 1

            # ── Viral & influencer flags ───────────────────────────────
            if like_count > _VIRAL_LIKE_THRESHOLD:
                has_viral_tweet = True
            if author_followers > _INFLUENCER_FOLLOWERS_THRESHOLD:
                influencer_mentioned = True

        # ── Derived metrics ────────────────────────────────────────────
        # Velocity: tweets per minute in the last 15-minute window
        mention_velocity: float = round(tweets_last_15m / 15.0, 4)

        # Sentiment score: 50 = neutral; ±5 per net positive/negative tweet
        sentiment_score: float = max(
            0.0, min(100.0, 50.0 + (positive_count - negative_count) * 5.0)
        )

        # ── Composite social score (0–100) ─────────────────────────────
        social_score: float = 0.0
        social_score += min(mention_count_1h / 2.0, 30.0)    # max 30 pts
        social_score += min(mention_velocity * 10.0, 20.0)   # max 20 pts
        social_score += sentiment_score * 0.3                 # max 30 pts
        social_score += 10.0 if has_viral_tweet else 0.0
        social_score += 10.0 if influencer_mentioned else 0.0
        social_score = round(min(100.0, social_score), 2)

        logger.info(
            f"SocialSignals for ${token_symbol}: "
            f"mentions_1h={mention_count_1h}, velocity={mention_velocity:.2f}/min, "
            f"sentiment={sentiment_score:.1f}, viral={has_viral_tweet}, "
            f"influencer={influencer_mentioned}, score={social_score}"
        )

        return SocialSignals(
            token_symbol=token_symbol,
            token_address=token_address,
            mention_count_1h=mention_count_1h,
            mention_velocity=mention_velocity,
            sentiment_score=sentiment_score,
            has_viral_tweet=has_viral_tweet,
            influencer_mentioned=influencer_mentioned,
            social_score=social_score,
        )
