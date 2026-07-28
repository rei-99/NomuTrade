"""Simulation dataset loader (INT-04, TBD-06 resolved; design D-10..D-14).

Loads the three dataset packs from `data/` into the platform's own tables:

- `simulation_historical_data/<SYM>_2026_historical.csv` — daily OHLC rows,
  loaded only for dates before the live window (they overlap Jun 30–Jul 10;
  the minute bars win, D-13). `adjusted_close`/dividend/split columns are
  ignored (corporate actions are out of MVP scope, SRS 1.2).
- `simulation_price_data_July_1-Aug_30/simulated_<SYM>_live.csv` — 1-minute
  bars, the replay source of truth.
- `simulation_news_data_July_1-Aug_30/simulated_<Month>_news_2026.json` —
  market news with per-ticker sentiment, flattened into NewsItem +
  NewsSentiment (reference data, never replayed, D-14).
- Bonds (design 21 §A2): the dataset pack ships no bond data, so every bond
  with zero ticks gets a GENERATED daily + minute series from
  `history.generate_bond_series` (mean-reverting around par 100, minute bars
  on the equities' own live timestamps so bonds replay in lockstep).

Everything lands in the existing PriceTick/Instrument tables plus the news
tables (D-10: single store). Idempotent: instruments are upserted by symbol;
tick/news loads are skipped when their tables are non-empty. When the data
directory is missing, `load_dataset` returns False and the caller falls back
to the generated random-walk feed — tests and CI never depend on the dataset.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Instrument, NewsItem, NewsSentiment, PriceTick
from app.core.timeutil import as_utc
from app.modules.marketdata.history import generate_bond_series

logger = logging.getLogger(__name__)

HISTORICAL_DIR = "simulation_historical_data"
LIVE_DIR = "simulation_price_data_July_1-Aug_30"
NEWS_DIR = "simulation_news_data_July_1-Aug_30"

# Minute bars start 2026-06-30; daily history before that date is loaded as
# backfill, everything from the live window on comes from the live CSVs.
LIVE_START = date(2026, 6, 30)

# (symbol, name) — the dataset universe; mirrors the fallback list in seed.py.
DATASET_INSTRUMENTS: dict[str, str] = {
    "AAPL": "Apple",
    "GOOG": "Alphabet",
    "IBM": "IBM",
    "MSFT": "Microsoft",
    "TSLA": "Tesla",
    "UL": "Unilever",
    "WMT": "Walmart",
}

# (symbol, name) — generated bond universe (design 21 §A2): asset_class BOND,
# USD, lot 1000 (face value), tick 0.01, quoted % of par. The dataset has no
# bond feed; prices are generated (see history.generate_bond_series).
# Kept in sync with seed.py's INSTRUMENTS.
BOND_INSTRUMENTS: dict[str, str] = {
    "UST10Y": "US Treasury 4.25% 2035",
    "UST2Y": "US Treasury 3.75% 2027",
    "AAPL29": "Apple Corp 3.40% 2029",
    "MSFT31": "Microsoft Corp 3.10% 2031",
}

_CHUNK = 5000


def resolve_data_dir(configured: str) -> Path | None:
    """Resolve the configured DATA_DIR against cwd, its parent and the repo
    root (backend/app/modules/marketdata/loader.py -> parents[4])."""
    candidates = [
        Path(configured),
        Path("..") / configured,
        Path(__file__).resolve().parents[4] / configured,
    ]
    for candidate in candidates:
        if (candidate / HISTORICAL_DIR).is_dir() and (candidate / LIVE_DIR).is_dir():
            return candidate
    return None


def _symbol_from_filename(path: Path, suffix: str) -> str:
    name = path.name
    if name.startswith("simulated_"):
        name = name[len("simulated_"):]
    return name[: -len(suffix)] if suffix and name.endswith(suffix) else path.stem


async def ensure_dataset_instruments(session: AsyncSession) -> list[Instrument]:
    """Upsert the dataset universe: 7 equities + 4 bonds (matched by symbol).

    Instruments from other eras (e.g. the pre-dataset JPY seed) are retired
    (tradable=False) so they disappear from watchlists and cannot be traded —
    their price history is kept for reference.
    """
    existing = {
        i.symbol: i
        for i in (await session.execute(select(Instrument))).scalars().all()
    }
    instruments: list[Instrument] = []
    for symbol, name in DATASET_INSTRUMENTS.items():
        instrument = existing.get(symbol)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                name=name,
                asset_class="EQUITY",
                currency="USD",
                lot_size=Decimal("1"),
                tick_size=Decimal("0.01"),
                tradable=True,
            )
            session.add(instrument)
        instruments.append(instrument)
    for symbol, name in BOND_INSTRUMENTS.items():
        instrument = existing.get(symbol)
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                name=name,
                asset_class="BOND",
                currency="USD",
                lot_size=Decimal("1000"),  # face value per lot
                tick_size=Decimal("0.01"),
                tradable=True,
            )
            session.add(instrument)
        instruments.append(instrument)
    for symbol, instrument in existing.items():
        if (
            symbol not in DATASET_INSTRUMENTS
            and symbol not in BOND_INSTRUMENTS
            and instrument.tradable
        ):
            instrument.tradable = False
            logger.info("dataset loader: retired off-dataset instrument %s", symbol)
    await session.flush()
    return instruments


def _read_historical(path: Path, instrument_id: str) -> list[PriceTick]:
    rows: list[PriceTick] = []
    with path.open(newline="") as fh:
        for rec in csv.DictReader(fh):
            day = date.fromisoformat(rec["timestamp"])
            if day >= LIVE_START:
                continue  # overlap: live minute bars win (D-13)
            ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            rows.append(
                PriceTick(
                    instrument_id=instrument_id,
                    ts=ts,
                    open=Decimal(rec["open"]),
                    high=Decimal(rec["high"]),
                    low=Decimal(rec["low"]),
                    close=Decimal(rec["close"]),
                    volume=Decimal(rec["volume"]),
                )
            )
    return rows


def _read_live(path: Path, instrument_id: str) -> list[PriceTick]:
    rows: list[PriceTick] = []
    with path.open(newline="") as fh:
        for rec in csv.DictReader(fh):
            ts = datetime.strptime(
                rec["timestamp"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            rows.append(
                PriceTick(
                    instrument_id=instrument_id,
                    ts=ts,
                    open=Decimal(rec["open"]),
                    high=Decimal(rec["high"]),
                    low=Decimal(rec["low"]),
                    close=Decimal(rec["close"]),
                    volume=Decimal(rec["volume"]),
                )
            )
    return rows


async def _insert_chunked(session: AsyncSession, rows: list[PriceTick]) -> None:
    # Commit per chunk: the write lock is released between chunks so the
    # relay/sweeper can interleave on SQLite instead of failing on it.
    for i in range(0, len(rows), _CHUNK):
        session.add_all(rows[i : i + _CHUNK])
        await session.commit()


def _parse_news_file(path: Path) -> tuple[list[NewsItem], list[NewsSentiment]]:
    items: list[NewsItem] = []
    sentiments: list[NewsSentiment] = []
    payload: dict[str, list[dict]] = json.loads(path.read_text())
    for day_items in payload.values():
        for raw in day_items:
            ts_raw = raw.get("time_published", "")
            try:
                ts = datetime.strptime(ts_raw, "%Y%m%dT%H%M%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            item = NewsItem(
                ts=ts,
                title=raw.get("title", ""),
                topics=[t.get("topic", "") for t in raw.get("topics", [])],
            )
            items.append(item)
            for sent in raw.get("ticker_sentiment", []):
                sentiments.append(
                    NewsSentiment(
                        news_item=item,
                        ticker=sent.get("ticker", ""),
                        relevance=_to_decimal(sent.get("relevance_score")),
                        score=_to_decimal(sent.get("ticker_sentiment_score")),
                        label=sent.get("ticker_sentiment_label"),
                    )
                )
    return items, sentiments


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except Exception:
        return None


async def _reference_minute_timestamps(
    session: AsyncSession, by_symbol: dict[str, Instrument]
) -> list[datetime]:
    """Live-window minute timestamps of the first equity that has any.

    The generated bond series is built on these exact timestamps so bonds
    replay in lockstep with the equity tape.
    """
    live_from = datetime(*LIVE_START.timetuple()[:3], tzinfo=timezone.utc)
    for symbol in DATASET_INSTRUMENTS:
        instrument = by_symbol.get(symbol)
        if instrument is None:
            continue
        rows = (
            (
                await session.execute(
                    select(PriceTick.ts)
                    .where(
                        PriceTick.instrument_id == instrument.instrument_id,
                        PriceTick.ts >= live_from,
                    )
                    .order_by(PriceTick.ts)
                )
            )
            .scalars()
            .all()
        )
        if rows:
            return [as_utc(ts) for ts in rows]
    return []


async def load_dataset(session: AsyncSession, data_dir: Path) -> dict:
    """Load instruments, price history and news from the dataset packs.

    Idempotent per stage; safe to call on every startup.
    """
    stats: dict[str, int | str] = {"data_dir": str(data_dir)}

    instruments = await ensure_dataset_instruments(session)
    by_symbol = {i.symbol: i for i in instruments}
    stats["instruments"] = len(instruments)

    # Tick stage, per symbol (not a global table-empty check): instruments
    # that already have ticks are skipped, but a non-empty table — e.g. a dev
    # DB from the pre-dataset JPY era — must NOT block the dataset symbols
    # from loading (regression: "No price data for AAPL").
    have_ticks = set(
        (
            await session.execute(
                select(PriceTick.instrument_id).group_by(PriceTick.instrument_id)
            )
        )
        .scalars()
        .all()
    )
    hist_files = {
        _symbol_from_filename(p, "_2026_historical.csv"): p
        for p in (data_dir / HISTORICAL_DIR).glob("*.csv")
    }
    live_files = {
        _symbol_from_filename(p, "_live.csv"): p
        for p in (data_dir / LIVE_DIR).glob("*.csv")
    }
    loaded = 0
    for symbol, instrument in by_symbol.items():
        if instrument.instrument_id in have_ticks:
            continue
        hist_path = hist_files.get(symbol)
        if hist_path is not None:
            rows = _read_historical(hist_path, instrument.instrument_id)
            await _insert_chunked(session, rows)
            loaded += len(rows)
        live_path = live_files.get(symbol)
        if live_path is not None:
            rows = _read_live(live_path, instrument.instrument_id)
            await _insert_chunked(session, rows)
            loaded += len(rows)
    stats["price_ticks"] = loaded

    # Bond stage (design 21 §A2): the dataset pack ships no bond data, so
    # every bond with zero ticks gets a GENERATED series (see
    # history.generate_bond_series): daily backfill + minute bars on the
    # equity live CSVs' own timestamps. Idempotent like the tick stage.
    bond_ticks = 0
    bonds_missing = [
        instrument
        for symbol, instrument in by_symbol.items()
        if symbol in BOND_INSTRUMENTS and instrument.instrument_id not in have_ticks
    ]
    if bonds_missing:
        minute_timestamps = await _reference_minute_timestamps(session, by_symbol)
        if not minute_timestamps:
            logger.warning(
                "dataset loader: no equity minute bars found; "
                "generating bond daily history only"
            )
        for instrument in bonds_missing:
            rows = generate_bond_series(instrument, LIVE_START, minute_timestamps)
            await _insert_chunked(session, rows)
            bond_ticks += len(rows)
    stats["bond_ticks"] = bond_ticks

    news_count = await session.scalar(select(func.count(NewsItem.news_id)))
    news_dir = data_dir / NEWS_DIR
    if not news_count and news_dir.is_dir():
        items_total = 0
        sentiments_total = 0
        for path in sorted(news_dir.glob("*.json")):
            items, sentiments = _parse_news_file(path)
            session.add_all(items)
            # sentiments reference their item via relationship; added transitively
            for sent in sentiments:
                session.add(sent)
            await session.commit()  # per file: release the write lock
            items_total += len(items)
            sentiments_total += len(sentiments)
        stats["news_items"] = items_total
        stats["news_sentiments"] = sentiments_total
    else:
        stats["news_items"] = 0

    await session.commit()
    logger.info("dataset loader: %s", stats)
    return stats
