"""
BTC 5分 Up/Down ボット メインループ
- 5分ごとに新しいマーケットを検出
- Binanceモメンタムでシグナル生成
- Polymarketに注文発注
"""
import asyncio
import time
from loguru import logger

from src.btc_updown.finder import fetch_current_market, seconds_to_window_end
from src.btc_updown.strategy import fetch_btc_prices, analyze
from src.config import AppConfig
from src.risk.manager import RiskManager


async def run_btc_updown(
    config: AppConfig,
    risk_manager: RiskManager,
    poly_connector,
    trade_size_usd: float = 10.0,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
) -> None:
    """BTC 5分 Up/Down ボットのメインループ"""

    logger.info("=== BTC 5分 Up/Down ボット起動 ===")
    logger.info(f"  サイズ: ${trade_size_usd} | 最小エッジ: {min_edge:.0%} | 最小変化: {min_change_pct:.2f}%")
    logger.info(f"  モード: {'📝 PAPER' if config.paper_trading else '🔴 LIVE'}")

    last_traded_slug = ""

    while True:
        try:
            secs = seconds_to_window_end()
            logger.info(f"次の5分ウィンドウまで {secs}秒")

            # ウィンドウ開始直後（残り270〜300秒）に分析・発注
            if secs >= 270:
                # 新しいウィンドウの開始 → マーケット取得
                market = await fetch_current_market()
                if market is None:
                    logger.warning("マーケット未検出。30秒後に再試行")
                    await asyncio.sleep(30)
                    continue

                if market.slug == last_traded_slug:
                    logger.info(f"このウィンドウは取引済み: {market.slug}")
                    await asyncio.sleep(secs - 260)
                    continue

                logger.info(f"マーケット: {market.title}")
                logger.info(f"  Up: {market.up_price:.3f} | Down: {market.down_price:.3f}")

                # BTC価格取得
                prices = await fetch_btc_prices()
                if prices is None:
                    logger.warning("BTC価格取得失敗。スキップ")
                    await asyncio.sleep(30)
                    continue

                btc_now, btc_5m_ago = prices

                # シグナル分析
                signal = analyze(
                    up_price=market.up_price,
                    down_price=market.down_price,
                    btc_now=btc_now,
                    btc_5m_ago=btc_5m_ago,
                    min_edge=min_edge,
                    min_change_pct=min_change_pct,
                )

                if not signal.trade:
                    logger.info("エッジ不足 → スキップ")
                    last_traded_slug = market.slug
                    await asyncio.sleep(secs - 260)
                    continue

                # リスクチェック
                approved, reason = risk_manager.can_trade(trade_size_usd, market.condition_id)
                if not approved:
                    logger.warning(f"リスクチェック不合格: {reason}")
                    last_traded_slug = market.slug
                    await asyncio.sleep(secs - 260)
                    continue

                # 発注
                if signal.direction == "up":
                    token_id = market.up_token_id
                    side = "BUY"
                    price = market.up_price
                else:
                    token_id = market.down_token_id
                    side = "BUY"
                    price = market.down_price

                logger.info(
                    f"発注: {signal.direction.upper()} @ {price:.3f} "
                    f"size=${trade_size_usd} edge={signal.edge:+.3f}"
                )

                result = await poly_connector.place_order(
                    market_id=token_id,
                    side=side,
                    price=price,
                    size=trade_size_usd,
                )

                if result:
                    fee = trade_size_usd * 0.002
                    risk_manager.register_trade(market.condition_id, trade_size_usd, fee)
                    logger.info(f"発注完了: {result}")
                else:
                    logger.error("発注失敗")

                last_traded_slug = market.slug

                # 次のウィンドウまで待機
                await asyncio.sleep(max(secs - 260, 10))

            else:
                # ウィンドウ中盤〜終盤は待機
                await asyncio.sleep(min(secs, 30))

        except Exception as e:
            logger.exception(f"ループエラー: {e}")
            await asyncio.sleep(30)
