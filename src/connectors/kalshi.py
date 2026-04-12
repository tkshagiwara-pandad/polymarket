"""
Kalshiクライアントラッパー
- 価格取得
- 注文発注（paper/live）
"""
import asyncio
from typing import Optional
from loguru import logger

from src.strategies.arbitrage import MarketPrice


class KalshiConnector:
    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        host: str = "https://trading-api.kalshi.com/trade-api/v2",
        paper: bool = True,
    ):
        self.paper = paper
        self.host = host
        self._client: Optional[object] = None

        if api_key_id and private_key_path and not paper:
            try:
                self._init_client(api_key_id, private_key_path)
            except Exception as e:
                logger.error(f"Kalshi初期化失敗: {e}")
        else:
            mode = "ペーパートレード" if paper else "認証情報なし"
            logger.info(f"Kalshiコネクタ: {mode}モード")

    def _init_client(self, api_key_id: str, private_key_path: str) -> None:
        """Kalshi REST APIクライアントを初期化する"""
        try:
            import kalshi_python
            config = kalshi_python.Configuration(host=self.host)
            self._client = kalshi_python.ApiClient(configuration=config)
            logger.info("Kalshiクライアント初期化完了（ライブモード）")
        except ImportError:
            logger.warning("kalshi_python が未インストール。模擬モードで動作します")

    async def get_market_price(self, market_id: str, topic: str = "") -> Optional[MarketPrice]:
        """市場の最良気配値を取得する"""
        if self._client is None:
            return self._mock_price(market_id, topic)

        try:
            loop = asyncio.get_event_loop()
            market = await loop.run_in_executor(
                None, lambda: self._client.get_market(ticker=market_id)
            )
            yes_bid = market.yes_bid / 100.0 if market.yes_bid else 0.5
            yes_ask = market.yes_ask / 100.0 if market.yes_ask else 0.5
            yes_price = (yes_bid + yes_ask) / 2.0

            return MarketPrice(
                market_id=market_id,
                venue="kalshi",
                topic=topic,
                yes_price=yes_price,
                no_price=round(1.0 - yes_price, 3),
                liquidity=float(market.volume or 0),
            )
        except Exception as e:
            logger.error(f"Kalshi価格取得失敗 {market_id}: {e}")
            return None

    async def place_order(
        self, market_id: str, side: str, price: float, size: int
    ) -> Optional[dict]:
        """注文を発注する（paperモードはログのみ）"""
        fee = size * price * 0.002
        if self.paper:
            logger.info(
                f"[PAPER] Kalshi注文: {side} {size}contracts @ {price:.3f} "
                f"market={market_id} fee=${fee:.4f}"
            )
            return {"order_id": f"paper_{market_id}_{side}", "status": "paper"}

        if self._client is None:
            logger.error("ライブクライアント未初期化")
            return None

        try:
            loop = asyncio.get_event_loop()
            order = await loop.run_in_executor(
                None,
                lambda: self._client.create_order(
                    ticker=market_id,
                    side=side,
                    count=size,
                    type="limit",
                    yes_price=int(price * 100),
                )
            )
            logger.info(f"Kalshi注文完了: {order}")
            return order
        except Exception as e:
            logger.error(f"Kalshi注文失敗: {e}")
            return None

    def _mock_price(self, market_id: str, topic: str) -> MarketPrice:
        """ペーパートレード用モック価格"""
        import random
        yes = round(random.uniform(0.35, 0.65), 3)
        return MarketPrice(
            market_id=market_id,
            venue="kalshi",
            topic=topic,
            yes_price=yes,
            no_price=round(1.0 - yes + random.uniform(-0.05, 0.05), 3),
            liquidity=random.uniform(5000, 50000),
        )
