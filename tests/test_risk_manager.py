"""リスクマネージャーのユニットテスト"""
import pytest
from src.risk.manager import RiskManager
from src.config import RiskConfig


@pytest.fixture
def rm():
    cfg = RiskConfig(
        max_position_pct=0.03,
        max_drawdown_pct=0.10,
    )
    return RiskManager(cfg, initial_balance=1000.0)


def test_can_trade_normal(rm):
    """通常時は取引可能"""
    ok, reason = rm.can_trade(25.0, "market-1")
    assert ok is True


def test_position_size_limit(rm):
    """最大ポジションサイズ超過は拒否"""
    ok, reason = rm.can_trade(50.0, "market-1")  # 5% > 3%上限
    assert ok is False
    assert "超過" in reason


def test_kill_switch_triggers_on_drawdown(rm):
    """ドローダウン10%超でキルスイッチ発動"""
    rm.state.portfolio_usd = 890.0   # 11%ドローダウン
    assert rm.check_kill_switch() is True
    assert rm.state.kill_switch_triggered is True


def test_kill_switch_blocks_trade(rm):
    """キルスイッチ発動後は取引不可"""
    rm.state.kill_switch_triggered = True
    ok, reason = rm.can_trade(10.0, "market-1")
    assert ok is False


def test_register_trade_updates_balance(rm):
    """取引登録でフィー分残高が減少する"""
    initial = rm.state.portfolio_usd
    rm.register_trade("market-1", 30.0, fee=0.06)
    assert rm.state.portfolio_usd == pytest.approx(initial - 0.06)


def test_settle_updates_pnl(rm):
    """決済でPnLが反映される"""
    rm.register_trade("market-1", 30.0, fee=0.0)
    rm.settle_position("market-1", pnl=5.0)
    assert rm.state.total_pnl == pytest.approx(5.0)


def test_drawdown_calculation(rm):
    """ドローダウンが正しく計算される"""
    rm.state.portfolio_usd = 900.0
    rm.state.peak_usd = 1000.0
    assert rm.current_drawdown == pytest.approx(0.10)
