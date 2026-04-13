"""
BTC 5分 Up/Down ボット メインループ（WebSocket版）

改善点（レイテンシー戦略）:
- BTC価格はWebSocketでリアルタイムキャッシュ済み → 取引時のAPI呼び出しゼロ
- ウィンドウ境界を精密に待機 → 開始0秒で即発注
- Polymarketの市場価格取得のみ必要（1回のHTTPリクエスト）
"""
import asyncio
import time
from loguru import logger

from src.btc_updown.finder import fetch_current_market, current_window_end
from src.btc_updown.strategy import analyze
from src.btc_updown.price_feed import BinancePriceFeed
from src.config import AppConfig
from src.risk.manager import RiskManager
from src.utils.notifier import Notifier


async def _wait_for_window_open() -> None:
    """次の5分ウィンドウの開始まで精密に待機する"""
    now = time.time()
    next_boundary = ((int(now) // 300) + 1) * 300
    wait = next_boundary - now
    if wait > 0:
        logger.info(f"次のウィンドウまで {wait:.1f}秒待機")
        await asyncio.sleep(wait)


async def run_btc_updown(
    config: AppConfig,
    risk_manager: RiskManager,
    poly_connector,
    notifier: Notifier,
    trade_size_usd: float = 10.0,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
) -> None:
    """BTC 5分 Up/Down ボットのメインループ（WebSocket版）"""

    logger.info("=== BTC 5分 Up/Down ボット起動（WebSocket版）===")
    logger.info(f"  サイズ: ${trade_size_usd} | エッジ閾値: {min_edge:.0%} | 変化率閾値: {min_change_pct:.2f}%")
    logger.info(f"  モード: {'📝 PAPER' if config.paper_trading else '🔴 LIVE'}")

    # BinanceのWebSocket接続をバックグラウンドで開始
    feed = BinancePriceFeed()
    feed_task = asyncio.create_task(feed.start())
    logger.info("Binance WebSocket 接続中...")

    # 初回データが届くまで少し待つ
    for _ in range(30):
        if feed.current_price > 0:
            break
        await asyncio.sleep(1)
    else:
        logger.warning("BTC価格未取得。接続を確認してください")

    logger.info(f"BTC現在価格: ${feed.current_price:,.0f}")

    last_traded_window = 0

    try:
        while True:
            # 次のウィンドウ境界まで精密待機
            await _wait_for_window_open()

            window_ts = current_window_end()

            # 同じウィンドウで二重取引を防止
            if window_ts == last_traded_window:
                await asyncio.sleep(1)
                continue

            t0 = time.time()  # 発注遅延計測開始

            # ① Polymarketの最新市場価格を取得（唯一のHTTPリクエスト）
            market = await fetch_current_market()
            if market is None:
                logger.warning("マーケット未検出。スキップ")
                last_traded_window = window_ts
                continue

            t_market = time.time()
            logger.info(
                f"マーケット取得: {market.title} "
                f"Up={market.up_price:.3f} Down={market.down_price:.3f} "
                f"({(t_market - t0)*1000:.0f}ms)"
            )

            # ② BTC価格はWebSocketキャッシュから即取得（遅延ゼロ）
            signal = analyze(
                feed=feed,
                up_price=market.up_price,
                down_price=market.down_price,
                min_edge=min_edge,
                min_change_pct=min_change_pct,
            )

            if not signal.trade:
                last_traded_window = window_ts
                continue

            # ③ リスクチェック
            approved, reason = risk_manager.can_trade(trade_size_usd, market.condition_id)
            if not approved:
                logger.warning(f"リスク不合格: {reason}")
                last_traded_window = window_ts
                continue

            # ④ 発注（できるだけ速く）
            if signal.direction == "up":
                token_id = market.up_token_id
                price = market.up_price
            else:
                token_id = market.down_token_id
                price = market.down_price

            result = await poly_connector.place_order(
                market_id=token_id,
                side="BUY",
                price=price,
                size=trade_size_usd,
            )

            t_order = time.time()
            total_ms = (t_order - t0) * 1000

            if result:
                fee = trade_size_usd * 0.002
                risk_manager.register_trade(market.condition_id, trade_size_usd, fee)
                logger.info(
                    f"✅ 発注完了: {signal.direction.upper()} ${trade_size_usd} @ {price:.3f} "
                    f"総遅延={total_ms:.0f}ms"
                )
                await notifier.send(
                    title="BTC約定",
                    message=(
                        f"{'📈 UP' if signal.direction == 'up' else '📉 DOWN'} "
                        f"${trade_size_usd} @ {price:.3f}\n"
                        f"BTC: ${signal.btc_ref:,.0f} → ${signal.btc_now:,.0f} "
                        f"({signal.change_pct:+.2f}%)\n"
                        f"edge={signal.edge:+.3f} 遅延={total_ms:.0f}ms\n"
                        f"{'📝 PAPER' if config.paper_trading else '🔴 LIVE'}"
                    ),
                )
            else:
                logger.error(f"❌ 発注失敗 ({total_ms:.0f}ms)")

            last_traded_window = window_ts

    finally:
        feed_task.cancel()
        logger.info("WebSocket切断")
