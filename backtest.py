"""
BTC 5分 Up/Down バックテスト
- Binance 1分足データで過去7日間をシミュレーション
- 現在の戦略ロジックをそのまま適用
- 勝率・最適パラメータを出力
"""
import asyncio
import time
from datetime import datetime

try:
    import aiohttp
except ImportError:
    print("aiohttp が必要です: pip install aiohttp")
    exit(1)


# ===== Binanceデータ取得 =====

async def fetch_binance_klines(days: int = 7) -> list:
    """Binanceから1分足データを取得（認証不要・無料）"""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    all_klines = []
    current = start_ms

    print(f"Binanceから{days}日分データ取得中...")
    async with aiohttp.ClientSession() as session:
        while current < end_ms:
            async with session.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1m",
                        "startTime": current, "limit": 1000},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
                if not data:
                    break
                all_klines.extend(data)
                current = data[-1][0] + 60_000
                print(f"  {len(all_klines)}件取得済み...", end="\r")

    print(f"\n取得完了: {len(all_klines)}件")
    return all_klines


# ===== バックテスト本体 =====

def run_backtest(
    klines: list,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
    min_volatility: float = 0.05,
    assumed_market_price: float = 0.505,  # 実測値に近い仮定
) -> list:
    """
    各5分ウィンドウで戦略をシミュレーション。
    market_price は実際のPolymarketデータがないため固定値を使用。
    """
    # 1分足を辞書化（タイムスタンプ秒 → 終値）
    prices: dict[int, float] = {}
    for k in klines:
        ts = k[0] // 1000  # ms → sec
        prices[ts] = float(k[4])  # 終値

    def nearest_price(target_ts: int, tolerance: int = 120) -> float | None:
        """target_ts に最も近い価格を返す（±tolerance秒以内）"""
        for delta in range(0, tolerance, 60):
            for sign in (0, -1, 1):
                p = prices.get(target_ts + sign * delta)
                if p:
                    return p
        return None

    timestamps = sorted(prices.keys())
    if not timestamps:
        return []

    first_window = ((timestamps[0] // 300) + 1) * 300
    last_window  = (timestamps[-1] // 300) * 300

    trades = []

    for window_start in range(first_window, last_window, 300):
        window_end = window_start + 300

        price_now    = nearest_price(window_start)        # ウィンドウ開始時
        price_300ago = nearest_price(window_start - 300)  # 300秒前
        price_60ago  = nearest_price(window_start - 60)   # 60秒前
        price_result = nearest_price(window_end)           # 実際の終値

        if not all([price_now, price_300ago, price_60ago, price_result]):
            continue

        # ① ボラティリティフィルター
        change_long  = (price_now - price_300ago) / price_300ago * 100
        change_short = (price_now - price_60ago)  / price_60ago  * 100
        if abs(change_long) < min_volatility:
            continue

        # ② タイムフレーム一致確認
        dir_long  = "up" if change_long  >= 0 else "down"
        dir_short = "up" if change_short >= 0 else "down"
        if dir_long != dir_short:
            continue

        direction    = dir_long
        market_price = assumed_market_price
        abs_change   = abs(change_long)

        # ③ 勝率推定
        short_bonus = min(abs(change_short) * 0.01, 0.03)
        p_win = min(0.50 + abs_change * 0.05 + short_bonus, 0.68)
        edge  = p_win - market_price

        if edge < min_edge or abs_change < min_change_pct:
            continue

        # ④ 実際の結果と照合
        actual_dir = "up" if price_result > price_now else "down"
        won = (direction == actual_dir)

        trades.append({
            "ts":           window_start,
            "datetime":     datetime.fromtimestamp(window_start).strftime("%m/%d %H:%M"),
            "direction":    direction,
            "change_long":  change_long,
            "change_short": change_short,
            "p_win":        p_win,
            "edge":         edge,
            "won":          won,
            "price_start":  price_now,
            "price_end":    price_result,
        })

    return trades


# ===== 結果表示 =====

def print_results(trades: list, days: int, market_price: float) -> None:
    if not trades:
        print("条件を満たす取引なし（パラメータを緩めてみてください）")
        return

    total = len(trades)
    wins  = sum(1 for t in trades if t["won"])
    win_rate = wins / total

    # 期待損益（$1賭けた場合）
    pnl_win  =  (1.0 - market_price) - 0.002   # 勝ち: ペイアウト - 手数料
    pnl_loss = -(market_price)       - 0.002    # 負け: 賭け金没収 - 手数料
    expected_pnl = (wins * pnl_win + (total - wins) * pnl_loss) / total

    print(f"\n{'='*50}")
    print(f"  バックテスト結果（過去{days}日間）")
    print(f"{'='*50}")
    print(f"  取引回数  : {total}回")
    print(f"  勝ち      : {wins}回")
    print(f"  負け      : {total - wins}回")
    print(f"  勝率      : {win_rate:.1%}")
    print(f"  期待収益  : {expected_pnl:+.2%} / トレード")
    print(f"  仮定市場価格: {market_price}")
    print(f"{'='*50}")

    # エッジ別勝率
    print(f"\n  エッジ閾値別勝率:")
    for edge_min in [0.03, 0.05, 0.07, 0.10]:
        subset = [t for t in trades if t["edge"] >= edge_min]
        if subset:
            wr = sum(1 for t in subset if t["won"]) / len(subset)
            print(f"    edge >= {edge_min:.2f}: {len(subset):3d}回  勝率={wr:.1%}")

    # 変化率別勝率
    print(f"\n  変化率閾値別勝率:")
    for chg_min in [0.10, 0.20, 0.30, 0.50]:
        subset = [t for t in trades if abs(t["change_long"]) >= chg_min]
        if subset:
            wr = sum(1 for t in subset if t["won"]) / len(subset)
            print(f"    変化率 >= {chg_min:.2f}%: {len(subset):3d}回  勝率={wr:.1%}")

    # 時間帯別勝率（JST = UTC+9）
    print(f"\n  時間帯別勝率（JST）:")
    hour_stats: dict[int, dict] = {}
    for t in trades:
        h = (datetime.fromtimestamp(t["ts"]).hour + 9) % 24  # UTC→JST
        if h not in hour_stats:
            hour_stats[h] = {"total": 0, "wins": 0}
        hour_stats[h]["total"] += 1
        if t["won"]:
            hour_stats[h]["wins"] += 1
    for h in sorted(hour_stats.keys()):
        s = hour_stats[h]
        wr = s["wins"] / s["total"]
        bar = "█" * s["wins"] + "░" * (s["total"] - s["wins"])
        flag = " ◀ 高勝率" if wr >= 0.55 else (" ◀ 低勝率" if wr < 0.40 else "")
        print(f"    {h:02d}:00 JST  {s['total']:3d}回  勝率={wr:.0%}  {bar[:20]}{flag}")

    # 最近10件
    print(f"\n  直近10件:")
    for t in trades[-10:]:
        result = "✅勝" if t["won"] else "❌負"
        print(
            f"    {t['datetime']} {t['direction'].upper():4s} "
            f"変化={t['change_long']:+.2f}% edge={t['edge']:+.3f} → {result}"
        )


# ===== メイン =====

async def main():
    DAYS = 7
    MARKET_PRICE = 0.505  # Polymarket実測値に近い仮定

    klines = await fetch_binance_klines(days=DAYS)
    trades = run_backtest(
        klines,
        min_edge=0.03,          # 少し緩めて件数確保
        min_change_pct=0.10,
        assumed_market_price=MARKET_PRICE,
    )
    print_results(trades, DAYS, MARKET_PRICE)


if __name__ == "__main__":
    asyncio.run(main())
