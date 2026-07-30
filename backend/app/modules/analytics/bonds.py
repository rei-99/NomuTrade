"""Bond pricing math (design 24 §D-24.3): YTM + duration, annual coupons.

Conventions (deliberate simplifications for a training platform):
- prices and coupons are % of par (par = 100), matching the bond quote
  convention of design 21 §A2;
- clean price — no accrued-interest modelling;
- cashflows are `n = max(1, round(years_to_maturity))` annual payments at
  t = 1..n years from today, the final one coupon + 100. With integer
  periods a par-priced bond's YTM is exactly its coupon, and the implied
  price at yield == coupon is exactly 100 — hand-checkable numbers.
- yields/coupons are percentage points (4.25 means 4.25 %).

Pure float functions over plain values — no ORM, no I/O.
"""

from __future__ import annotations

# Bisection bracket for the YTM solve (percent). Price is monotone
# decreasing in yield for positive cashflows, so bisection always converges
# inside the bracket; extreme market prices clamp at the bounds.
YTM_LOW_PCT = -99.0
YTM_HIGH_PCT = 100.0


def payment_count(years_to_maturity: float) -> int:
    """Number of remaining annual coupon payments (see module docstring)."""
    return max(1, round(years_to_maturity))


def price_from_yield(coupon_rate: float, n: int, ytm_pct: float) -> float:
    """Clean price (% of par) of an annual-coupon bond at `ytm_pct` %."""
    r = ytm_pct / 100.0
    discount = (1.0 + r) ** n
    coupons = sum(coupon_rate / (1.0 + r) ** t for t in range(1, n + 1))
    return coupons + 100.0 / discount


def solve_ytm(price: float, coupon_rate: float, n: int) -> float:
    """Yield-to-maturity (percent) implied by `price` — bisection solve."""
    lo, hi = YTM_LOW_PCT, YTM_HIGH_PCT
    if price >= price_from_yield(coupon_rate, n, lo):
        return lo
    if price <= price_from_yield(coupon_rate, n, hi):
        return hi
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if price_from_yield(coupon_rate, n, mid) > price:
            lo = mid  # price too high -> yield must rise
        else:
            hi = mid
    return (lo + hi) / 2.0


def macaulay_duration(coupon_rate: float, n: int, ytm_pct: float) -> float:
    """Macaulay duration in years: PV-weighted average cashflow time."""
    r = ytm_pct / 100.0
    price = price_from_yield(coupon_rate, n, ytm_pct)
    weighted = sum(
        t * coupon_rate / (1.0 + r) ** t for t in range(1, n + 1)
    )
    weighted += n * 100.0 / (1.0 + r) ** n
    return weighted / price


def modified_duration(coupon_rate: float, n: int, ytm_pct: float) -> float:
    """Modified duration: Macaulay / (1 + r) — price sensitivity per 1.0 r."""
    return macaulay_duration(coupon_rate, n, ytm_pct) / (1.0 + ytm_pct / 100.0)
