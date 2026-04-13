"""
Binance WebSocket リアルタイム価格フィード
- 常時接続でBTC価格をミリ秒単位でキャッシュ
- 直近300秒分の価格を保持
- REST APIポーリング不要
"""
import asyncio
import json
import time
from collections import deque
from typing import Optional
from loguru import logger

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

# Binance WebSocket URL（認証不要・無料）
WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"


class BinancePriceFeed:
    """
    Binance aggTradeストリームでBTC/USDTをリアルタイム取得。
    価格は(timestamp, price)のデック（1300秒分）に保持。
    出来高は(timestamp, qty_btc)のデックに保持（出来高フィルター用）。
    """

    def __init__(self):
        # maxlen なし: 時間ベース（1300秒分）でトリムするため件数上限不要
        self._history: deque[tuple[float, float]] = deque()
        # 出来高履歴: (timestamp_sec, qty_btc)
        self._vol_history: deque[tuple[float, float]] = deque()
        self._current: float = 0.0
        self._connected: bool = False
        self._reconnect_interval: float = 3.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def current_price(self) -> float:
        return self._current

    def price_n_seconds_ago(self, n: int) -> Optional[float]:
        """n秒前の価格を返す。データ不足の場合はNone"""
        if not self._history:
            logger.warning(f"price_n_seconds_ago({n}): deque空（WebSocket未受信?）")
            return None
        # ローカル時計ではなく Binance の最新タイムスタンプを基準にする
        latest_ts = self._history[-1][0]
        oldest_ts = self._history[0][0]
        span = latest_ts - oldest_ts
        target_ts = latest_ts - n
        best = None
        for ts, price in self._history:
            if ts <= target_ts:
                best = price
            else:
                break
        if best is None:
            logger.warning(
                f"[キャッシュ診断] n={n}s 件数={len(self._history)} "
                f"span={span:.0f}s oldest={oldest_ts:.0f} newest={latest_ts:.0f} target={target_ts:.0f}"
            )
        return best

    def change_pct(self, seconds: int = 300) -> Optional[float]:
        """過去seconds秒の価格変化率(%)を返す"""
        old = self.price_n_seconds_ago(seconds)
        if old is None or old == 0:
            return None
        return (self._current - old) / old * 100

    def volume_last_n_seconds(self, n: int) -> float:
        """直近n秒のBTC出来高合計を返す"""
        if not self._vol_history:
            return 0.0
        latest_ts = self._vol_history[-1][0]
        cutoff = latest_ts - n
        return sum(qty for ts, qty in self._vol_history if ts >= cutoff)

    def volume_ratio(self, short_sec: int = 300, long_sec: int = 1200) -> Optional[float]:
        """
        直近short_sec秒の出来高 ÷ 長期平均（同時間単位換算）。
        1.0 = 平均、1.5 = 平均の1.5倍。データ不足の場合はNone。
        """
        if len(self._vol_history) < 50:
            return None
        latest_ts = self._vol_history[-1][0]
        oldest_ts = self._vol_history[0][0]
        actual_span = latest_ts - oldest_ts
        # 長期データが long_sec の半分未満なら信頼性不足
        if actual_span < long_sec / 2:
            return None
        vol_short = self.volume_last_n_seconds(short_sec)
        vol_long = self.volume_last_n_seconds(long_sec)
        if vol_long == 0:
            return None
        # long_sec全体を基準にshort_secに正規化した1単位あたり平均
        baseline = vol_long * (short_sec / min(long_sec, actual_span))
        if baseline == 0:
            return None
        return vol_short / baseline

    async def start(self) -> None:
        """WebSocket接続を開始し、切断時は自動再接続する"""
        if not AIOHTTP_AVAILABLE:
            logger.error("aiohttp が未インストール")
            return

        while True:
            try:
                await self._connect()
            except Exception as e:
                logger.warning(f"WebSocket切断: {e} → {self._reconnect_interval}秒後に再接続")
                self._connected = False
                await asyncio.sleep(self._reconnect_interval)

    async def _connect(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                WS_URL,
                heartbeat=20,
                timeout=aiohttp.ClientWSTimeout(ws_close=5),
            ) as ws:
                self._connected = True
                logger.info("Binance WebSocket 接続完了")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self._handle(msg.data)
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break

    # 保持する最大秒数（15分トレンド900s + 20分出来高1200s + バッファ100s）
    _KEEP_SECONDS = 1300

    def _handle(self, raw: str) -> None:
        """aggTradeメッセージを処理してキャッシュを更新する"""
        try:
            data = json.loads(raw)
            # aggTrade: {"p": "価格", "q": "数量(BTC)", "T": タイムスタンプms, ...}
            price = float(data["p"])
            qty   = float(data["q"])
            ts    = data["T"] / 1000.0  # ミリ秒 → 秒
            self._history.append((ts, price))
            self._vol_history.append((ts, qty))
            self._current = price
            # 古いエントリを削除（件数ではなく時間で管理）
            cutoff = ts - self._KEEP_SECONDS
            while self._history and self._history[0][0] < cutoff:
                self._history.popleft()
            while self._vol_history and self._vol_history[0][0] < cutoff:
                self._vol_history.popleft()
        except Exception:
            pass
