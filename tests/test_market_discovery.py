"""マーケット発見・マッチングのユニットテスト"""
import pytest
from src.connectors.market_discovery import (
    RawMarket,
    _normalize,
    jaccard_similarity,
    match_markets,
    fetch_polymarket_markets,
    fetch_kalshi_markets,
    _mock_polymarket,
    _mock_kalshi,
)


# ─── _normalize ──────────────────────────────────────────────────────────────

def test_normalize_lowercases():
    tokens = _normalize("Will The Fed CUT rates?")
    assert "fed" in tokens
    assert "cut" in tokens
    # ストップワード除去
    assert "will" not in tokens
    assert "the" not in tokens


def test_normalize_strips_punctuation():
    tokens = _normalize("Bitcoin: 100k in 2024!")
    assert "bitcoin" in tokens
    assert "100k" in tokens
    assert "2024" in tokens


def test_normalize_drops_short_words():
    tokens = _normalize("Is it ok or not")
    # 2文字以下は除外
    assert "is" not in tokens
    assert "it" not in tokens
    assert "ok" not in tokens


# ─── jaccard_similarity ───────────────────────────────────────────────────────

def test_jaccard_identical_titles():
    score = jaccard_similarity("Fed rate cut 2024", "Fed rate cut 2024")
    assert score == pytest.approx(1.0)


def test_jaccard_no_overlap():
    score = jaccard_similarity("bitcoin price 2024", "elections president vote")
    assert score == pytest.approx(0.0)


def test_jaccard_partial_overlap():
    score = jaccard_similarity("Fed rate cut December 2024", "Fed cut rates 2024")
    assert 0.0 < score < 1.0


def test_jaccard_empty_string():
    score = jaccard_similarity("", "Fed rate cut")
    assert score == pytest.approx(0.0)


# ─── match_markets ────────────────────────────────────────────────────────────

def test_match_returns_correct_pairs():
    poly = [
        RawMarket("poly-fed", "polymarket", "Will the Fed cut rates in 2024?", None, 50000),
    ]
    kalshi = [
        RawMarket("KALSHI-FED", "kalshi", "Fed rate cut December 2024", None, 40000),
    ]
    pairs = match_markets(poly, kalshi, threshold=0.2)
    assert len(pairs) == 1
    assert pairs[0][0] == "poly-fed"
    assert pairs[0][1] == "KALSHI-FED"


def test_match_filters_low_volume():
    poly = [RawMarket("poly-1", "polymarket", "Fed rate cut 2024", None, 100)]
    kalshi = [RawMarket("KALSHI-1", "kalshi", "Fed rate cut 2024", None, 100)]
    # min_volume=1000 なので両方フィルタされペアなし
    pairs = match_markets(poly, kalshi, threshold=0.2, min_volume=1000.0)
    assert pairs == []


def test_match_below_threshold_excluded():
    poly = [RawMarket("poly-btc", "polymarket", "Bitcoin halving 2024", None, 10000)]
    kalshi = [RawMarket("KALSHI-ELECT", "kalshi", "US presidential election winner", None, 10000)]
    pairs = match_markets(poly, kalshi, threshold=0.3)
    assert pairs == []


def test_match_topic_is_truncated_title():
    poly = [RawMarket("poly-fed", "polymarket", "Will the Fed cut rates in December 2024?", None, 50000)]
    kalshi = [RawMarket("KALSHI-FED", "kalshi", "Fed rate cut December 2024", None, 40000)]
    pairs = match_markets(poly, kalshi, threshold=0.2)
    assert len(pairs) == 1
    # topic は poly タイトルの最初60文字
    assert len(pairs[0][2]) <= 60


# ─── モックデータ ─────────────────────────────────────────────────────────────

def test_mock_polymarket_returns_markets():
    markets = _mock_polymarket()
    assert len(markets) >= 1
    for m in markets:
        assert m.venue == "polymarket"
        assert m.volume > 0


def test_mock_kalshi_returns_markets():
    markets = _mock_kalshi()
    assert len(markets) >= 1
    for m in markets:
        assert m.venue == "kalshi"
        assert m.volume > 0


def test_mock_pairs_can_be_matched():
    """モックデータ同士でマッチングできる"""
    pairs = match_markets(_mock_polymarket(), _mock_kalshi(), threshold=0.1, min_volume=0)
    assert len(pairs) >= 2  # Fed + Bitcoin は一致するはず


# ─── 非同期フェッチ（aiohttp なし = モックフォールバック）────────────────────

@pytest.mark.asyncio
async def test_fetch_polymarket_falls_back_to_mock(monkeypatch):
    """aiohttp が使えない環境でもモックデータを返す"""
    import src.connectors.market_discovery as md
    monkeypatch.setattr(md, "AIOHTTP_AVAILABLE", False)
    markets = await fetch_polymarket_markets()
    assert len(markets) >= 1


@pytest.mark.asyncio
async def test_fetch_kalshi_falls_back_to_mock(monkeypatch):
    import src.connectors.market_discovery as md
    monkeypatch.setattr(md, "AIOHTTP_AVAILABLE", False)
    markets = await fetch_kalshi_markets()
    assert len(markets) >= 1
