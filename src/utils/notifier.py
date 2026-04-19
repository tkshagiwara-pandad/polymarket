"""
通知ユーティリティ（Apprise ベース）
- Telegram / Slack / Discord / Email など 100+ サービスに対応
- .env の APPRISE_URLS に通知先URLをカンマ区切りで列挙するだけで動作
  例: tgram://BOT_TOKEN/CHAT_ID,slack://TOKEN/CHANNEL
"""
import asyncio
from typing import Optional
from loguru import logger

import apprise


class Notifier:
    def __init__(self, apprise_urls: str = "", enabled: bool = True):
        """
        Args:
            apprise_urls: カンマ区切りの Apprise URL 文字列
                例: "tgram://token/chatid,slack://token/channel"
            enabled: 通知を有効にするか
        """
        self._ap = apprise.Apprise()
        self.enabled = enabled and bool(apprise_urls)

        if self.enabled:
            for url in apprise_urls.split(","):
                url = url.strip()
                if url:
                    if self._ap.add(url):
                        logger.info(f"通知先追加: {self._mask_url(url)}")
                    else:
                        logger.warning(f"通知URL無効: {self._mask_url(url)}")

        if not self.enabled:
            logger.info("通知無効（APPRISE_URLS 未設定）")

    @staticmethod
    def _mask_url(url: str) -> str:
        """URLのトークン部分をマスクする"""
        parts = url.split("//", 1)
        if len(parts) == 2:
            return f"{parts[0]}//*****"
        return "***"

    async def send(self, message: str, title: str = "ArbBot") -> None:
        """全通知先にメッセージを送信する"""
        if not self.enabled:
            logger.debug(f"[通知無効] {message[:80]}")
            return
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._ap.notify(body=message, title=title)
            )
            if not result:
                logger.warning(f"通知送信失敗（Apprise returned False）: {title}")
            else:
                logger.debug(f"通知送信OK: {title}")
        except Exception as e:
            logger.error(f"通知送信失敗: {e}")

    async def alert_opportunity(
        self, topic: str, edge: float, kelly_size: float,
        poly_price: float, kalshi_price: float, paper: bool
    ) -> None:
        mode = "[PAPER]" if paper else "[LIVE]"
        msg = (
            f"{mode} アービトラージ機会検出\n"
            f"テーマ: {topic}\n"
            f"Polymarket: {poly_price:.3f}\n"
            f"Kalshi:     {kalshi_price:.3f}\n"
            f"エッジ:      {edge*100:.2f}%\n"
            f"推奨サイズ:  ${kelly_size:.2f}"
        )
        await self.send(msg, title="Arb機会")

    async def alert_error(self, error: str) -> None:
        await self.send(f"エラー発生:\n{error[:500]}", title="ArbBot ERROR")

    async def alert_kill_switch(self, drawdown: float) -> None:
        await self.send(
            f"キルスイッチ発動\nドローダウン {drawdown*100:.1f}% に達しました。取引停止。",
            title="ArbBot STOP"
        )

    async def daily_summary(self, trades: int, pnl: float, balance: float) -> None:
        sign = "+" if pnl >= 0 else ""
        await self.send(
            f"日次サマリー\n"
            f"取引数: {trades}\n"
            f"損益:   {sign}${pnl:.2f}\n"
            f"残高:   ${balance:.2f}",
            title="ArbBot 日次レポート"
        )
