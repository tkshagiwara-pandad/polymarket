"""
BTC 5分 Up/Down ボット エントリーポイント

使い方:
  python btc_updown_main.py                   # ペーパートレード
  PAPER_TRADING=false python btc_updown_main.py  # ライブ
  TRADE_SIZE=5 python btc_updown_main.py         # サイズ指定（デフォルト$10）
"""
import asyncio
import os
import signal
import sys
from loguru import logger

from src.config import config
from src.utils.logger import setup_logger
from src.risk.manager import RiskManager
from src.connectors.polymarket import PolymarketConnector
from src.utils.notifier import Notifier
from src.btc_updown.bot import run_btc_updown

INITIAL_BALANCE_USD = float(os.getenv("INITIAL_BALANCE", "20.0"))
TRADE_SIZE_USD      = float(os.getenv("TRADE_SIZE", "3.0"))
MIN_EDGE            = float(os.getenv("MIN_EDGE", "0.005"))
MIN_CHANGE_PCT      = float(os.getenv("MIN_CHANGE_PCT", "0.02"))
DAILY_LOSS_LIMIT    = float(os.getenv("DAILY_LOSS_LIMIT", "15.0"))
USE_KELLY           = os.getenv("USE_KELLY", "false").lower() == "true"
KELLY_FRACTION      = float(os.getenv("KELLY_FRACTION", "0.25"))
KELLY_MIN_SIZE      = float(os.getenv("KELLY_MIN_SIZE", "2.0"))
REVIEW_DATE         = os.getenv("REVIEW_DATE", "")  # 例: "2026-04-28"


async def main():
    setup_logger(config.log_level)

    mode = "📝 PAPER TRADING" if config.paper_trading else "🔴 LIVE TRADING"
    logger.info("=" * 50)
    logger.info("  BTC 5分 Up/Down ボット")
    logger.info(f"  モード: {mode}")
    logger.info(f"  1回あたりサイズ: ${TRADE_SIZE_USD}（最大）")
    logger.info(f"  最小エッジ: {MIN_EDGE:.0%}")
    logger.info(f"  Kelly: {'有効' if USE_KELLY else '無効'} | 日次損失上限: ${DAILY_LOSS_LIMIT}")
    logger.info("=" * 50)

    risk_manager = RiskManager(config.risk, INITIAL_BALANCE_USD)
    notifier = Notifier(
        apprise_urls=config.notify.apprise_urls,
        enabled=config.notify.enabled,
    )
    poly = PolymarketConnector(
        private_key=config.polymarket.private_key,
        api_key=config.polymarket.api_key,
        api_secret=config.polymarket.api_secret,
        api_passphrase=config.polymarket.api_passphrase,
        paper=config.paper_trading,
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    bot_task = asyncio.create_task(
        run_btc_updown(
            config=config,
            risk_manager=risk_manager,
            poly_connector=poly,
            notifier=notifier,
            trade_size_usd=TRADE_SIZE_USD,
            min_edge=MIN_EDGE,
            min_change_pct=MIN_CHANGE_PCT,
            initial_balance=INITIAL_BALANCE_USD,
            daily_loss_limit=DAILY_LOSS_LIMIT,
            use_kelly=USE_KELLY,
            kelly_fraction=KELLY_FRACTION,
            kelly_min_size=KELLY_MIN_SIZE,
            review_date=REVIEW_DATE or None,
        )
    )

    # ボットタスクが予期せず終了した場合にエラーを表示
    def _on_bot_done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error(f"ボットタスク異常終了: {task.exception()}", exc_info=task.exception())
            shutdown_event.set()

    bot_task.add_done_callback(_on_bot_done)

    await shutdown_event.wait()
    bot_task.cancel()

    summary = risk_manager.summary()
    logger.info(f"終了: 残高=${summary['balance']:.2f} PnL=${summary['total_pnl']:+.2f} 取引={summary['total_trades']}回")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
