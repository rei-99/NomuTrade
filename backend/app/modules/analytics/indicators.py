"""Pure technical-indicator math (DESIGN 05: indicators are pure, cacheable,
unit-testable functions over the close series).

Every function takes a list of float closes and returns series aligned with the
input: entries before the warmup window are None. No external dependencies.
"""

from __future__ import annotations


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average; first value at index period-1."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first window."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for i in range(period, len(values)):
        current = (values[i] - current) * k + current
        out[i] = current
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI; first value at index period."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> list[tuple[float, float, float] | None]:
    """MACD line/signal/histogram; entries appear once all three are defined.

    Line = EMA(fast) - EMA(slow); signal = EMA(signal_period) of the line,
    seeded with the SMA of the first signal_period line values.
    """
    out: list[tuple[float, float, float] | None] = [None] * len(values)
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_ema, slow_ema)
    ]
    defined = [i for i, v in enumerate(line) if v is not None]
    if len(defined) < signal_period:
        return out
    seed_idx = defined[: signal_period]
    signal = sum(line[i] for i in seed_idx) / signal_period
    first = seed_idx[-1]
    out[first] = (line[first], signal, line[first] - signal)
    k = 2.0 / (signal_period + 1)
    for i in defined[signal_period:]:
        signal = (line[i] - signal) * k + signal
        out[i] = (line[i], signal, line[i] - signal)
    return out


def bollinger(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger bands (upper, middle, lower); population standard deviation."""
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        mean = middle[i]
        window = values[i - period + 1 : i + 1]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance**0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return upper, middle, lower
