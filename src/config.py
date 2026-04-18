"""
設定管理モジュール
- paper_tradingをデフォルトに
- リスクパラメータを一元管理
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class RiskConfig:
    """リスク管理設定"""
    max_position_pct: float = 0.20        # 1トレードあたり最大20%（$20残高で$3取引、手数料考慮）
    kelly_fraction: float = 0.25          # Kelly係数（保守的に1/4 Kelly）
    max_drawdown_pct: float = 0.10        # 最大ドローダウン10%でキルスイッチ
    min_edge_pct: float = 0.02            # 最小期待エッジ2%
    max_spread_pct: float = 0.05          # 最大スプレッド許容5%
    slippage_buffer_pct: float = 0.005    # スリッページバッファ0.5%
    fee_pct: float = 0.002                # 手数料0.2%


@dataclass
class PolymarketConfig:
    """Polymarket設定"""
    private_key: str = field(default_factory=lambda: os.getenv("POLYGON_PRIVATE_KEY", ""))
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_PASSPHRASE", ""))
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137  # Polygon mainnet


@dataclass
class KalshiConfig:
    """Kalshi設定"""
    api_key_id: str = field(default_factory=lambda: os.getenv("KALSHI_API_KEY_ID", ""))
    private_key_path: str = field(default_factory=lambda: os.getenv("KALSHI_PRIVATE_KEY_PATH", ""))
    host: str = "https://trading-api.kalshi.com/trade-api/v2"
    demo_host: str = "https://demo-api.kalshi.co/trade-api/v2"


@dataclass
class NotifyConfig:
    """通知設定（Apprise）"""
    # カンマ区切りで複数サービスを指定可能
    # 例: tgram://TOKEN/CHAT_ID,slack://TOKEN/CHANNEL,discord://ID/TOKEN
    apprise_urls: str = field(default_factory=lambda: os.getenv("APPRISE_URLS", ""))
    enabled: bool = field(default_factory=lambda: bool(os.getenv("APPRISE_URLS")))


@dataclass
class AppConfig:
    """アプリケーション全体設定"""
    paper_trading: bool = field(
        default_factory=lambda: os.getenv("PAPER_TRADING", "true").lower() != "false"
    )
    poll_interval_sec: float = 30.0       # 価格取得間隔（秒）
    db_path: str = "data/trades.db"
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    risk: RiskConfig = field(default_factory=RiskConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    kalshi: KalshiConfig = field(default_factory=KalshiConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


# シングルトンインスタンス
config = AppConfig()
