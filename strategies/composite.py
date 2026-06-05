"""
Composite strategy
==================
A user-defined strategy assembled from the indicator library and a list of
ENTRY (all-of) and EXIT (any-of) conditions. The frontend builder produces
the JSON shape consumed here; no `eval` is used — operators are dispatched
explicitly.

Params shape (all keys required, lists may be empty):

    {
      "indicators": [{"id": "sma_50", "type": "sma", "args": {"period": 50}}, ...],
      "entry": {"all": [<condition>, ...]},
      "exit":  {"any": [<condition>, ...]}
    }

A condition is `lhs <op> rhs` where each side is a tagged union:

    {"kind": "indicator", "id": "<existing indicator id>"}
    {"kind": "const",     "value": <number>}
    {"kind": "price",     "field": "close" | "high" | "low" | "open"}

Allowed ops: ">", "<", ">=", "<=", "==", "crosses_above", "crosses_below".

Signals: BUY (+1) when ALL entry conditions are true on a bar; SELL (-1)
when ANY exit condition is true (and entry is not). Otherwise 0.
The simulator already de-dupes consecutive +1s, so producing level-based
(non-crossing) signals is fine.
"""

import pandas as pd

from .indicators import INDICATOR_REGISTRY

KEY = "composite"
NAME = "Composite Builder"
DESCRIPTION = "Build a custom strategy from indicators + entry/exit rules."
PARAMS: list = []        # UI is custom; flat params unused.
INDICATORS: list = []    # Filled per-request via indicators_for(params).
BUILDER = True           # Flag for the frontend to render the builder UI.

ALLOWED_OPS = {">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"}
ALLOWED_PRICE_FIELDS = {"close", "high", "low", "open"}


# ─── Validation ───────────────────────────────────────────────────────────────

def _validate(params: dict) -> None:
    if not isinstance(params, dict):
        raise ValueError("params must be a dict")

    inds = params.get("indicators")
    if not isinstance(inds, list):
        raise ValueError("params.indicators must be a list")

    seen_ids = set()
    for i, ind in enumerate(inds):
        if not isinstance(ind, dict):
            raise ValueError(f"indicators[{i}] must be a dict")
        ind_id = ind.get("id")
        ind_type = ind.get("type")
        args = ind.get("args", {})
        if not isinstance(ind_id, str) or not ind_id:
            raise ValueError(f"indicators[{i}].id must be a non-empty string")
        if ind_id in seen_ids:
            raise ValueError(f"indicators[{i}].id duplicates earlier id {ind_id!r}")
        if ind_type not in INDICATOR_REGISTRY:
            raise ValueError(
                f"indicators[{i}].type {ind_type!r} not in "
                f"{sorted(INDICATOR_REGISTRY)}"
            )
        if not isinstance(args, dict):
            raise ValueError(f"indicators[{i}].args must be a dict")
        seen_ids.add(ind_id)

    for section_key, combiner_key in [("entry", "all"), ("exit", "any")]:
        section = params.get(section_key)
        if not isinstance(section, dict):
            raise ValueError(f"params.{section_key} must be a dict")
        conds = section.get(combiner_key)
        if not isinstance(conds, list):
            raise ValueError(f"params.{section_key}.{combiner_key} must be a list")
        for j, cond in enumerate(conds):
            _validate_condition(cond, seen_ids, where=f"{section_key}.{combiner_key}[{j}]")


def _validate_operand(op: dict, indicator_ids: set, where: str) -> None:
    if not isinstance(op, dict):
        raise ValueError(f"{where} must be a dict")
    kind = op.get("kind")
    if kind == "indicator":
        if op.get("id") not in indicator_ids:
            raise ValueError(f"{where} references unknown indicator id {op.get('id')!r}")
    elif kind == "const":
        if not isinstance(op.get("value"), (int, float)):
            raise ValueError(f"{where}.value must be a number")
    elif kind == "price":
        if op.get("field") not in ALLOWED_PRICE_FIELDS:
            raise ValueError(
                f"{where}.field must be one of {sorted(ALLOWED_PRICE_FIELDS)}"
            )
    else:
        raise ValueError(f"{where}.kind must be one of indicator|const|price")


def _validate_condition(cond: dict, indicator_ids: set, where: str) -> None:
    if not isinstance(cond, dict):
        raise ValueError(f"{where} must be a dict")
    if cond.get("op") not in ALLOWED_OPS:
        raise ValueError(f"{where}.op must be one of {sorted(ALLOWED_OPS)}")
    _validate_operand(cond.get("lhs", {}), indicator_ids, f"{where}.lhs")
    _validate_operand(cond.get("rhs", {}), indicator_ids, f"{where}.rhs")


# ─── Indicator computation ────────────────────────────────────────────────────

def _compute_indicators(df: pd.DataFrame, indicators: list) -> dict[str, list[str]]:
    """For each declared indicator, write its output column(s) onto df. Returns
    a map from user id → list of column names actually written (multi-output
    indicators write more than one)."""
    written: dict[str, list[str]] = {}
    for ind in indicators:
        spec = INDICATOR_REGISTRY[ind["type"]]
        args = ind.get("args", {})
        kwargs = {a: args[a] for a in spec["args"] if a in args}
        target = df["Close"] if spec["input"] == "close" else df
        result = spec["fn"](target, **kwargs)
        outputs = spec["outputs"]
        if len(outputs) == 1:
            col = ind["id"]
            df[col] = result
            written[ind["id"]] = [col]
        else:
            assert isinstance(result, tuple) and len(result) == len(outputs)
            cols = []
            for suffix, series in zip(outputs, result):
                col = ind["id"] + suffix
                df[col] = series
                cols.append(col)
            written[ind["id"]] = cols
    return written


# ─── Operand resolution ───────────────────────────────────────────────────────

def _resolve(operand: dict, df: pd.DataFrame, written: dict[str, list[str]]):
    kind = operand["kind"]
    if kind == "indicator":
        cols = written[operand["id"]]
        # For multi-output indicators (MACD, Bollinger), referencing the bare
        # id resolves to the *first* output (the line). Users wanting bands
        # would reference e.g. `bollinger_20_2_upper` directly via a
        # separate indicator declaration. v1 keeps this simple.
        return df[cols[0]]
    if kind == "const":
        return float(operand["value"])
    if kind == "price":
        col_map = {"close": "Close", "high": "High", "low": "Low", "open": "Open"}
        return df[col_map[operand["field"]]]
    raise ValueError(f"unknown operand kind {kind!r}")  # pragma: no cover


def _eval_condition(cond: dict, df: pd.DataFrame,
                    written: dict[str, list[str]]) -> pd.Series:
    lhs = _resolve(cond["lhs"], df, written)
    rhs = _resolve(cond["rhs"], df, written)
    op = cond["op"]

    if op == ">":  return lhs > rhs
    if op == "<":  return lhs < rhs
    if op == ">=": return lhs >= rhs
    if op == "<=": return lhs <= rhs
    if op == "==": return lhs == rhs

    # crosses_* operators need both sides as series for prev-bar state.
    if op in ("crosses_above", "crosses_below"):
        lhs_s = lhs if isinstance(lhs, pd.Series) else pd.Series(lhs, index=df.index)
        rhs_s = rhs if isinstance(rhs, pd.Series) else pd.Series(rhs, index=df.index)
        prev_l, prev_r = lhs_s.shift(1), rhs_s.shift(1)
        if op == "crosses_above":
            return (prev_l <= prev_r) & (lhs_s > rhs_s)
        return (prev_l >= prev_r) & (lhs_s < rhs_s)

    raise ValueError(f"unknown op {op!r}")  # pragma: no cover


# ─── Public hooks ─────────────────────────────────────────────────────────────

def signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    _validate(params)
    df = df.copy()

    written = _compute_indicators(df, params["indicators"])

    entry_conds = params["entry"]["all"]
    exit_conds = params["exit"]["any"]

    if entry_conds:
        entry_mask = pd.Series(True, index=df.index)
        for cond in entry_conds:
            entry_mask &= _eval_condition(cond, df, written).fillna(False)
    else:
        entry_mask = pd.Series(False, index=df.index)

    if exit_conds:
        exit_mask = pd.Series(False, index=df.index)
        for cond in exit_conds:
            exit_mask |= _eval_condition(cond, df, written).fillna(False)
    else:
        exit_mask = pd.Series(False, index=df.index)

    df["signal"] = 0
    df.loc[entry_mask, "signal"] = 1
    df.loc[exit_mask & ~entry_mask, "signal"] = -1
    return df


def indicators_for(params: dict) -> list[dict]:
    """Return chart metadata for every declared indicator. Each entry is tagged
    with pane="price" or pane="oscillator" so the frontend can place
    price-scale indicators (SMA, EMA, Bollinger bands) on the main chart and
    off-scale indicators (RSI, MACD, ATR) on a separate oscillator chart."""
    out = []
    for ind in params.get("indicators", []):
        if ind.get("type") not in INDICATOR_REGISTRY:
            continue
        spec = INDICATOR_REGISTRY[ind["type"]]
        pane = "price" if spec["price_scale"] else "oscillator"
        outputs = spec["outputs"]
        colors = spec["colors"]
        for suffix, color in zip(outputs, colors):
            col = ind["id"] + suffix
            label = spec["label"]
            args = ind.get("args", {})
            arg_str = ",".join(str(args[a]) for a in spec["args"] if a in args)
            if arg_str:
                label = f"{label}({arg_str})"
            if suffix:
                label = f"{label}{suffix}"
            out.append({
                "key":   col,
                "label": label,
                "color": color,
                "dash":  spec["dash"],
                "pane":  pane,
            })
    return out
