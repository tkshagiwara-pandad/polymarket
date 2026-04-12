"""
リスク管理・キルスイッチ
- ドローダウン監視
- 最大損失でボット停止
- ポジション集中リスク管理
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict
from loguru import logger

from src.config import RiskConfig


@dataclass
class RiskState:
    portfolio_usd: float
    peak_usd: float
    open_positions: Dict[str, float] = field(default_factory=dict)  # market_id -> exposure
    kill_switch_triggered: bool = False
    total_trades: int = 0
    total_pnl: float = 0.0
    session_start: datetime = field(default_factory=datetime.utcnow)


class RiskManager:
    def __init__(self, config: RiskConfig, initial_balance: float):
        self.config = config
        self.state = RiskState(
            portfolio_usd=initial_balance,
            peak_usd=initial_balance,
        )

    @property
    def current_drawdown(self) -> float:
        if self.state.peak_usd <= 0:
            return 0.0
        return (self.state.peak_usd - self.state.portfolio_usd) / self.state.peak_usd

    @property
    def total_exposure(self) -> float:
        return sum(self.state.open_positions.values())

    def check_kill_switch(self) -> bool:
        """ドローダウンがしきい値を超えたらキルスイッチを発動"""
        if self.current_drawdown >= self.config.max_drawdown_pct:
            if not self.state.kill_switch_triggered:
                self.state.kill_switch_triggered = True
                logger.critical(
                    f"🛑 キルスイッチ発動: ドローダウン {self.current_drawdown*100:.1f}% "
                    f"(上限 {self.config.max_drawdown_pct*100:.1f}%)"
                )
            return True
        return False

    def can_trade(self, size_usd: float, market_id: str) -> tuple[bool, str]:
        """取引可否を判定する"""
        if self.state.kill_switch_triggered:
            return False, "キルスイッチが発動中"

        if self.check_kill_switch():
            return False, f"ドローダウン超過: {self.current_drawdown*100:.1f}%"

        # ポジション集中チェック
        max_size = self.state.portfolio_usd * self.config.max_position_pct
        if size_usd > max_size:
            return False, f"ポジションサイズ超過: ${size_usd:.2f} > ${max_size:.2f}"

        # 総エクスポージャーチェック（最大20%）
        if (self.total_exposure + size_usd) > self.state.portfolio_usd * 0.20:
            return False, f"総エクスポージャー超過"

        return True, "OK"

    def register_trade(self, market_id: str, size_usd: float, fee: float) -> None:
        self.state.open_positions[market_id] = (
            self.state.open_positions.get(market_id, 0.0) + size_usd
        )
        self.state.portfolio_usd -= fee
        self.state.total_trades += 1
        logger.info(
            f"取引登録: {market_id} size=${size_usd:.2f} fee=${fee:.4f} "
            f"残高=${self.state.portfolio_usd:.2f} "
            f"DD={self.current_drawdown*100:.2f}%"
        )

    def settle_position(self, market_id: str, pnl: float) -> None:
        """ポジションを決済してPnLを反映する"""
        exposure = self.state.open_positions.pop(market_id, 0.0)
        self.state.portfolio_usd += exposure + pnl
        self.state.total_pnl += pnl
        self.state.peak_usd = max(self.state.peak_usd, self.state.portfolio_usd)
        logger.info(
            f"決済: {market_id} pnl=${pnl:+.2f} "
            f"累計PnL=${self.state.total_pnl:+.2f} "
            f"残高=${self.state.portfolio_usd:.2f}"
        )

    def summary(self) -> dict:
        return {
            "balance": self.state.portfolio_usd,
            "peak": self.state.peak_usd,
            "drawdown_pct": self.current_drawdown * 100,
            "total_trades": self.state.total_trades,
            "total_pnl": self.state.total_pnl,
            "open_positions": len(self.state.open_positions),
            "kill_switch": self.state.kill_switch_triggered,
        }
