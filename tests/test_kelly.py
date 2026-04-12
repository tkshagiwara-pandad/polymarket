"""Kelly Criterion のユニットテスト"""
import pytest
from src.strategies.kelly import calculate_kelly, KellyResult


def test_viable_opportunity():
    """明確なエッジがある場合、viable=True でサイズが返る"""
    result = calculate_kelly(
        prob_win=0.60,
        price_buy=0.45,
        portfolio_usd=1000.0,
    )
    assert result.viable is True
    assert result.size_usd > 0
    assert result.edge > 0


def test_max_position_cap():
    """ポジションサイズが最大3%を超えない"""
    result = calculate_kelly(
        prob_win=0.99,
        price_buy=0.10,
        portfolio_usd=1000.0,
        max_position_pct=0.03,
    )
    assert result.size_usd <= 1000.0 * 0.03 + 0.01  # 浮動小数点誤差許容


def test_no_edge():
    """エッジがない場合、viable=False"""
    result = calculate_kelly(
        prob_win=0.50,
        price_buy=0.50,
        portfolio_usd=1000.0,
        min_edge_pct=0.02,
    )
    assert result.viable is False
    assert result.size_usd == 0.0


def test_fee_slippage_reduces_edge():
    """手数料・スリッページが大きいとエッジが減少する"""
    low_cost = calculate_kelly(prob_win=0.55, price_buy=0.45, portfolio_usd=1000.0,
                               fee_pct=0.001, slippage_pct=0.001)
    high_cost = calculate_kelly(prob_win=0.55, price_buy=0.45, portfolio_usd=1000.0,
                                fee_pct=0.01, slippage_pct=0.01)
    assert low_cost.edge > high_cost.edge


def test_price_over_one_not_viable():
    """価格がコスト込みで1を超える場合は不可"""
    result = calculate_kelly(
        prob_win=0.50,
        price_buy=0.98,
        portfolio_usd=1000.0,
        fee_pct=0.01,
        slippage_pct=0.01,
    )
    assert result.viable is False
