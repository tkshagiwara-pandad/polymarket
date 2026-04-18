"""
BTC 5分 Up/Down ボット メインループ（WebSocket版）

改善点（レイテンシー戦略）:
- BTC価格はWebSocketでリアルタイムキャッシュ済み → 取引時のAPI呼び出しゼロ
- ウィンドウ境界を精密に待機 → 開始0秒で即発注
- Polymarketの市場価格取得のみ必要（1回のHTTPリクエスト）
"""
import asyncio
import time
from datetime import datetime
from loguru import logger

from src.btc_updown.finder import fetch_current_market, current_window_end
from src.btc_updown.strategy import analyze
from src.btc_updown.price_feed import BinancePriceFeed
from src.config import AppConfig
from src.risk.manager import RiskManager
from src.utils.notifier import Notifier
from src.utils.trade_db import TradeDB, TradeRecord


def _kelly_size(
    p_win: float,
    market_price: float,
    balance: float,
    kelly_fraction: float,
    min_size: float,
    max_size: float,
) -> float:
    """
    Fractional Kelly 基準でポジションサイズを計算する。

    Polymarketのペイアウト: 勝ち時 (1/price - 1) * 0.98 の純利益（2%手数料考慮）
    Kelly公式: f* = (p*b - (1-p)) / b
    """
    b = (1.0 / market_price - 1.0) * 0.98
    if b <= 0:
        return min_size
    kelly_f = max(0.0, (p_win * b - (1.0 - p_win)) / b)
    size = balance * kelly_f * kelly_fraction
    return round(max(min_size, min(size, max_size)), 2)


async def _wait_for_window_open() -> None:
    """次の5分ウィンドウの開始まで精密に待機する"""
    now = time.time()
    next_boundary = ((int(now) // 300) + 1) * 300
    wait = next_boundary - now
    if wait > 0:
        logger.info(f"次のウィンドウまで {wait:.1f}秒待機")
        # +0.2秒: asyncio.sleep が境界直前に起きるのを防ぐ
        await asyncio.sleep(wait + 0.2)


async def _check_trade_result(
    db: TradeDB,
    trade_id: int,
    direction: str,
    btc_at_entry: float,
    feed: BinancePriceFeed,
    notifier: Notifier,
    trade_size_usd: float,
    price: float,
    risk_manager,
    market_id: str,
) -> None:
    """5分後に勝敗を判定してDBを更新し、Telegramに通知する"""
    await asyncio.sleep(330)  # 5分30秒後に確認（解決後の余裕）
    btc_now = feed.current_price
    if btc_now == 0:
        logger.warning(f"結果確認: BTC価格未取得 id={trade_id}")
        return

    actual_dir = "up" if btc_now > btc_at_entry else "down"
    won = (direction == actual_dir)
    db.update_result(trade_id, won)

    # 損益計算
    if won:
        gross = trade_size_usd / price
        pnl = gross - trade_size_usd - gross * 0.02
    else:
        pnl = -trade_size_usd

    # リスクマネージャーのポジションを決済（エクスポージャーを解放）
    risk_manager.settle_position(market_id, pnl)

    emoji = "✅ 勝ち" if won else "❌ 負け"
    await notifier.send(
        title="BTC 結果",
        message=(
            f"{emoji}\n"
            f"{'📈 UP' if direction == 'up' else '📉 DOWN'} ${trade_size_usd} @ {price:.3f}\n"
            f"BTC: ${btc_at_entry:,.0f} → ${btc_now:,.0f}\n"
            f"損益: {pnl:+.2f}$"
        ),
    )


async def _daily_summary_loop(
    db: TradeDB,
    notifier: Notifier,
    review_date: str | None = None,
) -> None:
    """毎日 09:00 JST（00:00 UTC）に日次サマリーをTelegramへ送信"""
    while True:
        now = datetime.utcnow()
        # 次の 00:00 UTC まで待機
        seconds_until_midnight = (
            (23 - now.hour) * 3600
            + (59 - now.minute) * 60
            + (60 - now.second)
        )
        await asyncio.sleep(seconds_until_midnight)

        s = db.daily_summary()
        total = db.total_summary()

        if s["total"] == 0:
            continue  # 取引がない日はスキップ

        win_rate_str = f"{s['win_rate']:.0%}" if s["total"] > 0 else "-"
        await notifier.send(
            title="📊 日次サマリー",
            message=(
                f"【{s['date']}】\n"
                f"取引数: {s['total']}回  "
                f"勝率: {win_rate_str}\n"
                f"勝: {s['wins']}  負: {s['losses']}\n"
                f"本日損益: {s['pnl']:+.2f}$\n"
                f"─────────────\n"
                f"累計損益: {total['pnl']:+.2f}$  "
                f"通算{total['total']}戦{total['wins']}勝"
            ),
        )

        # 振り返り通知（REVIEW_DATE と一致した日）
        if review_date and s["date"] == review_date:
            win_rate_total = f"{total['win_rate']:.1%}" if total["total"] > 0 else "-"
            await notifier.send(
                title="📅 戦略振り返りタイミング！",
                message=(
                    f"設定した振り返り日（{review_date}）です。\n"
                    f"─────────────\n"
                    f"累計: {total['total']}戦 {total['wins']}勝\n"
                    f"勝率: {win_rate_total}\n"
                    f"累計PnL: {total['pnl']:+.2f}$\n"
                    f"─────────────\n"
                    f"パラメータの見直しを検討してください"
                ),
            )


async def run_btc_updown(
    config: AppConfig,
    risk_manager: RiskManager,
    poly_connector,
    notifier: Notifier,
    trade_size_usd: float = 10.0,
    min_edge: float = 0.04,
    min_change_pct: float = 0.15,
    initial_balance: float = 20.0,
    daily_loss_limit: float = 15.0,
    use_kelly: bool = False,
    kelly_fraction: float = 0.25,
    kelly_min_size: float = 2.0,
    review_date: str | None = None,
) -> None:
    """BTC 5分 Up/Down ボットのメインループ（WebSocket版）"""

    logger.info("=== BTC 5分 Up/Down ボット起動（WebSocket版）===")
    logger.info(f"  サイズ: ${trade_size_usd} | エッジ閾値: {min_edge:.0%} | 変化率閾値: {min_change_pct:.2f}%")
    logger.info(f"  モード: {'📝 PAPER' if config.paper_trading else '🔴 LIVE'}")
    logger.info(f"  Kelly: {'有効' if use_kelly else '無効（固定サイズ）'} | 日次損失上限: ${daily_loss_limit}")
    if review_date:
        logger.info(f"  振り返り日: {review_date}")

    # DB・WebSocket・日次サマリーを初期化
    db = TradeDB()
    feed = BinancePriceFeed()
    feed_task = asyncio.create_task(feed.start())
    summary_task = asyncio.create_task(_daily_summary_loop(db, notifier, review_date))
    logger.info("Binance WebSocket 接続中...")

    # 初回データが届くまで少し待つ
    for _ in range(30):
        if feed.current_price > 0:
            break
        await asyncio.sleep(1)
    else:
        logger.warning("BTC価格未取得。接続を確認してください")

    logger.info(f"BTC現在価格: ${feed.current_price:,.0f}")

    last_traded_window = 0
    cooldown_windows_remaining = 0  # 連続負けクールダウン残ウィンドウ数

    try:
        # 累計サマリーをログに表示（try内で例外をキャッチ）
        total = db.total_summary()
        if total["total"] > 0:
            logger.info(
                f"累計: {total['total']}戦 {total['wins']}勝 "
                f"勝率={total['win_rate']:.0%} PnL={total['pnl']:+.2f}$"
            )

        while True:
            # 次のウィンドウ境界まで精密待機
            await _wait_for_window_open()

            window_ts = current_window_end()

            # 同じウィンドウで二重取引を防止
            if window_ts == last_traded_window:
                await asyncio.sleep(1)
                continue

            # ① 連続負けクールダウン確認
            if cooldown_windows_remaining > 0:
                cooldown_windows_remaining -= 1
                logger.info(f"⏸ クールダウン中 | 残り{cooldown_windows_remaining + 1}ウィンドウ")
                last_traded_window = window_ts
                continue

            # ② 日次損失上限確認
            daily = db.daily_summary()
            if daily["pnl"] < -daily_loss_limit:
                logger.warning(
                    f"⛔ 日次損失上限超過 | 本日: {daily['pnl']:+.2f}$ < -${daily_loss_limit:.0f} → 本日取引停止"
                )
                last_traded_window = window_ts
                continue

            # ③ 連続負け確認（ペーパートレード中は無効化）
            if not config.paper_trading:
                consecutive = db.consecutive_losses()
                if consecutive >= 3:
                    cooldown_windows_remaining = 3
                    logger.warning(f"⛔ {consecutive}連敗検出 → 15分クールダウン開始")
                    last_traded_window = window_ts
                    continue
                elif consecutive >= 2:
                    cooldown_windows_remaining = 1
                    logger.warning(f"⚠ {consecutive}連敗検出 → 次の1ウィンドウスキップ")
                    last_traded_window = window_ts
                    continue

            t0 = time.time()

            # ④ Polymarketの最新市場価格を取得
            market = await fetch_current_market()
            if market is None:
                logger.warning("マーケット未検出。スキップ")
                last_traded_window = window_ts
                continue

            t_market = time.time()
            logger.info(
                f"マーケット取得: {market.title} "
                f"Up={market.up_price:.3f} Down={market.down_price:.3f} "
                f"({(t_market - t0)*1000:.0f}ms)"
            )

            # ⑤ シグナル判定
            signal = analyze(
                feed=feed,
                up_price=market.up_price,
                down_price=market.down_price,
                min_edge=min_edge,
                min_change_pct=min_change_pct,
            )

            if not signal.trade:
                last_traded_window = window_ts
                continue

            # ⑥ Kelly サイジング（または固定サイズ）
            if use_kelly:
                total_pnl = db.total_summary()["pnl"]
                current_balance = max(initial_balance + total_pnl, kelly_min_size * 3)
                actual_size = _kelly_size(
                    p_win=signal.confidence,
                    market_price=signal.market_price,
                    balance=current_balance,
                    kelly_fraction=kelly_fraction,
                    min_size=kelly_min_size,
                    max_size=trade_size_usd,
                )
                logger.info(
                    f"Kelly サイジング: 残高=${current_balance:.2f} "
                    f"p_win={signal.confidence:.3f} → ${actual_size}"
                )
            else:
                actual_size = trade_size_usd

            # ⑦ リスクチェック
            approved, reason = risk_manager.can_trade(actual_size, market.condition_id)
            if not approved:
                logger.warning(f"リスク不合格: {reason}")
                last_traded_window = window_ts
                continue

            # ⑧ 発注
            if signal.direction == "up":
                token_id = market.up_token_id
                price = market.up_price
            else:
                token_id = market.down_token_id
                price = market.down_price

            result = await poly_connector.place_order(
                market_id=token_id,
                side="BUY",
                price=price,
                size=actual_size,
            )

            t_order = time.time()
            total_ms = (t_order - t0) * 1000

            if result:
                fee = actual_size * 0.002
                risk_manager.register_trade(market.condition_id, actual_size, fee)

                # ⑨ DB に記録
                trade_id = db.log_trade(TradeRecord(
                    direction=signal.direction,
                    market_id=token_id,
                    price=price,
                    size_usd=actual_size,
                    fee_usd=fee,
                    btc_price=signal.btc_now,
                    change_pct=signal.change_pct,
                    edge=signal.edge,
                    order_id=str(result) if result else "",
                ))

                kelly_tag = f" Kelly=${actual_size}" if use_kelly else ""
                logger.info(
                    f"✅ 発注完了: {signal.direction.upper()} ${actual_size}{kelly_tag} @ {price:.3f} "
                    f"総遅延={total_ms:.0f}ms"
                )
                await notifier.send(
                    title="BTC約定",
                    message=(
                        f"{'📈 UP' if signal.direction == 'up' else '📉 DOWN'} "
                        f"${actual_size} @ {price:.3f}\n"
                        f"BTC: ${signal.btc_ref:,.0f} → ${signal.btc_now:,.0f} "
                        f"(300s:{signal.change_pct:+.2f}% / 60s:{signal.change_pct_short:+.2f}%)\n"
                        f"edge={signal.edge:+.3f} 遅延={total_ms:.0f}ms\n"
                        f"{'📝 PAPER' if config.paper_trading else '🔴 LIVE'}"
                    ),
                )

                # ⑩ 5分後に結果を確認（バックグラウンド）
                asyncio.create_task(_check_trade_result(
                    db=db,
                    trade_id=trade_id,
                    direction=signal.direction,
                    btc_at_entry=signal.btc_now,
                    feed=feed,
                    notifier=notifier,
                    trade_size_usd=actual_size,
                    price=price,
                    risk_manager=risk_manager,
                    market_id=market.condition_id,
                ))
            else:
                logger.error(f"❌ 発注失敗 ({total_ms:.0f}ms)")

            last_traded_window = window_ts

    finally:
        feed_task.cancel()
        summary_task.cancel()
        db.close()
        logger.info("WebSocket切断")
