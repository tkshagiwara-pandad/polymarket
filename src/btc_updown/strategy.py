"""
BTC 5分 Up/Down 売買戦略（WebSocket版）
- BinancePriceFeedからリアルタイム価格を取得
- REST APIポーリングなし → ミリ秒レベルで判断
"""
from dataclasses import dataclass
from loguru import logger

from src.btc_updown.price_feed import BinancePriceFeed


@dataclass
class Signal:
    direction: str       # "up" or "down"
    confidence: float    # 推定勝率 0〜1
    btc_now: float
    btc_ref: float       # 比較基準価格（300秒前）
    change_pct: float
    market_price: float  # 取引方向の現在市場価格
    edge: float          # 期待エッジ
    trade: bool          # True なら取引実行


def analyze(
    feed: BinancePriceFeed,
    up_price: float,
    down_price: float,
    lookback_sec: int = 300,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
) -> Signal:
    """
    WebSocketキャッシュから即座にシグナルを生成する（遅延ゼロ）。

    ロジック:
    1. lookback_sec前のBTC価格と現在を比較
    2. 上昇 → Up / 下落 → Down
    3. エッジ = 推定勝率 - 市場価格
    """
    btc_now = feed.current_price
    btc_ref = feed.price_n_seconds_ago(lookback_sec)

    if btc_ref is None or btc_ref == 0:
        # データ不足（起動直後など）
        logger.warning(f"BTC価格キャッシュ不足（{lookback_sec}秒分待機中）")
        return Signal(
            direction="none", confidence=0.5,
            btc_now=btc_now, btc_ref=0, change_pct=0,
            market_price=0.5, edge=0, trade=False,
        )

    change_pct = (btc_now - btc_ref) / btc_ref * 100
    abs_change = abs(change_pct)

    if change_pct >= 0:
        direction = "up"
        market_price = up_price
    else:
        direction = "down"
        market_price = down_price

    # モメンタム強度から勝率を推定（保守的に最大65%）
    p_win = min(0.50 + abs_change * 0.05, 0.65)
    edge = p_win - market_price
    trade = edge >= min_edge and abs_change >= min_change_pct

    logger.info(
        f"[シグナル] {direction.upper()} | "
        f"BTC {btc_ref:.0f}→{btc_now:.0f} ({change_pct:+.3f}%) | "
        f"市場={market_price:.3f} 勝率推定={p_win:.3f} edge={edge:+.3f} | "
        f"{'✅ 発注' if trade else '⏭ スキップ'}"
    )

    return Signal(
        direction=direction,
        confidence=p_win,
        btc_now=btc_now,
        btc_ref=btc_ref,
        change_pct=change_pct,
        market_price=market_price,
        edge=edge,
        trade=trade,
    )
