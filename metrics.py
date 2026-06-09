"""
Performance metrics
===================
Honest, reusable performance statistics computed from an *equity curve* — one
source of truth for the only question that matters: "did this actually work,
after costs, versus just holding?"

Deliberately dependency-free (stdlib ``math`` only) and provider-agnostic: it
takes an equity curve (a list of ``{"date","value"}`` points or plain floats) and
returns a metrics dict. The strategy backtester uses it now; the copy-trading
simulators (Congress / Superinvestor / Hyperliquid) can use the exact same
measurement later, so every signal source is judged on the same honest ruler.

Public API:
    compute(curve, *, exposure=None, periods_per_year=252, rf_annual=0.0) -> dict
    vs_benchmark(strategy_metrics, benchmark_metrics) -> dict
"""

import math
from typing import Optional

TRADING_DAYS = 252  # daily bars → annualization factor


def _values(curve) -> list:
    """Accept a list of {"date","value"} points or a list of numbers → [float]."""
    if not curve:
        return []
    if isinstance(curve[0], dict):
        return [float(p["value"]) for p in curve]
    return [float(v) for v in curve]


def _returns(values: list) -> list:
    """Period-over-period simple returns from an equity series."""
    out = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev > 0:
            out.append(values[i] / prev - 1.0)
    return out


def max_drawdown_pct(values: list) -> float:
    """Largest peak-to-trough decline, as a positive percentage (18.5 == -18.5%)."""
    peak = values[0] if values else 0.0
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < worst:
                worst = dd
    return round(abs(worst) * 100, 2)


def _empty() -> dict:
    return {
        "total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "volatility_pct": 0.0,
        "calmar": 0.0, "exposure_pct": None,
    }


def compute(curve, *, exposure: Optional[float] = None,
            periods_per_year: int = TRADING_DAYS, rf_annual: float = 0.0) -> dict:
    """Full metrics from an equity curve.

    ``exposure`` — fraction of the period actually holding a position (0..1);
                   pass ``1.0`` for buy & hold, ``None`` if unknown.
    ``rf_annual`` — annual risk-free rate for Sharpe/Sortino (default 0).
    """
    values = _values(curve)
    if len(values) < 2 or values[0] <= 0:
        m = _empty()
        if exposure is not None:
            m["exposure_pct"] = round(exposure * 100, 1)
        return m

    rets = _returns(values)
    n = len(values) - 1
    years = n / periods_per_year if periods_per_year else 0.0

    total_return = values[-1] / values[0] - 1.0
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    rf_per = rf_annual / periods_per_year if periods_per_year else 0.0
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    sd = math.sqrt(var)
    downside = [min(0.0, r - rf_per) for r in rets]
    dd_dev = math.sqrt(sum(d * d for d in downside) / len(downside)) if downside else 0.0

    ann = math.sqrt(periods_per_year)
    sharpe = ((mean - rf_per) / sd * ann) if sd > 0 else 0.0
    sortino = ((mean - rf_per) / dd_dev * ann) if dd_dev > 0 else 0.0
    vol_ann = sd * ann
    maxdd = max_drawdown_pct(values)
    calmar = (cagr * 100 / maxdd) if maxdd > 0 else 0.0

    m = {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": maxdd,                 # positive magnitude
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "volatility_pct": round(vol_ann * 100, 2),
        "calmar": round(calmar, 2),                # CAGR / max drawdown
        "exposure_pct": round(exposure * 100, 1) if exposure is not None else None,
    }
    return m


def vs_benchmark(strategy: dict, benchmark: dict) -> dict:
    """Summarize the strategy *relative to* its benchmark (buy & hold)."""
    excess = round(strategy["total_return_pct"] - benchmark["total_return_pct"], 2)
    # Positive dd_improvement = strategy suffered a SMALLER drawdown than buy & hold.
    dd_improvement = round(benchmark["max_drawdown_pct"] - strategy["max_drawdown_pct"], 2)
    return {
        "excess_return_pct": excess,
        "beats_benchmark": strategy["total_return_pct"] > benchmark["total_return_pct"],
        "dd_improvement_pct": dd_improvement,
        "sharpe_delta": round(strategy["sharpe"] - benchmark["sharpe"], 2),
    }
