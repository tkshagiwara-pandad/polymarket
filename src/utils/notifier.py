"""
Telegram通知ユーティリティ
- アービトラージ機会のアラート
- エラー通知
- 日次サマリー
"""
import asyncio
from typing import Optional
from loguru import logger

try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class Notifier:
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.chat_id = chat_id
        self.enabled = enabled and TELEGRAM_AVAILABLE and bool(bot_token)
        self._bot: Optional[object] = None
        if self.enabled:
            self._bot = Bot(token=bot_token)

    async def send(self, message: str, parse_mode: str = "HTML") -> None:
        if not self.enabled:
            logger.debug(f"[Notifier disabled] {message}")
            return
        try:
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error(f"Telegram送信失敗: {e}")

    async def alert_opportunity(
        self, topic: str, edge: float, kelly_size: float,
        poly_price: float, kalshi_price: float, paper: bool
    ) -> None:
        mode = "📝 PAPER" if paper else "🔴 LIVE"
        msg = (
            f"{mode} アービトラージ機会検出\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{topic}</b>\n"
            f"📈 Polymarket: {poly_price:.3f}\n"
            f"📉 Kalshi:     {kalshi_price:.3f}\n"
            f"💰 エッジ:      {edge*100:.2f}%\n"
            f"📐 推奨サイズ:  ${kelly_size:.2f}\n"
        )
        await self.send(msg)

    async def alert_error(self, error: str) -> None:
        msg = f"⚠️ <b>エラー発生</b>\n<code>{error[:500]}</code>"
        await self.send(msg)

    async def alert_kill_switch(self, drawdown: float) -> None:
        msg = (
            f"🛑 <b>キルスイッチ発動</b>\n"
            f"ドローダウン {drawdown*100:.1f}% に達しました\n"
            f"取引を停止します。"
        )
        await self.send(msg)

    async def daily_summary(self, trades: int, pnl: float, balance: float) -> None:
        emoji = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{emoji} <b>日次サマリー</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"取引数:   {trades}\n"
            f"損益:     ${pnl:+.2f}\n"
            f"残高:     ${balance:.2f}\n"
        )
        await self.send(msg)
