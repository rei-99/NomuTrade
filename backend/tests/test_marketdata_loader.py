"""Dataset loader tests (INT-04, D-10..D-14).

Builds a miniature dataset in tmp_path (2 symbols, a few daily + minute rows,
one news day) and drives app.modules.marketdata.loader directly. The real
`data/` pack is never used in tests.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import create_all, get_engine, get_sessionmaker
from app.core.models import Instrument, NewsItem, NewsSentiment, PriceTick
from app.modules.marketdata.loader import (
    LIVE_START,
    ensure_dataset_instruments,
    load_dataset,
    resolve_data_dir,
)

HIST_DIR = "simulation_historical_data"
LIVE_DIR = "simulation_price_data_July_1-Aug_30"
NEWS_DIR = "simulation_news_data_July_1-Aug_30"

HIST_CSV = """timestamp,open,high,low,close,adjusted_close,volume,dividend_amount,split_coefficient
2026-06-28,100,101,99,100.5,100.5,1000,0.0,1.0
2026-06-29,100.5,102,100,101.5,101.5,1100,0.0,1.0
2026-07-01,101.5,103,101,102.5,102.5,1200,0.0,1.0
"""

LIVE_CSV = """timestamp,open,high,low,close,volume
2026-06-30 09:30:00,200,201,199,200.5,5000
2026-06-30 09:31:00,200.5,202,200,201.5,6000
2026-07-01 09:30:00,201.5,203,201,202.5,7000
"""

NEWS_JSON = {
    "20260701": [
        {
            "title": "Tesla leaps on delivery beat",
            "time_published": "20260701T062006",
            "topics": [{"topic": "Technology", "relevance_score": "1.0"}],
            "ticker_sentiment": [
                {
                    "ticker": "TSLA",
                    "relevance_score": "0.9",
                    "ticker_sentiment_score": "0.45",
                    "ticker_sentiment_label": "Bullish",
                }
            ],
        },
        {
            "title": "Market drifts in quiet session",
            "time_published": "20260701T083011",
            "topics": [],
            "ticker_sentiment": [
                {
                    "ticker": "TSLA",
                    "relevance_score": "0.5",
                    "ticker_sentiment_score": "-0.05",
                    "ticker_sentiment_label": "Neutral",
                },
                {
                    "ticker": "GOOG",
                    "relevance_score": "0.4",
                    "ticker_sentiment_score": "0.02",
                    "ticker_sentiment_label": "Neutral",
                },
            ],
        },
    ]
}


def _write_mini_dataset(root):
    data_dir = root / "mini-data"
    for sub in (HIST_DIR, LIVE_DIR, NEWS_DIR):
        (data_dir / sub).mkdir(parents=True)
    (data_dir / HIST_DIR / "TSLA_2026_historical.csv").write_text(HIST_CSV)
    (data_dir / LIVE_DIR / "simulated_TSLA_live.csv").write_text(LIVE_CSV)
    (data_dir / NEWS_DIR / "simulated_July_news_2026.json").write_text(
        json.dumps(NEWS_JSON)
    )
    return data_dir


@pytest.fixture
async def session(tmp_path):
    from app.config import Settings

    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/loader_test.db",
        DATA_DIR=str(tmp_path / "mini-data"),
    )
    engine = get_engine(settings)
    await create_all(engine)
    sessionmaker = get_sessionmaker(settings, engine)
    async with sessionmaker() as s:
        yield s
    await engine.dispose()


def test_resolve_data_dir(tmp_path):
    assert resolve_data_dir(str(tmp_path / "missing")) is None
    data_dir = _write_mini_dataset(tmp_path)
    assert resolve_data_dir(str(data_dir)) == data_dir


async def test_load_dataset(tmp_path, session):
    data_dir = _write_mini_dataset(tmp_path)
    stats = await load_dataset(session, data_dir)

    # Instruments upserted (the full dataset universe is ensured: 7 equities
    # + 4 bonds, design 21 §A2).
    instruments = (await session.execute(select(Instrument))).scalars().all()
    assert len(instruments) == 11
    tsla = next(i for i in instruments if i.symbol == "TSLA")
    assert tsla.currency == "USD" and tsla.lot_size == 1 and tsla.tradable
    assert stats["instruments"] == 11

    # Ticks: 2 historical rows (the 2026-07-01 row is >= LIVE_START and must
    # be skipped — live window wins, D-13) + 3 minute bars.
    ticks = (
        (
            await session.execute(
                select(PriceTick)
                .where(PriceTick.instrument_id == tsla.instrument_id)
                .order_by(PriceTick.ts)
            )
        )
        .scalars()
        .all()
    )
    assert len(ticks) == 2 + 3
    assert all(t.ts.date() < LIVE_START for t in ticks[:2])
    assert float(ticks[2].close) == 200.5  # first live bar
    assert stats["price_ticks"] == 5

    # Bonds (§A2): the dataset ships no bond data, so each bond gets a
    # generated series — 120 daily bars + one minute bar per reference live
    # timestamp (TSLA's 3 here).
    assert stats["bond_ticks"] == 4 * (120 + 3)

    # News flattened: 2 items, 3 sentiment rows, ticker link intact.
    assert stats["news_items"] == 2
    assert stats["news_sentiments"] == 3
    items = (await session.execute(select(NewsItem))).scalars().all()
    assert {i.title for i in items} == {
        "Tesla leaps on delivery beat",
        "Market drifts in quiet session",
    }
    sent = (
        (await session.execute(select(NewsSentiment).where(NewsSentiment.ticker == "TSLA")))
        .scalars()
        .all()
    )
    assert len(sent) == 2
    assert {s.label for s in sent} == {"Bullish", "Neutral"}
    bullish = next(s for s in sent if s.label == "Bullish")
    assert float(bullish.score) == 0.45


async def test_load_dataset_idempotent(tmp_path, session):
    data_dir = _write_mini_dataset(tmp_path)
    first = await load_dataset(session, data_dir)
    second = await load_dataset(session, data_dir)
    assert first["price_ticks"] == 5 and first["news_items"] == 2
    assert second["price_ticks"] == 0 and second["news_items"] == 0
    assert second["bond_ticks"] == 0  # bonds already have their generated ticks
    tick_count = await session.scalar(select(func.count(PriceTick.instrument_id)))
    news_count = await session.scalar(select(func.count(NewsItem.news_id)))
    # 5 equity rows + 4 bonds × (120 daily + 3 generated minute bars).
    assert tick_count == 5 + 4 * (120 + 3) and news_count == 2


async def test_ensure_dataset_instruments_upsert(tmp_path, session):
    created = await ensure_dataset_instruments(session)
    await session.commit()
    again = await ensure_dataset_instruments(session)
    await session.commit()
    assert len(created) == len(again) == 11  # 7 equities + 4 bonds (§A2)
    count = await session.scalar(select(func.count(Instrument.instrument_id)))
    assert count == 11


async def test_load_dataset_with_foreign_ticks_present(tmp_path, session):
    """Regression: a non-empty price_ticks table (e.g. a dev DB from the
    pre-dataset era) must not block dataset symbols from loading — only
    symbols that already have ticks are skipped."""
    from datetime import datetime, timezone

    foreign = Instrument(
        symbol="OLD1.X",
        name="Legacy instrument",
        asset_class="EQUITY",
        currency="JPY",
        lot_size=100,
        tick_size=Decimal("0.5"),
        tradable=True,
    )
    session.add(foreign)
    await session.flush()
    session.add(
        PriceTick(
            instrument_id=foreign.instrument_id,
            ts=datetime(2026, 1, 5, tzinfo=timezone.utc),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )
    )
    await session.commit()

    data_dir = _write_mini_dataset(tmp_path)
    stats = await load_dataset(session, data_dir)

    # Dataset symbol loaded despite the non-empty table; foreign row intact.
    tsla = await session.scalar(select(Instrument).where(Instrument.symbol == "TSLA"))
    tsla_ticks = await session.scalar(
        select(func.count(PriceTick.instrument_id)).where(
            PriceTick.instrument_id == tsla.instrument_id
        )
    )
    foreign_ticks = await session.scalar(
        select(func.count(PriceTick.instrument_id)).where(
            PriceTick.instrument_id == foreign.instrument_id
        )
    )
    assert tsla_ticks == 5
    assert foreign_ticks == 1
    assert stats["price_ticks"] == 5

    # Off-dataset instruments are retired (hidden from watchlists, untradable).
    await session.refresh(foreign)
    assert foreign.tradable is False
    dataset_symbols = {
        i.symbol for i in (await session.execute(select(Instrument))).scalars().all()
        if i.tradable
    }
    assert dataset_symbols == {
        "AAPL", "GOOG", "IBM", "MSFT", "TSLA", "UL", "WMT",
        "UST10Y", "UST2Y", "AAPL29", "MSFT31",  # generated bonds (§A2)
    }
