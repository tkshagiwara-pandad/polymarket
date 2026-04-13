"""
Polymarket BTC 5分 Up/Down マーケット自動検出
- 現在の5分ウィンドウのスラッグを計算
- Gamma APIからconditionIdとトークンIDを取得
"""
import time
import json
from dataclasses import dataclass
from typing import Optional
from loguru import logger

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class BtcMarket:
    condition_id: str
    slug: str
    title: str
    up_token_id: str    # "Up" に対応するCLOBトークンID
    down_token_id: str  # "Down" に対応するCLOBトークンID
    up_price: float     # 現在の"Up"価格（0〜1）
    down_price: float   # 現在の"Down"価格（0〜1）
    end_ts: int         # 終了Unixタイムスタンプ


def current_window_end() -> int:
    """現在の5分ウィンドウの終了タイムスタンプを返す"""
    now = int(time.time())
    return ((now // 300) + 1) * 300


def seconds_to_window_end() -> int:
    """次の5分ウィンドウ開始まで何秒か"""
    now = int(time.time())
    return 300 - (now % 300)


async def fetch_current_market() -> Optional[BtcMarket]:
    """現在アクティブなBTC 5分マーケットを取得する"""
    if not AIOHTTP_AVAILABLE:
        logger.error("aiohttp が未インストール")
        return None

    end_ts = current_window_end()
    slug = f"btc-updown-5m-{end_ts}"

    url = "https://gamma-api.polymarket.com/events"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    logger.warning(f"Gamma API エラー: {r.status} slug={slug}")
                    return None
                data = await r.json()
                if not data:
                    logger.warning(f"マーケット未発見: {slug}")
                    return None

                event = data[0]
                markets = event.get("markets", [])
                if not markets:
                    return None

                m = markets[0]
                condition_id = m.get("conditionId", "")
                title = m.get("question", "")
                prices = json.loads(m.get("outcomePrices", '["0.5","0.5"]'))
                token_ids = json.loads(m.get("clobTokenIds", '["",""]'))

                if len(token_ids) < 2 or len(prices) < 2:
                    logger.warning("トークンID/価格の取得失敗")
                    return None

                return BtcMarket(
                    condition_id=condition_id,
                    slug=slug,
                    title=title,
                    up_token_id=token_ids[0],
                    down_token_id=token_ids[1],
                    up_price=float(prices[0]),
                    down_price=float(prices[1]),
                    end_ts=end_ts,
                )
    except Exception as e:
        logger.error(f"マーケット取得失敗: {e}")
        return None
