"""
Kalshiクライアントラッパー
- RSA署名によるREST API認証
- 価格取得
- 注文発注（paper/live）
"""
import asyncio
import base64
import time
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

from src.strategies.arbitrage import MarketPrice


class KalshiConnector:
    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        paper: bool = True,
    ):
        self.paper = paper
        self.api_key_id = api_key_id
        self._private_key = None

        if api_key_id and private_key_path and not paper:
            self._load_private_key(private_key_path)
        else:
            mode = "ペーパートレード" if paper else "認証情報なし"
            logger.info(f"Kalshiコネクタ: {mode}モード")

    def _load_private_key(self, path: str) -> None:
        if not CRYPTO_AVAILABLE:
            logger.error("cryptography パッケージが未インストール: pip install cryptography")
            return
        try:
            pem = Path(path).read_bytes()
            self._private_key = serialization.load_pem_private_key(pem, password=None)
            logger.info("Kalshi RSA秘密鍵ロード完了（ライブモード）")
        except Exception as e:
            logger.error(f"Kalshi秘密鍵ロード失敗: {e}")

    def _sign(self, method: str, path: str) -> dict:
        """Kalshi API リクエスト署名ヘッダーを生成する"""
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}/trade-api/v2{path}".encode()
        sig = self._private_key.sign(msg, padding.PKCS1v15(), hashes.SHA256())
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "Content-Type": "application/json",
        }

    async def get_market_price(self, market_id: str, topic: str = "") -> Optional[MarketPrice]:
        """市場の最良気配値を取得する"""
        if self._private_key is None:
            return self._mock_price(market_id, topic)

        if not AIOHTTP_AVAILABLE:
            logger.error("aiohttp が未インストール")
            return self._mock_price(market_id, topic)

        path = f"/markets/{market_id}"
        headers = self._sign("GET", path)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status != 200:
                        logger.error(f"Kalshi API エラー {r.status}: {market_id}")
                        return None
                    data = await r.json()
                    m = data.get("market", {})
                    yes_bid = m.get("yes_bid", 50) / 100.0
                    yes_ask = m.get("yes_ask", 50) / 100.0
                    yes_price = (yes_bid + yes_ask) / 2.0
                    return MarketPrice(
                        market_id=market_id,
                        venue="kalshi",
                        topic=topic,
                        yes_price=yes_price,
                        no_price=round(1.0 - yes_price, 3),
                        liquidity=float(m.get("volume", 0)),
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

        if self._private_key is None:
            logger.error("ライブクライアント未初期化（秘密鍵が読み込めていません）")
            return None

        path = "/portfolio/orders"
        headers = self._sign("POST", path)
        payload = {
            "ticker": market_id,
            "action": "buy",
            "side": side,
            "count": size,
            "type": "limit",
            "yes_price": int(price * 100),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.BASE_URL}{path}",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
                    if r.status not in (200, 201):
                        logger.error(f"Kalshi注文失敗 {r.status}: {data}")
                        return None
                    logger.info(f"Kalshi注文完了: {data}")
                    return data
        except Exception as e:
            logger.error(f"Kalshi注文エラー: {e}")
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
