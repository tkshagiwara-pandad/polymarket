"""データベース（SQLite）のユニットテスト"""
import os
import pytest
from src.utils.database import Database


@pytest.fixture
def db(tmp_path):
    """テスト用の一時DBを作成"""
    return Database(str(tmp_path / "test_trades.db"))


def test_tables_created(db):
    """初期化でテーブルが作られる"""
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "trades" in tables
    assert "arb_opportunities" in tables
    assert "portfolio" in tables


def test_record_trade_returns_id(db):
    """トレード記録でIDが返る"""
    row_id = db.record_trade("market-1", "polymarket", "BUY", 0.55, 30.0, 0.06)
    assert row_id == 1


def test_record_trade_multiple(db):
    """複数トレードがそれぞれ別IDを持つ"""
    id1 = db.record_trade("market-1", "polymarket", "BUY", 0.55, 30.0)
    id2 = db.record_trade("market-2", "kalshi", "no", 0.47, 30.0)
    assert id1 != id2
    assert id2 == id1 + 1


def test_get_trade_history(db):
    """取引履歴が取得できる"""
    db.record_trade("market-1", "polymarket", "BUY", 0.55, 30.0)
    db.record_trade("market-2", "kalshi", "no", 0.47, 30.0)
    history = db.get_trade_history(limit=10)
    assert len(history) == 2
    # 最新順
    assert history[0]["market_id"] == "market-2"


def test_get_trade_history_limit(db):
    """limitパラメータが機能する"""
    for i in range(5):
        db.record_trade(f"market-{i}", "polymarket", "BUY", 0.5, 10.0)
    history = db.get_trade_history(limit=3)
    assert len(history) == 3


def test_record_opportunity_returns_id(db):
    """アービトラージ機会記録でIDが返る"""
    row_id = db.record_opportunity(
        topic="Fed rate cut",
        poly_market_id="poly-fed",
        kalshi_market_id="KALSHI-FED",
        poly_price=0.44,
        kalshi_price=0.60,
        edge=0.14,
        kelly_size=25.0,
    )
    assert row_id == 1


def test_update_portfolio_first_entry(db):
    """最初のポートフォリオ更新でpeakが残高と同値"""
    db.update_portfolio(1000.0)
    drawdown = db.get_current_drawdown()
    assert drawdown == pytest.approx(0.0)


def test_update_portfolio_drawdown(db):
    """残高が下がるとドローダウンが正しく計算される"""
    db.update_portfolio(1000.0)
    db.update_portfolio(900.0)   # 10%ドローダウン
    drawdown = db.get_current_drawdown()
    assert drawdown == pytest.approx(0.10)


def test_update_portfolio_peak_rises(db):
    """残高が増えたらpeakも更新されドローダウンは0"""
    db.update_portfolio(1000.0)
    db.update_portfolio(1100.0)  # 新高値
    drawdown = db.get_current_drawdown()
    assert drawdown == pytest.approx(0.0)


def test_get_current_drawdown_empty_db(db):
    """データなしの場合は0.0を返す"""
    drawdown = db.get_current_drawdown()
    assert drawdown == 0.0


def test_paper_trade_flag(db):
    """ペーパートレードフラグが保存される"""
    import sqlite3
    db.record_trade("market-1", "polymarket", "BUY", 0.5, 10.0, paper=True)
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute("SELECT paper FROM trades WHERE id=1").fetchone()
    assert row[0] == 1  # paper=True → 1


def test_live_trade_flag(db):
    """ライブトレードフラグが保存される"""
    import sqlite3
    db.record_trade("market-1", "polymarket", "BUY", 0.5, 10.0, paper=False)
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute("SELECT paper FROM trades WHERE id=1").fetchone()
    assert row[0] == 0  # paper=False → 0
