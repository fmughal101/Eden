"""
Strategy plugin registry
========================
Auto-discovers every module in this package and exposes them by KEY.

A strategy module must define:
    KEY          — short identifier (e.g. "sma_crossover")
    NAME         — human label (e.g. "SMA Crossover")
    DESCRIPTION  — one-line description
    PARAMS       — list of param specs (key, label, default, type, min/max)
    INDICATORS   — list of indicator specs (key, label, color, dash) for chart
    signals(df, params) -> pd.DataFrame  — adds 'signal' (+1/-1/0) column
                                           plus indicator columns named by INDICATORS keys
"""

import importlib
import pkgutil
from pathlib import Path

_strategies: dict = {}


def _load() -> None:
    pkg_dir = Path(__file__).parent
    for info in pkgutil.iter_modules([str(pkg_dir)]):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "KEY") and hasattr(module, "signals"):
            _strategies[module.KEY] = module


def get(key: str):
    if key not in _strategies:
        raise KeyError(f"Unknown strategy: {key!r}. Available: {list(_strategies)}")
    return _strategies[key]


def list_all() -> list:
    return [
        {
            "key": s.KEY,
            "name": s.NAME,
            "description": getattr(s, "DESCRIPTION", ""),
            "params": getattr(s, "PARAMS", []),
            "indicators": getattr(s, "INDICATORS", []),
            "builder": bool(getattr(s, "BUILDER", False)),
        }
        for s in _strategies.values()
    ]


_load()
