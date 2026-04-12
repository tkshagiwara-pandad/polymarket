"""
Polymarket CLOBクライアントラッパー
- 価格取得
- 注文発注（paper/live）
- エラーハンドリング
"""
import asyncio
from typing import Optional
from loguru import logger

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.constants import POLYGON
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    logger.warning("py-clob-client が未インストール。Polymarketは模擬モードで動作します")

from src.strategies.arbitrage import MarketPrice


class PolymarketConnector:
    def __init__(
        self,
        private_key: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        host: str = "https://clob.polymarket.com",
        paper: bool = True,
    ):
        self.paper = paper
        self._client: Optional[object] = None

        if CLOB_AVAILABLE and private_key and not paper:
            try:
                self._client = ClobClient(
                    host=host,
                    key=private_key,
                    chain_id=POLYGON,
                    creds=ApiCreds(
                        api_key=api_key,
                        api_secret=api_secret,
                        api_passphrase=api_passphrase,
                    ),
                )
                logger.info("Polymarket CLOBクライアント初期化完了（ライブモード）")
            except Exception as e:
                logger.error(f"Polymarket初期化失敗: {e}")
        else:
            mode = "ペーパートレード" if paper else "認証情報なし"
            logger.info(f"Polymarketコネクタ: {mode}モード")

    async def get_market_price(self, market_id: str, topic: str = "") -> Optional[MarketPrice]:
        """市場の最良気配値を取得する"""
        if self._client is None:
            # ペーパーモード: モックデータを返す
            return self._mock_price(market_id, topic)

        try:
            loop = asyncio.get_event_loop()
            book = await loop.run_in_executor(
                None, lambda: self._client.get_order_book(market_id)
            )
            yes_price = float(book.asks[0].price) if book.asks else 0.5
            no_price = 1.0 - float(book.bids[0].price) if book.bids else 0.5

            return MarketPrice(
                market_id=market_id,
                venue="polymarket",
                topic=topic,
                yes_price=yes_price,
                no_price=no_price,
                liquidity=float(book.asks[0].size) if book.asks else 0.0,
            )
        except Exception as e:
            logger.error(f"Polymarket価格取得失敗 {market_id}: {e}")
            return None

    async def place_order(
        self, market_id: str, side: str, price: float, size: float
    ) -> Optional[dict]:
        """注文を発注する（paperモードはログのみ）"""
        fee = size * 0.002  # 0.2%手数料
        if self.paper:
            logger.info(
                f"[PAPER] Polymarket注文: {side} {size:.2f}contracts @ {price:.3f} "
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
                lambda: self._client.create_and_post_order(
                    OrderArgs(
                        price=price,
                        size=size,
                        side=side,
                        token_id=market_id,
                        order_type=OrderType.GTC,
                    )
                )
            )
            logger.info(f"Polymarket注文完了: {order}")
            return order
        except Exception as e:
            logger.error(f"Polymarket注文失敗: {e}")
            return None

    def _mock_price(self, market_id: str, topic: str) -> MarketPrice:
        """ペーパートレード用モック価格"""
        import random
        yes = round(random.uniform(0.35, 0.65), 3)
        return MarketPrice(
            market_id=market_id,
            venue="polymarket",
            topic=topic,
            yes_price=yes,
            no_price=round(1.0 - yes + random.uniform(-0.05, 0.05), 3),
            liquidity=random.uniform(5000, 50000),
        )
