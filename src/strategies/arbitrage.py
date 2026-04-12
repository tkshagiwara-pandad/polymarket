"""
アービトラージ戦略
- Polymarket / Kalshi 間の価格差検出
- フィー・スリッページ控除後の純エッジ計算
"""
from dataclasses import dataclass
from typing import Optional
from loguru import logger

from src.strategies.kelly import calculate_kelly, KellyResult


@dataclass
class MarketPrice:
    market_id: str
    venue: str          # "polymarket" or "kalshi"
    topic: str          # 同一テーマのラベル
    yes_price: float    # YES の最良気配値（0〜1）
    no_price: float     # NO  の最良気配値（0〜1）
    liquidity: float    # 流動性（USD）


@dataclass
class ArbOpportunity:
    topic: str
    poly: MarketPrice
    kalshi: MarketPrice
    direction: str      # "poly_yes_kalshi_no" or "poly_no_kalshi_yes"
    buy_price: float    # 安い方の価格
    sell_price: float   # 高い方の価格（= 1 - 相手のNO価格）
    edge: float
    kelly: KellyResult


def find_arbitrage(
    poly: MarketPrice,
    kalshi: MarketPrice,
    portfolio_usd: float,
    fee_pct: float = 0.002,
    slippage_pct: float = 0.005,
    min_edge_pct: float = 0.02,
    min_liquidity: float = 1000.0,
) -> Optional[ArbOpportunity]:
    """
    2市場間のアービトラージ機会を検出する。

    バイナリー市場の裁定原理:
      Polymarket YES + Kalshi NO の合計が1未満 → 裁定機会
      例: Poly YES = 0.45, Kalshi NO = 0.48 → 合計0.93 → 0.07のエッジ
    """
    if poly.liquidity < min_liquidity or kalshi.liquidity < min_liquidity:
        logger.debug(f"流動性不足: poly={poly.liquidity:.0f}, kalshi={kalshi.liquidity:.0f}")
        return None

    # パターン1: Polymarket YES + Kalshi NO を買う
    cost1 = poly.yes_price + kalshi.no_price
    edge1 = 1.0 - cost1 - 2 * (fee_pct + slippage_pct)

    # パターン2: Polymarket NO + Kalshi YES を買う
    cost2 = poly.no_price + kalshi.yes_price
    edge2 = 1.0 - cost2 - 2 * (fee_pct + slippage_pct)

    best_edge = max(edge1, edge2)
    if best_edge < min_edge_pct:
        return None

    if edge1 >= edge2:
        direction = "poly_yes_kalshi_no"
        buy_price = poly.yes_price
        # YES側でKellyを計算（勝率はKalshiNO価格を利用）
        prob_win = 1.0 - kalshi.no_price
    else:
        direction = "poly_no_kalshi_yes"
        buy_price = poly.no_price
        prob_win = 1.0 - kalshi.yes_price

    kelly = calculate_kelly(
        prob_win=prob_win,
        price_buy=buy_price,
        portfolio_usd=portfolio_usd,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
    )

    if not kelly.viable:
        return None

    logger.info(
        f"アービトラージ検出: {poly.topic} | "
        f"direction={direction} | edge={best_edge:.3f} | "
        f"size=${kelly.size_usd:.2f}"
    )

    return ArbOpportunity(
        topic=poly.topic,
        poly=poly,
        kalshi=kalshi,
        direction=direction,
        buy_price=buy_price,
        sell_price=1.0 - buy_price,
        edge=best_edge,
        kelly=kelly,
    )
