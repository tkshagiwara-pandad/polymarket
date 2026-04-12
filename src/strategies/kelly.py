"""
Kelly Criterion ポジションサイジング
- 1/4 Kelly（保守的設定）
- 最大2-5%キャップ
- フィー・スリッページ控除後のエッジで計算
"""
from dataclasses import dataclass
from loguru import logger


@dataclass
class KellyResult:
    edge: float           # 純エッジ（フィー・スリッページ控除後）
    kelly_pct: float      # Kelly推奨比率
    capped_pct: float     # キャップ後の比率
    size_usd: float       # 推奨ポジションサイズ（USD）
    viable: bool          # 取引実行可否


def calculate_kelly(
    prob_win: float,          # 勝率（0〜1）
    price_buy: float,         # 購入価格（0〜1）
    portfolio_usd: float,     # ポートフォリオ総額
    fee_pct: float = 0.002,   # 手数料率
    slippage_pct: float = 0.005,  # スリッページ率
    kelly_fraction: float = 0.25, # Kelly係数（1/4 Kelly）
    max_position_pct: float = 0.03,  # 最大ポジション比率
    min_edge_pct: float = 0.02,      # 最小エッジ閾値
) -> KellyResult:
    """
    Kelly Criterionでポジションサイズを計算する。

    バイナリー市場の場合:
      - 勝ち: (1 - price_buy) / price_buy 倍のリターン
      - 負け: -1（全損）
      f* = (b*p - q) / b
        b = (1 - price) / price  (オッズ)
        p = prob_win
        q = 1 - p
    """
    # コスト調整後の実効価格
    effective_cost = price_buy + fee_pct + slippage_pct

    if effective_cost >= 1.0:
        return KellyResult(
            edge=0.0, kelly_pct=0.0, capped_pct=0.0,
            size_usd=0.0, viable=False
        )

    # バイナリーオッズ
    b = (1.0 - effective_cost) / effective_cost
    p = prob_win
    q = 1.0 - p

    # Kelly比率
    raw_kelly = (b * p - q) / b if b > 0 else 0.0

    # エッジ計算（期待値 - コスト）
    edge = p * (1.0 - effective_cost) - q * effective_cost

    if edge < min_edge_pct or raw_kelly <= 0:
        return KellyResult(
            edge=edge, kelly_pct=raw_kelly, capped_pct=0.0,
            size_usd=0.0, viable=False
        )

    # 1/4 Kelly（保守的）
    fractional_kelly = raw_kelly * kelly_fraction

    # 最大ポジション率でキャップ
    capped_pct = min(fractional_kelly, max_position_pct)

    size_usd = portfolio_usd * capped_pct

    logger.debug(
        f"Kelly計算: prob={p:.3f}, price={price_buy:.3f}, "
        f"edge={edge:.3f}, kelly={raw_kelly:.3f}, "
        f"capped={capped_pct:.3f}, size=${size_usd:.2f}"
    )

    return KellyResult(
        edge=edge,
        kelly_pct=raw_kelly,
        capped_pct=capped_pct,
        size_usd=size_usd,
        viable=True,
    )
