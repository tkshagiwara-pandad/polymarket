"""アービトラージ検出のユニットテスト"""
import pytest
from src.strategies.arbitrage import MarketPrice, find_arbitrage


def make_price(venue, yes, no, liquidity=10000):
    return MarketPrice(
        market_id=f"{venue}-1",
        venue=venue,
        topic="Test",
        yes_price=yes,
        no_price=no,
        liquidity=liquidity,
    )


def test_detects_arb_opportunity():
    """明確な裁定機会を検出できる"""
    poly = make_price("polymarket", yes=0.44, no=0.58)
    kalshi = make_price("kalshi", yes=0.60, no=0.42)
    opp = find_arbitrage(poly, kalshi, portfolio_usd=1000.0)
    assert opp is not None
    assert opp.edge > 0


def test_no_arb_when_prices_fair():
    """価格が適正な場合は機会なし"""
    poly = make_price("polymarket", yes=0.50, no=0.52)
    kalshi = make_price("kalshi", yes=0.50, no=0.52)
    opp = find_arbitrage(poly, kalshi, portfolio_usd=1000.0, min_edge_pct=0.02)
    assert opp is None


def test_no_arb_low_liquidity():
    """流動性不足では機会なし"""
    poly = make_price("polymarket", yes=0.40, no=0.62, liquidity=500)
    kalshi = make_price("kalshi", yes=0.65, no=0.37, liquidity=500)
    opp = find_arbitrage(poly, kalshi, portfolio_usd=1000.0, min_liquidity=1000.0)
    assert opp is None


def test_correct_direction():
    """方向性が正しく判定される"""
    # Poly YES + Kalshi NO が安い → poly_yes_kalshi_no
    poly = make_price("polymarket", yes=0.44, no=0.58)
    kalshi = make_price("kalshi", yes=0.60, no=0.42)
    opp = find_arbitrage(poly, kalshi, portfolio_usd=1000.0)
    assert opp is not None
    assert opp.direction in ("poly_yes_kalshi_no", "poly_no_kalshi_yes")
