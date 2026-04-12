"""
SQLiteデータベース管理
- トレード履歴の永続化
- ポートフォリオ残高の追跡
- ドローダウン計算
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger


class Database:
    def __init__(self, db_path: str = "data/trades.db"):
        Path(db_path).parent.mkdir(exist_ok=True)
        self.db_path = db_path
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    market_id   TEXT    NOT NULL,
                    venue       TEXT    NOT NULL,  -- polymarket / kalshi
                    side        TEXT    NOT NULL,  -- YES / NO
                    price       REAL    NOT NULL,
                    size        REAL    NOT NULL,
                    fee         REAL    NOT NULL DEFAULT 0,
                    pnl         REAL,
                    paper       INTEGER NOT NULL DEFAULT 1,  -- 1=paper, 0=live
                    status      TEXT    NOT NULL DEFAULT 'open',
                    notes       TEXT
                );

                CREATE TABLE IF NOT EXISTS arb_opportunities (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    NOT NULL,
                    topic           TEXT    NOT NULL,
                    poly_market_id  TEXT    NOT NULL,
                    kalshi_market_id TEXT   NOT NULL,
                    poly_price      REAL    NOT NULL,
                    kalshi_price    REAL    NOT NULL,
                    edge            REAL    NOT NULL,
                    kelly_size      REAL    NOT NULL,
                    executed        INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS portfolio (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    balance     REAL    NOT NULL,
                    peak        REAL    NOT NULL,
                    drawdown    REAL    NOT NULL DEFAULT 0
                );
            """)
        logger.info(f"Database initialized: {self.db_path}")

    def record_trade(
        self, market_id: str, venue: str, side: str,
        price: float, size: float, fee: float = 0.0,
        paper: bool = True, notes: str = ""
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (timestamp, market_id, venue, side, price, size, fee, paper, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), market_id, venue, side,
                 price, size, fee, int(paper), notes)
            )
            return cur.lastrowid

    def record_opportunity(
        self, topic: str, poly_market_id: str, kalshi_market_id: str,
        poly_price: float, kalshi_price: float, edge: float, kelly_size: float
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO arb_opportunities
                   (timestamp, topic, poly_market_id, kalshi_market_id,
                    poly_price, kalshi_price, edge, kelly_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), topic, poly_market_id,
                 kalshi_market_id, poly_price, kalshi_price, edge, kelly_size)
            )
            return cur.lastrowid

    def update_portfolio(self, balance: float) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(peak) as peak FROM portfolio"
            ).fetchone()
            peak = max(row["peak"] or balance, balance)
            drawdown = (peak - balance) / peak if peak > 0 else 0.0
            conn.execute(
                """INSERT INTO portfolio (timestamp, balance, peak, drawdown)
                   VALUES (?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), balance, peak, drawdown)
            )

    def get_current_drawdown(self) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT drawdown FROM portfolio ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["drawdown"] if row else 0.0

    def get_trade_history(self, limit: int = 50) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
