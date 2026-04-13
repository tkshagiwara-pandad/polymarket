"""
マーケット発見・マッチングモジュール
- Polymarket / Kalshi の全マーケットを取得
- 同一テーマのマーケットを自動マッチング（キーワード類似度）
- MARKET_PAIRS リストを動的生成
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Optional
from loguru import logger

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class RawMarket:
    market_id: str
    venue: str
    title: str
    end_date: Optional[str]
    volume: float


def _normalize(text: str) -> set[str]:
    """タイトルを正規化してトークンセットに変換"""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    stopwords = {"will", "the", "a", "an", "in", "on", "at", "to", "of",
                 "by", "be", "is", "or", "and", "for", "win", "who"}
    return {w for w in text.split() if w not in stopwords and len(w) > 2}


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard類似度でタイトルの一致度を計算"""
    sa, sb = _normalize(a), _normalize(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


async def fetch_polymarket_markets(limit: int = 100) -> list[RawMarket]:
    """Polymarket Gamma APIからアクティブマーケットを取得"""
    if not AIOHTTP_AVAILABLE:
        return _mock_polymarket()

    url = "https://gamma-api.polymarket.com/markets"
    params = {"active": "true", "closed": "false", "limit": limit}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.warning(f"Polymarket API エラー: {r.status}")
                    return _mock_polymarket()
                data = await r.json()
                return [
                    RawMarket(
                        market_id=m.get("conditionId", ""),
                        venue="polymarket",
                        title=m.get("question", ""),
                        end_date=m.get("endDate"),
                        volume=float(m.get("volume", 0)),
                    )
                    for m in data
                    if m.get("conditionId") and m.get("question")
                ]
    except Exception as e:
        logger.error(f"Polymarketマーケット取得失敗: {e}")
        return _mock_polymarket()


async def fetch_kalshi_markets(limit: int = 100) -> list[RawMarket]:
    """Kalshi APIからアクティブマーケットを取得"""
    if not AIOHTTP_AVAILABLE:
        return _mock_kalshi()

    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    # 政治・経済系カテゴリに絞る（スポーツを除外）
    POLITICAL_SERIES = ["KXFED", "KXPRES", "KXELECT", "KXBTC", "KXGDP",
                        "KXINFL", "KXUNEMP", "KXUSELECT", "KXRECESSION"]
    markets: list[RawMarket] = []
    try:
        async with aiohttp.ClientSession() as session:
            # シリーズごとに取得して結合
            for series in POLITICAL_SERIES:
                params = {"status": "open", "limit": 20, "series_ticker": series}
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    markets += [
                        RawMarket(
                            market_id=m.get("ticker", ""),
                            venue="kalshi",
                            title=m.get("title", ""),
                            end_date=m.get("close_time"),
                            volume=float(m.get("volume", 0)),
                        )
                        for m in data.get("markets", [])
                        if m.get("ticker") and m.get("title")
                    ]
            # フォールバック：絞り込みで0件なら全件取得
            if not markets:
                params = {"status": "open", "limit": limit}
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        markets = [
                            RawMarket(
                                market_id=m.get("ticker", ""),
                                venue="kalshi",
                                title=m.get("title", ""),
                                end_date=m.get("close_time"),
                                volume=float(m.get("volume", 0)),
                            )
                            for m in data.get("markets", [])
                            if m.get("ticker") and m.get("title")
                        ]
            return markets
    except Exception as e:
        logger.error(f"Kalshiマーケット取得失敗: {e}")
        return _mock_kalshi()


def match_markets(
    poly_markets: list[RawMarket],
    kalshi_markets: list[RawMarket],
    threshold: float = 0.25,
    min_volume: float = 1000.0,
) -> list[tuple[str, str, str]]:
    """
    類似度スコアで同一テーマのマーケットをマッチングする。

    Returns:
        (poly_id, kalshi_id, topic) のリスト
    """
    pairs = []
    # 流動性フィルター
    poly_filtered = [m for m in poly_markets if m.volume >= min_volume]
    kalshi_filtered = list(m for m in kalshi_markets if m.volume >= min_volume)

    # 1対1マッチング：各Kalshiマーケットは1回のみ使用
    used_kalshi: set[str] = set()

    # Polymarketを流動性順にソートして高流動性を優先
    for pm in sorted(poly_filtered, key=lambda m: m.volume, reverse=True):
        best_score = 0.0
        best_km = None
        for km in kalshi_filtered:
            if km.market_id in used_kalshi:
                continue
            score = jaccard_similarity(pm.title, km.title)
            if score > best_score:
                best_score = score
                best_km = km

        if best_score >= threshold and best_km:
            topic = pm.title[:60]
            pairs.append((pm.market_id, best_km.market_id, topic))
            used_kalshi.add(best_km.market_id)
            logger.info(
                f"マッチング: [{best_score:.2f}] "
                f"POLY: {pm.title[:40]} ↔ KALSHI: {best_km.title[:40]}"
            )

    logger.info(f"マーケットマッチング完了: {len(pairs)}ペア発見")
    return pairs


async def discover_market_pairs(
    poly_limit: int = 100,
    kalshi_limit: int = 100,
    similarity_threshold: float = 0.12,
    min_volume: float = 100.0,
) -> list[tuple[str, str, str]]:
    """マーケットを自動発見してペアリストを返す"""
    poly_markets, kalshi_markets = await asyncio.gather(
        fetch_polymarket_markets(poly_limit),
        fetch_kalshi_markets(kalshi_limit),
    )
    logger.info(f"取得: Polymarket {len(poly_markets)}件, Kalshi {len(kalshi_markets)}件")
    return match_markets(poly_markets, kalshi_markets, similarity_threshold, min_volume)


def _mock_polymarket() -> list[RawMarket]:
    return [
        RawMarket("poly-fed-rate-2024", "polymarket", "Will the Fed cut rates in 2024?", None, 50000),
        RawMarket("poly-btc-100k", "polymarket", "Will Bitcoin reach 100k by end of 2024?", None, 80000),
        RawMarket("poly-us-recession", "polymarket", "Will the US enter recession in 2024?", None, 30000),
    ]


def _mock_kalshi() -> list[RawMarket]:
    return [
        RawMarket("FED-RATE-DEC24", "kalshi", "Fed rate cut December 2024", None, 40000),
        RawMarket("BTC-100K-2024", "kalshi", "Bitcoin hits 100000 in 2024", None, 60000),
        RawMarket("US-RECESSION-24", "kalshi", "US recession 2024", None, 25000),
    ]
