"""
BTC 5分 Up/Down 売買戦略
- Binance公開APIでBTC価格を取得（認証不要）
- 直近5分のモメンタムを計算
- 市場価格と比較してエッジを判定
"""
from dataclasses import dataclass
from typing import Optional
from loguru import logger

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class Signal:
    direction: str      # "up" or "down"
    confidence: float   # 0〜1
    btc_now: float
    btc_5m_ago: float
    change_pct: float
    market_price: float  # 取引方向の現在市場価格
    edge: float          # 期待エッジ（正なら取引価値あり）
    trade: bool          # Trueなら取引実行


async def fetch_btc_prices() -> Optional[tuple[float, float]]:
    """
    Binance APIから現在価格と5分前の終値を取得する。
    Returns: (現在価格, 5分前価格) or None
    """
    if not AIOHTTP_AVAILABLE:
        return None

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": "BTCUSDT", "interval": "1m", "limit": 6}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                if r.status != 200:
                    logger.warning(f"Binance API エラー: {r.status}")
                    return None
                klines = await r.json()
                if len(klines) < 6:
                    return None
                # klines[i] = [open_time, open, high, low, close, ...]
                price_now = float(klines[-1][4])   # 最新足の終値
                price_5m_ago = float(klines[0][4]) # 5本前の終値
                return price_now, price_5m_ago
    except Exception as e:
        logger.error(f"BTC価格取得失敗: {e}")
        return None


def analyze(
    up_price: float,
    down_price: float,
    btc_now: float,
    btc_5m_ago: float,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
) -> Signal:
    """
    モメンタムと市場価格からシグナルを生成する。

    ロジック:
    - BTCが過去5分で上昇 → "up"シグナル
    - BTCが過去5分で下落 → "down"シグナル
    - シグナル方向の市場価格が低い（安い）ほどエッジが高い
    - min_edge未満またはmin_change_pct未満なら取引しない
    """
    change_pct = (btc_now - btc_5m_ago) / btc_5m_ago * 100
    abs_change = abs(change_pct)

    if change_pct >= 0:
        direction = "up"
        market_price = up_price
        # モメンタムが"up"なのに市場が"up"を安く評価していればエッジあり
        edge = (1.0 - market_price) - market_price  # 簡易: payoff - cost
        # より正確: 期待値 = 1.0 * p_win - market_price
        # p_win を モメンタム強度から推定（保守的に0.55〜0.65）
        p_win = min(0.50 + abs_change * 0.05, 0.65)
        edge = p_win - market_price
    else:
        direction = "down"
        market_price = down_price
        p_win = min(0.50 + abs_change * 0.05, 0.65)
        edge = p_win - market_price

    trade = edge >= min_edge and abs_change >= min_change_pct

    logger.info(
        f"シグナル: {direction.upper()} | BTC変化={change_pct:+.2f}% "
        f"({btc_5m_ago:.0f}→{btc_now:.0f}) | "
        f"市場価格={market_price:.3f} | edge={edge:+.3f} | "
        f"取引={'✓' if trade else '✗'}"
    )

    return Signal(
        direction=direction,
        confidence=p_win,
        btc_now=btc_now,
        btc_5m_ago=btc_5m_ago,
        change_pct=change_pct,
        market_price=market_price,
        edge=edge,
        trade=trade,
    )
