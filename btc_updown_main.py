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
from src.btc_updown.bot import run_btc_updown

INITIAL_BALANCE_USD = float(os.getenv("INITIAL_BALANCE", "1000.0"))
TRADE_SIZE_USD = float(os.getenv("TRADE_SIZE", "10.0"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.04"))
MIN_CHANGE_PCT = float(os.getenv("MIN_CHANGE_PCT", "0.15"))


async def main():
    setup_logger(config.log_level)

    mode = "📝 PAPER TRADING" if config.paper_trading else "🔴 LIVE TRADING"
    logger.info("=" * 50)
    logger.info("  BTC 5分 Up/Down ボット")
    logger.info(f"  モード: {mode}")
    logger.info(f"  1回あたりサイズ: ${TRADE_SIZE_USD}")
    logger.info(f"  最小エッジ: {MIN_EDGE:.0%}")
    logger.info("=" * 50)

    risk_manager = RiskManager(config.risk, INITIAL_BALANCE_USD)
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
            trade_size_usd=TRADE_SIZE_USD,
            min_edge=MIN_EDGE,
            min_change_pct=MIN_CHANGE_PCT,
        )
    )

    await shutdown_event.wait()
    bot_task.cancel()

    summary = risk_manager.summary()
    logger.info(f"終了: 残高=${summary['balance']:.2f} PnL=${summary['total_pnl']:+.2f} 取引={summary['total_trades']}回")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
