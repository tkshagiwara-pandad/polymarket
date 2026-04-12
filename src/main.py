"""
Polymarket / Kalshi アービトラージボット
メインエントリーポイント

使い方:
  python -m src.main                   # ペーパートレード（デフォルト）
  PAPER_TRADING=false python -m src.main  # ライブトレード（要認証情報）
"""
import asyncio
import signal
import sys
from loguru import logger

from src.config import config
from src.utils.logger import setup_logger
from src.utils.database import Database
from src.utils.notifier import Notifier
from src.risk.manager import RiskManager
from src.connectors.polymarket import PolymarketConnector
from src.connectors.kalshi import KalshiConnector
from src.connectors.market_discovery import discover_market_pairs
from src.workflow.graph import build_arb_graph


# ─── 静的マーケットペア（手動設定・フォールバック用）───────────────
# 動的発見が失敗した場合に使用
FALLBACK_MARKET_PAIRS = [
    # (polymarket_id, kalshi_ticker, topic)
    ("example-poly-market-id-1", "EXAMPLE-KALSHI-1", "2024年大統領選"),
    ("example-poly-market-id-2", "EXAMPLE-KALSHI-2", "Fed利上げ"),
]

# ─── 初期ポートフォリオ残高 ────────────────────────────────────────
INITIAL_BALANCE_USD = 1000.0

# マーケットペア再発見の間隔（サイクル数）
REDISCOVER_EVERY_N_CYCLES = 20


async def run_bot() -> None:
    """ボットのメインループ"""
    setup_logger(config.log_level)

    mode = "📝 PAPER TRADING" if config.paper_trading else "🔴 LIVE TRADING"
    logger.info(f"{'='*50}")
    logger.info(f"  Polymarket / Kalshi アービトラージボット起動")
    logger.info(f"  モード: {mode}")
    logger.info(f"  初期残高: ${INITIAL_BALANCE_USD:.2f}")
    logger.info(f"  最大DD: {config.risk.max_drawdown_pct*100:.0f}%")
    logger.info(f"  最大ポジション: {config.risk.max_position_pct*100:.0f}%")
    logger.info(f"{'='*50}")

    # コンポーネント初期化
    db = Database(config.db_path)
    notifier = Notifier(
        apprise_urls=config.notify.apprise_urls,
        enabled=config.notify.enabled,
    )
    risk_manager = RiskManager(config.risk, INITIAL_BALANCE_USD)
    poly = PolymarketConnector(
        private_key=config.polymarket.private_key,
        api_key=config.polymarket.api_key,
        api_secret=config.polymarket.api_secret,
        api_passphrase=config.polymarket.api_passphrase,
        paper=config.paper_trading,
    )
    kalshi = KalshiConnector(
        api_key_id=config.kalshi.api_key_id,
        private_key_path=config.kalshi.private_key_path,
        paper=config.paper_trading,
    )

    # 起動時にマーケットペアを動的発見
    logger.info("マーケットペアを自動発見中...")
    market_pairs = await discover_market_pairs()
    if not market_pairs:
        logger.warning("自動発見失敗 → フォールバックペアを使用")
        market_pairs = FALLBACK_MARKET_PAIRS
    logger.info(f"{len(market_pairs)}ペアで監視開始")

    # LangGraphワークフロー構築
    arb_graph = build_arb_graph(
        risk_manager=risk_manager,
        db=db,
        notifier=notifier,
        config=config,
        poly_connector=poly,
        kalshi_connector=kalshi,
        market_pairs=market_pairs,
    )

    # グレースフルシャットダウン
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("シャットダウン信号を受信しました")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(f"監視開始（{config.poll_interval_sec}秒間隔）")

    cycle = 0
    while not shutdown_event.is_set():
        cycle += 1
        try:
            logger.info(f"─── サイクル #{cycle} ───")

            # 定期的にマーケットペアを再発見
            if cycle % REDISCOVER_EVERY_N_CYCLES == 0:
                logger.info("マーケットペアを再発見中...")
                new_pairs = await discover_market_pairs()
                if new_pairs:
                    market_pairs = new_pairs
                    arb_graph = build_arb_graph(
                        risk_manager=risk_manager, db=db, notifier=notifier,
                        config=config, poly_connector=poly,
                        kalshi_connector=kalshi, market_pairs=market_pairs,
                    )
                    logger.info(f"ペア更新: {len(market_pairs)}件")

            initial_state = {
                "timestamp": "",
                "opportunities": [],
                "selected_opp": None,
                "risk_approved": False,
                "trade_result": None,
                "kill_switch": False,
                "error": None,
            }
            result = await arb_graph.ainvoke(initial_state)

            # キルスイッチ発動で終了
            if result.get("kill_switch"):
                logger.critical("キルスイッチによりボット停止")
                break

        except Exception as e:
            logger.exception(f"サイクルエラー: {e}")
            await notifier.alert_error(str(e))

        # 次のサイクルまで待機
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=config.poll_interval_sec
            )
        except asyncio.TimeoutError:
            pass

    # 終了サマリー
    summary = risk_manager.summary()
    logger.info(f"ボット終了サマリー: {summary}")
    await notifier.daily_summary(
        trades=summary["total_trades"],
        pnl=summary["total_pnl"],
        balance=summary["balance"],
    )


def main():
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("ボットを停止しました")
        sys.exit(0)


if __name__ == "__main__":
    main()
