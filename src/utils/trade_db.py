"""
取引履歴データベース（SQLite）
- 全取引を永続化（再起動しても消えない）
- 勝敗・損益を自動更新
- 日次・累計サマリーを提供
"""
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from loguru import logger


@dataclass
class TradeRecord:
    direction: str    # "up" or "down"
    market_id: str    # Polymarket token ID
    price: float      # エントリー価格（0〜1）
    size_usd: float   # ポジションサイズ（ドル）
    fee_usd: float    # 手数料
    btc_price: float  # 発注時のBTC価格
    change_pct: float # BTCの変化率
    edge: float       # 期待エッジ
    order_id: str = ""


class TradeDB:
    """SQLiteベースの取引履歴管理"""

    def __init__(self, db_path: str = "data/trades.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()
        logger.info(f"取引DB初期化: {db_path}")

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   INTEGER NOT NULL,
                datetime    TEXT    NOT NULL,
                direction   TEXT    NOT NULL,
                market_id   TEXT    NOT NULL,
                price       REAL    NOT NULL,
                size_usd    REAL    NOT NULL,
                fee_usd     REAL    NOT NULL,
                btc_price   REAL    NOT NULL,
                change_pct  REAL    NOT NULL,
                edge        REAL    NOT NULL,
                order_id    TEXT    DEFAULT '',
                status      TEXT    DEFAULT 'pending',
                pnl_usd     REAL    DEFAULT 0.0
            )
        """)
        self._conn.commit()
        # カラム追加マイグレーション（古いDBとの互換性）
        for col, definition in [
            ("status",  "TEXT DEFAULT 'pending'"),
            ("pnl_usd", "REAL DEFAULT 0.0"),
            ("order_id", "TEXT DEFAULT ''"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {definition}")
                self._conn.commit()
                logger.info(f"DB マイグレーション: {col} カラムを追加")
            except Exception:
                pass  # カラムが既に存在する場合はスキップ

    # ── 書き込み ──────────────────────────────────────

    def log_trade(self, record: TradeRecord) -> int:
        """取引を記録し、発行された ID を返す"""
        ts = int(time.time())
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        cur = self._conn.execute(
            """INSERT INTO trades
               (timestamp, datetime, direction, market_id, price,
                size_usd, fee_usd, btc_price, change_pct, edge, order_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, dt, record.direction, record.market_id, record.price,
             record.size_usd, record.fee_usd, record.btc_price,
             record.change_pct, record.edge, record.order_id),
        )
        self._conn.commit()
        logger.info(f"取引記録: id={cur.lastrowid} {record.direction.upper()} ${record.size_usd}")
        return cur.lastrowid

    def update_result(self, trade_id: int, won: bool) -> None:
        """勝敗と損益を更新する"""
        # 元の取引情報を取得
        row = self._conn.execute(
            "SELECT price, size_usd, fee_usd FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not row:
            return

        price, size_usd, fee_usd = row
        if won:
            # 勝ち: size_usd / price * 1.0 - size_usd - fee（Polymarketは勝ち時2%手数料）
            gross = size_usd / price
            pnl = gross - size_usd - gross * 0.02 - fee_usd
        else:
            pnl = -(size_usd + fee_usd)

        status = "won" if won else "lost"
        self._conn.execute(
            "UPDATE trades SET status=?, pnl_usd=? WHERE id=?",
            (status, round(pnl, 4), trade_id),
        )
        self._conn.commit()
        emoji = "✅" if won else "❌"
        logger.info(f"結果更新: id={trade_id} {emoji} PnL={pnl:+.2f}")

    # ── 集計 ──────────────────────────────────────────

    def daily_summary(self, target_date: str | None = None) -> dict:
        """指定日（デフォルト: 今日）の集計を返す"""
        d = target_date or date.today().strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT
                COUNT(*)                                         AS total,
                SUM(CASE WHEN status='won'  THEN 1 ELSE 0 END)  AS wins,
                SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END)  AS losses,
                COALESCE(SUM(pnl_usd), 0)                       AS pnl,
                COALESCE(SUM(fee_usd), 0)                       AS fees
               FROM trades WHERE datetime LIKE ? AND status != 'pending'""",
            (f"{d}%",),
        ).fetchone()
        total, wins, losses, pnl, fees = row
        return {
            "date": d,
            "total": total or 0,
            "wins": wins or 0,
            "losses": losses or 0,
            "pnl": pnl or 0.0,
            "fees": fees or 0.0,
            "win_rate": (wins / total) if total else 0.0,
        }

    def total_summary(self) -> dict:
        """全期間の累計集計を返す"""
        row = self._conn.execute(
            """SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN status='won' THEN 1 ELSE 0 END)  AS wins,
                COALESCE(SUM(pnl_usd), 0)                      AS pnl
               FROM trades WHERE status != 'pending'"""
        ).fetchone()
        total, wins, pnl = row
        total = total or 0
        wins = wins or 0
        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": (wins / total) if total else 0.0,
            "pnl": pnl or 0.0,
        }

    def consecutive_losses(self) -> int:
        """直近の連続負け数を返す（勝ちが出た時点でリセット）"""
        rows = self._conn.execute(
            "SELECT status FROM trades WHERE status != 'pending' "
            "ORDER BY timestamp DESC LIMIT 10"
        ).fetchall()
        count = 0
        for (status,) in rows:
            if status == "lost":
                count += 1
            else:
                break
        return count

    def close(self) -> None:
        self._conn.close()
