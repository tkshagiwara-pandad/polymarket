"""
BTC 5分 Up/Down 売買戦略（WebSocket版）
- BinancePriceFeedからリアルタイム価格を取得
- REST APIポーリングなし → ミリ秒レベルで判断

予測精度向上のための改善:
1. 複数タイムフレーム確認: 60秒と300秒が同方向のみ発注
2. ボラティリティフィルター: 動きが小さすぎる場合はスキップ
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from loguru import logger

from src.btc_updown.price_feed import BinancePriceFeed

# JST = UTC+9
_JST = timezone(timedelta(hours=9))

# 短期・長期の2タイムフレーム
SHORT_LOOKBACK = 60   # 秒
LONG_LOOKBACK  = 300  # 秒

# ボラティリティフィルター: 長期変化率がこれ未満はスキップ
MIN_VOLATILITY_PCT = 0.05

# 市場価格フィルター: 0.5からの乖離がこれ未満はスキップ（競合ボットが多い）
# 例: 0.03なら 0.47〜0.53 の範囲はスキップ
MIN_MARKET_IMBALANCE = 0.03

# 取引許可時間帯（JST）: バックテストで勝率が低い深夜帯（2〜6時）を除外
# None にすると時間帯フィルターを無効化
ACTIVE_HOURS_JST: set[int] | None = {
    7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1
}
# 除外: 2, 3, 4, 5, 6 JST（US深夜〜早朝、流動性低い）

# トレンドフィルター: 15分方向がシグナルと逆の場合はスキップ
TREND_LOOKBACK = 900        # 秒（15分）
TREND_MIN_CHANGE = 0.08     # 15分変化率がこれ未満の場合はトレンド判定スキップ（フラット）

# 出来高フィルター: 直近5分の出来高が長期平均のX倍以上必要
MIN_VOLUME_RATIO = 1.2      # 1.2 = 平均の120%以上

# モメンタム加速フィルター: 60秒変化率が「300秒÷5」の1分平均に対してこの比率以上必要
# 1.0 = 完全加速（60sが300s平均を超える）、0.8 = 80%以上で通過（やや緩め）
MIN_ACCELERATION_RATIO = 0.8


@dataclass
class Signal:
    direction: str        # "up" or "down"
    confidence: float     # 推定勝率 0〜1
    btc_now: float
    btc_ref: float        # 比較基準価格（300秒前）
    change_pct: float     # 300秒の変化率
    change_pct_short: float  # 60秒の変化率
    market_price: float   # 取引方向の現在市場価格
    edge: float           # 期待エッジ
    trade: bool           # True なら取引実行
    skip_reason: str      # スキップ理由（trade=Falseの場合）


def analyze(
    feed: BinancePriceFeed,
    up_price: float,
    down_price: float,
    lookback_sec: int = LONG_LOOKBACK,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
) -> Signal:
    """
    複数タイムフレームを確認してシグナルを生成する。

    ロジック:
    1. 300秒・60秒それぞれの方向を確認
    2. 両方が同方向 → 発注候補
    3. 逆方向またはどちらかがフラット → スキップ（不確実）
    4. ボラティリティフィルター: 300秒変化率 < 0.05% → スキップ
    5. エッジ = 推定勝率 - 市場価格
    """
    # ⓪ 時間帯フィルター
    if ACTIVE_HOURS_JST is not None:
        jst_hour = datetime.now(_JST).hour
        if jst_hour not in ACTIVE_HOURS_JST:
            logger.info(f"[スキップ] 非取引時間帯 | JST {jst_hour:02d}:xx（対象外）")
            return _no_trade(feed.current_price, f"非取引時間帯(JST {jst_hour:02d}時)")

    btc_now = feed.current_price

    # 長期（300秒）データ取得
    btc_ref_long = feed.price_n_seconds_ago(LONG_LOOKBACK)
    if btc_ref_long is None or btc_ref_long == 0:
        logger.warning(f"BTC価格キャッシュ不足（{LONG_LOOKBACK}秒分待機中）")
        return _no_trade(btc_now, "キャッシュ不足")

    # 短期（60秒）データ取得
    btc_ref_short = feed.price_n_seconds_ago(SHORT_LOOKBACK)
    if btc_ref_short is None or btc_ref_short == 0:
        logger.warning(f"BTC価格キャッシュ不足（{SHORT_LOOKBACK}秒分待機中）")
        return _no_trade(btc_now, "キャッシュ不足(短期)")

    change_long  = (btc_now - btc_ref_long)  / btc_ref_long  * 100
    change_short = (btc_now - btc_ref_short) / btc_ref_short * 100

    # ① ボラティリティフィルター: 動きが小さすぎる場合はスキップ
    if abs(change_long) < MIN_VOLATILITY_PCT:
        logger.info(
            f"[スキップ] ボラティリティ不足 | "
            f"300s={change_long:+.3f}% < {MIN_VOLATILITY_PCT}%"
        )
        return _no_trade(btc_now, "ボラティリティ不足", btc_ref_long, change_long, change_short)

    # ② 方向判定
    dir_long  = "up" if change_long  >= 0 else "down"
    dir_short = "up" if change_short >= 0 else "down"

    # ③ 複数タイムフレーム確認: 方向が一致しない場合はスキップ
    if dir_long != dir_short:
        logger.info(
            f"[スキップ] タイムフレーム不一致 | "
            f"300s={change_long:+.3f}%({dir_long}) vs 60s={change_short:+.3f}%({dir_short})"
        )
        return _no_trade(btc_now, "タイムフレーム不一致", btc_ref_long, change_long, change_short)

    direction = dir_long
    market_price = up_price if direction == "up" else down_price
    abs_change = abs(change_long)

    # ④ モメンタム加速フィルター
    # 60秒の変化率が「300秒÷5」の1分あたり平均を下回る = 勢いが鈍化中 → スキップ
    per_min_avg = abs(change_long) / 5
    if abs(change_short) < per_min_avg * MIN_ACCELERATION_RATIO:
        logger.info(
            f"[スキップ] モメンタム加速なし | "
            f"60s={abs(change_short):.3f}% < 300s平均/min={per_min_avg:.3f}%×{MIN_ACCELERATION_RATIO} "
            f"(ratio={abs(change_short)/per_min_avg:.2f})"
        )
        return _no_trade(btc_now, "モメンタム加速なし", btc_ref_long, change_long, change_short)

    # ⑤ 市場価格フィルター: 0.5に近すぎる（競合激しい）場合はスキップ
    imbalance = abs(market_price - 0.5)
    if imbalance < MIN_MARKET_IMBALANCE:
        logger.info(
            f"[スキップ] 市場が均衡 | "
            f"市場={market_price:.3f} 0.5からの乖離={imbalance:.3f} < {MIN_MARKET_IMBALANCE}"
        )
        return _no_trade(btc_now, "市場均衡", btc_ref_long, change_long, change_short)

    # ⑥ トレンドフィルター: 15分方向がシグナルと逆ならスキップ
    btc_ref_trend = feed.price_n_seconds_ago(TREND_LOOKBACK)
    if btc_ref_trend is not None and btc_ref_trend > 0:
        trend_change = (btc_now - btc_ref_trend) / btc_ref_trend * 100
        if abs(trend_change) >= TREND_MIN_CHANGE:
            trend_dir = "up" if trend_change >= 0 else "down"
            if trend_dir != direction:
                logger.info(
                    f"[スキップ] トレンド逆方向 | "
                    f"15min: {trend_change:+.3f}%({trend_dir}) vs シグナル: {direction}"
                )
                return _no_trade(btc_now, "トレンド逆方向", btc_ref_long, change_long, change_short)
            else:
                logger.debug(f"[トレンド一致] 15min: {trend_change:+.3f}%({trend_dir})")
    else:
        logger.debug("[トレンド] データ不足（起動後15分未満）→ スキップせず続行")

    # ⑦ 出来高フィルター: 直近5分出来高が長期平均以上か確認
    vol_ratio = feed.volume_ratio(short_sec=300, long_sec=1200)
    if vol_ratio is not None and vol_ratio < MIN_VOLUME_RATIO:
        logger.info(
            f"[スキップ] 出来高不足 | "
            f"ratio={vol_ratio:.2f} < {MIN_VOLUME_RATIO}（動きに確信なし）"
        )
        return _no_trade(btc_now, "出来高不足", btc_ref_long, change_long, change_short)
    elif vol_ratio is not None:
        logger.debug(f"[出来高OK] ratio={vol_ratio:.2f}")

    # ⑧ 勝率推定・発注判断: 両タイムフレーム一致時はボーナス（最大68%）
    # 短期モメンタムも同方向 → より確実性が高い
    short_bonus = min(abs(change_short) * 0.01, 0.03)  # 最大+3%ボーナス
    p_win = min(0.50 + abs_change * 0.05 + short_bonus, 0.68)
    edge = p_win - market_price
    trade = edge >= min_edge and abs_change >= min_change_pct

    skip_reason = "" if trade else f"edge={edge:+.3f} or 変化率不足"

    logger.info(
        f"[シグナル] {direction.upper()} | "
        f"300s: {btc_ref_long:.0f}→{btc_now:.0f} ({change_long:+.3f}%) | "
        f"60s: ({change_short:+.3f}%) | "
        f"市場={market_price:.3f} 勝率={p_win:.3f} edge={edge:+.3f} | "
        f"{'✅ 発注' if trade else f'⏭ スキップ ({skip_reason})'}"
    )

    return Signal(
        direction=direction,
        confidence=p_win,
        btc_now=btc_now,
        btc_ref=btc_ref_long,
        change_pct=change_long,
        change_pct_short=change_short,
        market_price=market_price,
        edge=edge,
        trade=trade,
        skip_reason=skip_reason,
    )


def _no_trade(
    btc_now: float,
    reason: str,
    btc_ref: float = 0,
    change_pct: float = 0,
    change_pct_short: float = 0,
) -> Signal:
    return Signal(
        direction="none", confidence=0.5,
        btc_now=btc_now, btc_ref=btc_ref,
        change_pct=change_pct, change_pct_short=change_pct_short,
        market_price=0.5, edge=0, trade=False,
        skip_reason=reason,
    )
