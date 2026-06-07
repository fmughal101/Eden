"""
Congress trade data
====================
Fetches STOCK Act periodic transaction reports and computes per-member
performance statistics using yfinance for historical prices.

Data is sourced through a pluggable provider (see ``_PROVIDER``). The current
provider is Financial Modeling Prep (FMP) — a free-tier API (free key, no card,
read from the ``FMP_API_KEY`` env var) that serves the public STOCK Act
disclosures as clean JSON. Trades are enriched with party / home-state / real
Bioguide photo using the free, no-key @unitedstates congress-legislators dataset.

The provider boundary is deliberate: a future self-hosted scraper (Senate EFD
CSRF handshake + House Clerk PDF parsing) can implement ``fetch_raw_trades`` and
drop in without touching the performance, simulation, or UI layers.

Public API:
    fetch_trades(days=180)         → list of trade dicts
    member_list()                  → members with summary performance stats
    member_trades(member_id)       → trades for one member
    member_performance(member_id)  → detailed performance stats
    simulate_follow(member_id, capital) → equity curve + return

Config:
    FMP_API_KEY  — free key from https://site.financialmodelingprep.com/
                   (read from environment or a .env file next to this module)
"""

import hashlib
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests
import yfinance as yf


# ── .env loader (no external dependency) ───────────────────────────────────────

def _load_env_file() -> None:
    """Populate os.environ from a .env file next to this module, if present."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_env_file()


class MissingAPIKey(RuntimeError):
    """Raised when FMP_API_KEY is not configured."""


class APIUnavailable(RuntimeError):
    """Raised when the provider rejects a configured key (rate-limited, not yet
    activated, or the dataset isn't on the current plan) and no cached data is
    available to fall back on."""


def _fmp_key() -> str:
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        raise MissingAPIKey(
            "FMP_API_KEY is not set. Get a free key at "
            "https://site.financialmodelingprep.com/ and add it to a .env file "
            "next to congress_data.py:  FMP_API_KEY=your_key_here"
        )
    return key


def is_mock() -> bool:
    """True when CONGRESS_MOCK is enabled — serves generated demo data so the UI
    can be exercised without a working FMP key."""
    return os.getenv("CONGRESS_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


# ── Cache ──────────────────────────────────────────────────────────────────────
_TRADE_CACHE: dict = {}           # key → (result, expires_ts)
_PERF_CACHE: dict = {}
_REF_CACHE: dict = {}             # legislators reference index
_CACHE_LOCK = threading.Lock()
_TRADES_TTL = 3600                # 1 hour
_PERF_TTL   = 21600               # 6 hours
_REF_TTL    = 86400               # 24 hours

# ── Price cache (avoids redundant yfinance calls) ──────────────────────────────
_PRICE_CACHE: dict = {}
_PRICE_LOCK = threading.Lock()

# ── HTTP session ───────────────────────────────────────────────────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Eden-TradingDashboard/1.0 (educational use)",
    "Accept": "application/json",
})

FMP_BASE = "https://financialmodelingprep.com/stable"
# Persist the last successful pull so a stingy free tier (or an offline restart)
# still shows data. Refreshed whenever a live fetch succeeds.
DISK_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "congress_cache.json")
FMP_PAGE_LIMIT = 100              # records per request
FMP_MAX_PAGES = 4                 # stay frugal — free tier throttles aggressively
LEGISLATORS_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
# @unitedstates member photo CDN — reliable headshots keyed by Bioguide ID.
PHOTO_CDN = "https://unitedstates.github.io/images/congress/450x550/{bioguide_id}.jpg"

PARTY_COLORS = {"R": "red", "D": "blue", "I": "amber"}


# ── Utility ────────────────────────────────────────────────────────────────────

def _cache_get(store: dict, key: str):
    with _CACHE_LOCK:
        entry = store.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(store: dict, key: str, value, ttl: int):
    with _CACHE_LOCK:
        store[key] = (value, time.time() + ttl)


def _get_prices(ticker: str, start: date, end: date) -> dict:
    """Returns {date_str: close_price} for the given range."""
    cache_key = f"{ticker}:{start}:{end}"
    with _PRICE_LOCK:
        cached = _PRICE_CACHE.get(cache_key)
    if cached:
        return cached
    try:
        hist = yf.Ticker(ticker).history(
            start=str(start), end=str(end + timedelta(days=5)),
            auto_adjust=True,
        )
        result = {str(idx.date()): float(row["Close"]) for idx, row in hist.iterrows()}
    except Exception:
        result = {}
    with _PRICE_LOCK:
        _PRICE_CACHE[cache_key] = result
    return result


def _nearest_price(prices: dict, target_date: date) -> Optional[float]:
    """Closing price on or AFTER target_date (up to 5 days ahead) — for buy/entry
    execution: you fill on the next trading day."""
    for offset in range(6):
        key = str(target_date + timedelta(days=offset))
        if key in prices:
            return prices[key]
    return None


def _price_asof(prices: dict, target_date: date) -> Optional[float]:
    """Last close on or BEFORE target_date (up to ~7 days back) — for marking a
    position to market / valuing on a date that may be a weekend or holiday (e.g.
    'today' when today is a Saturday)."""
    for offset in range(8):
        key = str(target_date - timedelta(days=offset))
        if key in prices:
            return prices[key]
    return None


def _trade_id(member_id: str, ticker: str, trade_date: str, ttype: str) -> str:
    raw = f"{member_id}:{ticker}:{trade_date}:{ttype}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ── Legislators reference (party / state / Bioguide photo) ──────────────────────

def _legislators_index() -> dict:
    """
    Build a lookup from member name → {party, state, chamber, bioguide_id} using
    the free, no-key @unitedstates congress-legislators dataset. Keyed by
    lowercase last name; values are lists (to disambiguate by first name).
    """
    cached = _cache_get(_REF_CACHE, "legislators")
    if cached is not None:
        return cached
    index: dict = defaultdict(list)
    try:
        resp = _SESSION.get(LEGISLATORS_URL, timeout=20)
        resp.raise_for_status()
        for m in resp.json():
            name = m.get("name", {})
            last = (name.get("last") or "").strip().lower()
            first = (name.get("first") or "").strip().lower()
            if not last:
                continue
            term = (m.get("terms") or [{}])[-1]
            index[last].append({
                "first": first,
                "party": _clean_party(term.get("party") or ""),
                "state": (term.get("state") or "").upper(),
                "chamber": "Senate" if term.get("type") == "sen" else "House",
                "bioguide_id": (m.get("id") or {}).get("bioguide") or "",
            })
    except Exception:
        index = defaultdict(list)
    index = dict(index)
    _cache_set(_REF_CACHE, "legislators", index, _REF_TTL)
    return index


def _match_legislator(first: str, last: str) -> Optional[dict]:
    idx = _legislators_index()
    candidates = idx.get((last or "").strip().lower(), [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    f = (first or "").strip().lower()
    for c in candidates:
        if f and (c["first"].startswith(f[:3]) or f.startswith(c["first"][:3])):
            return c
    return candidates[0]


# ── Financial Modeling Prep provider ───────────────────────────────────────────

def _pick(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _split_name(rec: dict) -> tuple:
    """Return (first, last) from an FMP record (handles 'office' = 'Last, First')."""
    first = _pick(rec, "firstName", "first_name")
    last = _pick(rec, "lastName", "last_name")
    if first or last:
        return first.strip(), last.strip()
    office = _pick(rec, "office", "representative", "senator", "name")
    if "," in office:
        last, _, first = office.partition(",")
        return first.strip(), last.strip()
    parts = office.split()
    if len(parts) >= 2:
        return parts[0].strip(), parts[-1].strip()
    return "", office.strip()


def _normalize_fmp(rec: dict, chamber: str, cutoff: date) -> Optional[dict]:
    ticker = (_pick(rec, "symbol", "ticker")).strip().upper()
    if not ticker or not re.match(r"^[A-Z.]{1,6}$", ticker):
        return None

    ttype_raw = _pick(rec, "type", "transactionType").lower()
    if "purchase" in ttype_raw or ttype_raw == "buy":
        ttype = "Purchase"
    elif "sale" in ttype_raw or "sell" in ttype_raw:
        ttype = "Sale"
    else:
        return None

    trade_date = str(_pick(rec, "transactionDate", "transaction_date", "date"))[:10]
    disclose_date = str(_pick(rec, "disclosureDate", "disclosure_date",
                              "dateRecieved", "dateReceived"))[:10]
    if trade_date:
        td = _parse_date(trade_date)
        if td and td < cutoff:
            return None

    first, last = _split_name(rec)
    full_name = f"{last}, {first}".strip(", ") or _pick(rec, "office", default="Unknown")

    leg = _match_legislator(first, last)
    if leg:
        party = leg["party"]
        state = leg["state"]
        bioguide = leg["bioguide_id"]
        chamber = leg["chamber"] or chamber
    else:
        party = _clean_party(_pick(rec, "party"))
        state = (_pick(rec, "district", "state")[:2]).upper() or "—"
        bioguide = ""

    member_id = bioguide or ("X_" + re.sub(r"[^A-Z]", "", (last + first).upper())[:38])
    amt_low, amt_high = _parse_amount(_pick(rec, "amount", "transactionAmount"))

    return {
        "id": _trade_id(member_id, ticker, trade_date, ttype),
        "member": full_name,
        "member_id": member_id,
        "bioguide_id": bioguide,
        "party": party or "?",
        "chamber": chamber,
        "state": state or "—",
        "ticker": ticker,
        "asset_name": _pick(rec, "assetDescription", "assetName", default=ticker),
        "type": ttype,
        "amount_low": amt_low,
        "amount_high": amt_high,
        "trade_date": trade_date,
        "disclosed_date": disclose_date,
        "disclosure_lag_days": _calc_lag(trade_date, disclose_date),
    }


def _fetch_fmp_chamber(endpoint: str, chamber: str, days: int) -> list:
    """
    Page through an FMP latest-disclosures endpoint and normalize. Frugal by
    design: the free tier throttles hard and reports throttling as a misleading
    401 "Invalid API KEY". We cap pages, pause briefly between requests, and
    surface a 401 on the very first page (no data yet) as APIUnavailable so the
    UI can explain "key not active / over limit" — distinct from a missing key.
    """
    cutoff = date.today() - timedelta(days=days)
    key = _fmp_key()
    trades: list = []
    for page in range(0, FMP_MAX_PAGES):
        try:
            resp = _SESSION.get(
                f"{FMP_BASE}/{endpoint}",
                params={"page": page, "limit": FMP_PAGE_LIMIT, "apikey": key},
                timeout=20,
            )
        except Exception:
            break

        if resp.status_code == 401:
            # First page with nothing collected → the key isn't working right now.
            if page == 0 and not trades:
                raise APIUnavailable(
                    "FMP returned 401 for a configured key. A newly created key can "
                    "take a few minutes to activate (and may need email verification); "
                    "the free tier also throttles bursts. Wait a bit and retry."
                )
            break  # already have some data this run — just stop paging

        try:
            resp.raise_for_status()
            batch = resp.json()
        except Exception:
            break
        if not isinstance(batch, list) or not batch:
            break

        for rec in batch:
            norm = _normalize_fmp(rec, chamber, cutoff)
            if norm:
                trades.append(norm)

        # Reverse-chronological feed: once an entire page predates the window, stop.
        oldest_in_page = min(
            (d for d in (
                _parse_date(str(_pick(r, "transactionDate", "transaction_date", "date"))[:10])
                for r in batch
            ) if d),
            default=None,
        )
        if oldest_in_page and oldest_in_page < cutoff:
            break
        time.sleep(0.4)  # be gentle with the free-tier rate limit
    return trades


def _fetch_senate(days: int) -> list:
    return _fetch_fmp_chamber("senate-latest", "Senate", days)


def _fetch_house(days: int) -> list:
    return _fetch_fmp_chamber("house-latest", "House", days)


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_amount(s: str) -> tuple:
    """Parse STOCK Act range strings like '$1,001 - $15,000' → (1001, 15000)."""
    nums = re.findall(r"[\d,]+", str(s))
    vals = [int(n.replace(",", "")) for n in nums]
    if len(vals) >= 2:
        return vals[0], vals[1]
    if len(vals) == 1:
        return vals[0], vals[0]
    return 0, 0


def _clean_party(p: str) -> str:
    p = (p or "").strip().upper()
    if p.startswith("R"):
        return "R"
    if p.startswith("D"):
        return "D"
    if p.startswith("I"):
        return "I"
    return p[:1] or "?"


def _calc_lag(trade_date: str, disclose_date: str) -> int:
    try:
        t = date.fromisoformat(trade_date[:10])
        d = date.fromisoformat(disclose_date[:10])
        return max(0, (d - t).days)
    except Exception:
        return 0


# ── Disk cache (survives restarts + stingy free-tier quotas) ────────────────────

def _save_disk_cache(trades: list) -> None:
    try:
        with open(DISK_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(),
                       "trades": trades}, fh)
    except Exception:
        pass


def _load_disk_cache() -> Optional[list]:
    try:
        with open(DISK_CACHE, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("trades")
    except Exception:
        return None


# ── Mock data (CONGRESS_MOCK=1) ────────────────────────────────────────────────

_MOCK_ASSETS = [
    ("NVDA", "NVIDIA Corp"), ("AAPL", "Apple Inc"), ("MSFT", "Microsoft Corp"),
    ("TSLA", "Tesla Inc"), ("AMZN", "Amazon.com Inc"), ("GOOGL", "Alphabet Inc"),
    ("META", "Meta Platforms Inc"), ("AMD", "Advanced Micro Devices"),
    ("JPM", "JPMorgan Chase & Co"), ("DIS", "Walt Disney Co"),
    ("NFLX", "Netflix Inc"), ("CRM", "Salesforce Inc"), ("PLTR", "Palantir Technologies"),
    ("COIN", "Coinbase Global"), ("XOM", "Exxon Mobil Corp"), ("BAC", "Bank of America"),
    ("WMT", "Walmart Inc"), ("KO", "Coca-Cola Co"), ("PFE", "Pfizer Inc"),
    ("ORCL", "Oracle Corp"), ("V", "Visa Inc"), ("HD", "Home Depot Inc"),
]
_MOCK_AMOUNTS = [
    (1001, 15000), (15001, 50000), (50001, 100000),
    (100001, 250000), (250001, 500000), (500001, 1000000),
]


def _mock_trades() -> list:
    """Deterministic, realistic demo trades built on REAL members (names, party,
    state, Bioguide photo) from the legislators dataset and real tickers — so the
    full UI (leaderboard, detail page, yfinance-backed equity curves) works
    without a live FMP key. Toggle via CONGRESS_MOCK=1."""
    cached = _cache_get(_TRADE_CACHE, "mock")
    if cached is not None:
        return cached

    import random
    rnd = random.Random(42)  # fixed seed → stable leaderboard across reloads

    idx = _legislators_index()
    flat = [(last, e) for last, entries in idx.items()
            for e in entries if e.get("bioguide_id")]
    flat.sort(key=lambda x: x[1]["bioguide_id"])  # stable order before shuffle
    rnd.shuffle(flat)

    # A spread across both chambers and parties.
    picked, seen_chambers = [], {"Senate": 0, "House": 0}
    for last, e in flat:
        if len(picked) >= 16:
            break
        picked.append((last, e))
        seen_chambers[e["chamber"]] = seen_chambers.get(e["chamber"], 0) + 1

    today = date.today()
    trades: list = []
    for last, e in picked:
        full = f"{last.title()}, {e['first'].title()}"
        mid = e["bioguide_id"]
        for _ in range(rnd.randint(5, 15)):
            ticker, asset = rnd.choice(_MOCK_ASSETS)
            ttype = "Purchase" if rnd.random() < 0.6 else "Sale"
            td = today - timedelta(days=rnd.randint(5, 175))
            dd = min(td + timedelta(days=rnd.randint(8, 44)), today)
            lo, hi = rnd.choice(_MOCK_AMOUNTS)
            trades.append({
                "id": _trade_id(mid, ticker, td.isoformat(), ttype) + str(rnd.randint(0, 9999)),
                "member": full, "member_id": mid, "bioguide_id": mid,
                "party": e["party"] or "?", "chamber": e["chamber"],
                "state": e["state"] or "—",
                "ticker": ticker, "asset_name": asset, "type": ttype,
                "amount_low": lo, "amount_high": hi,
                "trade_date": td.isoformat(), "disclosed_date": dd.isoformat(),
                "disclosure_lag_days": (dd - td).days,
            })

    trades.sort(key=lambda t: t["trade_date"], reverse=True)
    _cache_set(_TRADE_CACHE, "mock", trades, _TRADES_TTL)
    return trades


# ── Public API ─────────────────────────────────────────────────────────────────

def _fetch_all() -> list:
    """One live pull of the widest window we use (1 year), deduped + sorted.
    Persisted to disk on success; on provider failure, falls back to the last
    good disk snapshot rather than returning nothing."""
    if is_mock():
        return _mock_trades()

    cached = _cache_get(_TRADE_CACHE, "all")
    if cached is not None:
        return cached

    try:
        all_trades = _fetch_senate(365) + _fetch_house(365)
    except APIUnavailable:
        disk = _load_disk_cache()
        if disk:
            _cache_set(_TRADE_CACHE, "all", disk, _TRADES_TTL)
            return disk
        raise

    seen, unique = set(), []
    for t in all_trades:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    unique.sort(key=lambda t: t["trade_date"], reverse=True)

    if unique:
        _save_disk_cache(unique)
    else:
        # Empty live result (commonly a silently-throttled free tier that returns
        # 401/empty intermittently). Prefer the last good snapshot; if there's
        # none, signal unavailable so the UI shows guidance, not a blank table.
        disk = _load_disk_cache()
        if disk:
            unique = disk
        else:
            raise APIUnavailable(
                "No Congress trade data returned. The free FMP tier is likely "
                "throttling the key (it returns intermittent 401s). Confirm the key "
                "is active on your FMP dashboard, then retry in a minute."
            )

    _cache_set(_TRADE_CACHE, "all", unique, _TRADES_TTL)
    return unique


def fetch_trades(days: int = 180) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [t for t in _fetch_all() if (t.get("trade_date") or "") >= cutoff]


def member_list() -> list:
    """Members with trades in the past 180 days, each with summary stats
    (6-month return + win rate). Returns are computed against a single shared
    price cache — one yfinance fetch per unique ticker — so the leaderboard is
    actually rankable without an O(members × tickers) price storm."""
    cached = _cache_get(_PERF_CACHE, "leaderboard")
    if cached is not None:
        return cached

    trades = fetch_trades(days=180)
    members: dict = {}
    by_member: dict = defaultdict(list)
    for t in trades:
        mid = t["member_id"]
        by_member[mid].append(t)
        if mid not in members:
            members[mid] = {
                "member_id": mid,
                "member": t["member"],
                "party": t["party"],
                "chamber": t["chamber"],
                "state": t["state"],
                "photo_url": _photo_url(mid),
                "trade_count": 0,
                "return_6mo": None,
                "win_rate": None,
            }
        members[mid]["trade_count"] += 1

    # Shared price map: each unique ticker fetched once over a fixed 190-day window.
    today = date.today()
    window_start = today - timedelta(days=190)
    price_map = {
        tk: _get_prices(tk, window_start, today)
        for tk in {t["ticker"] for t in trades}
    }
    for mid, m in members.items():
        ret, win = _fast_perf(by_member[mid], price_map, today)
        m["return_6mo"] = ret
        m["win_rate"] = win

    result = list(members.values())
    # Rank by 6-month return (members without a computable return sink to the bottom).
    result.sort(key=lambda m: (m["return_6mo"] is not None, m["return_6mo"] or 0,
                               m["trade_count"]), reverse=True)
    _cache_set(_PERF_CACHE, "leaderboard", result, _PERF_TTL)
    return result


def _fast_perf(trades: list, price_map: dict, today: date) -> tuple:
    """Leaderboard 6-month return + win rate from a pre-fetched shared price map
    (no network). Return uses the SAME member-basis calc as the detail page's
    6-month return (trade dates, amount-weighted), so the two agree; win rate is
    a per-purchase tally."""
    purchases = [t for t in trades if t["type"] == "Purchase" and t["trade_date"]]
    if not purchases:
        return None, None

    ret = _member_return(trades, price_map, today)

    sales_map: dict = defaultdict(list)
    for t in trades:
        if t["type"] == "Sale" and t["trade_date"]:
            sd = _parse_date(t["disclosed_date"] or t["trade_date"])
            if sd:
                sales_map[t["ticker"]].append(sd)
    for k in sales_map:
        sales_map[k].sort()

    wins = losses = 0
    for p in purchases:
        bd = _parse_date(p["disclosed_date"] or p["trade_date"])
        prices = price_map.get(p["ticker"], {})
        if not bd or not prices:
            continue
        sell_date = today
        for sd in sales_map.get(p["ticker"], []):
            if sd > bd:
                sell_date = sd
                break
        bp = _nearest_price(prices, bd)
        sp = _price_asof(prices, sell_date)
        if not bp:
            continue
        sp = sp or bp
        if sp > bp:
            wins += 1
        elif sp < bp:
            losses += 1

    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) else None
    return ret, win_rate


def member_trades(member_id: str) -> list:
    # 180-day window so the detail page's "6-month" stats match the leaderboard.
    trades = fetch_trades(days=180)
    mt = [t for t in trades if t["member_id"] == member_id]
    if not mt:
        raise LookupError(f"No trades found for member {member_id!r}")
    return mt


def attach_trade_prices(trades: list) -> list:
    """Annotate each trade with ``price`` — the market close on the member's trade
    date (an estimate of the price they got; disclosures give dollar ranges, not
    actual fills). Mutates and returns the same list."""
    today = date.today()
    price_series, _ = _price_series_for(trades, today, "trade")
    for t in trades:
        d = _parse_date(t["trade_date"])
        series = price_series.get(t["ticker"], {})
        px = _price_asof(series, d) if d else None
        t["price"] = round(px, 2) if px else None
    return trades


def member_performance(member_id: str) -> dict:
    key = f"perf:{member_id}"
    cached = _cache_get(_PERF_CACHE, key)
    if cached is not None:
        return cached

    trades = member_trades(member_id)
    result = _compute_performance(member_id, trades)
    _cache_set(_PERF_CACHE, key, result, _PERF_TTL)
    return result


def simulate_follow(member_id: str, capital: float = 10_000.0,
                    sizing: str = "equal") -> dict:
    trades = member_trades(member_id)
    return _simulate(trades, capital, sizing=sizing)


# ── Performance computation ────────────────────────────────────────────────────

def _compute_performance(member_id: str, trades: list) -> dict:
    if not trades:
        return _empty_perf()

    member = trades[0]["member"]
    party = trades[0]["party"]
    chamber = trades[0]["chamber"]
    state = trades[0]["state"]

    # Only purchases count for return calculation (sales close positions)
    purchases = [t for t in trades if t["type"] == "Purchase" and t["trade_date"]]
    sales_raw  = [t for t in trades if t["type"] == "Sale" and t["trade_date"]]

    # Win rate (member's hit rate): for each purchase, was the stock worth more at
    # the member's sale (or today, if still held) than at their purchase? Uses the
    # member's TRADE dates so it's consistent with MEMBER RETURN.
    wins, losses = 0, 0
    pnl_pcts = []

    for buy in purchases:
        ticker = buy["ticker"]
        buy_date = _parse_date(buy["trade_date"]) or _parse_date(buy["disclosed_date"])
        if not buy_date:
            continue

        # Find the earliest matching sale for this ticker after the buy
        sell_date = date.today()
        for sale in sales_raw:
            if sale["ticker"] == ticker:
                sd = _parse_date(sale["trade_date"]) or _parse_date(sale["disclosed_date"])
                if sd and sd > buy_date:
                    sell_date = sd
                    break

        prices = _get_prices(ticker, buy_date, sell_date + timedelta(days=5))
        buy_price = _nearest_price(prices, buy_date)
        sell_price = _price_asof(prices, sell_date)
        if buy_price and sell_price and buy_price > 0:
            pnl = (sell_price - buy_price) / buy_price * 100
            pnl_pcts.append(pnl)
            if pnl > 0:
                wins += 1
            else:
                losses += 1

    total_trades = len(purchases) + len(sales_raw)
    win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None

    # MEMBER's own 6-month return: each trade entered on the member's trade date,
    # blended by the dollar size they disclosed. (Distinct from a follower's
    # equal-weight, disclosure-date simulation.)
    today = date.today()
    member_prices, _ = _price_series_for(trades, today, "trade")
    return_6mo = _member_return(trades, member_prices, today) if member_prices else None

    # SPY benchmark
    spy_return = _spy_return_6mo()

    # Most traded tickers
    buy_tickers = Counter(t["ticker"] for t in purchases)
    sell_tickers = Counter(t["ticker"] for t in sales_raw)
    most_bought = buy_tickers.most_common(1)[0] if buy_tickers else (None, 0)
    most_sold = sell_tickers.most_common(1)[0] if sell_tickers else (None, 0)

    # Top sectors
    top_sectors = _top_sectors([t["ticker"] for t in purchases[:10]])

    # Average hold days
    avg_hold = _avg_hold_days(purchases, sales_raw)

    result = {
        "member_id": member_id,
        "member": member,
        "party": party,
        "chamber": chamber,
        "state": state,
        "photo_url": _photo_url(member_id),
        "total_trades": total_trades,
        "purchase_count": len(purchases),
        "sale_count": len(sales_raw),
        "win_rate": win_rate,
        "return_6mo": return_6mo,
        "spy_return_6mo": spy_return,
        "outperformance": round(return_6mo - spy_return, 2) if (return_6mo is not None and spy_return is not None) else None,
        "most_bought_ticker": most_bought[0],
        "most_bought_count": most_bought[1],
        "most_sold_ticker": most_sold[0],
        "most_sold_count": most_sold[1],
        "top_sectors": top_sectors,
        "avg_hold_days": avg_hold,
    }
    return result


def _member_return(trades: list, price_series: dict, today: date) -> Optional[float]:
    """The member's own blended 6-month return: each purchase entered on the
    member's trade date and exited on the matching same-ticker sale's trade date
    (or marked to today if still open), weighted by the dollar size they disclosed
    (range midpoint). Not cash-constrained — it answers 'how did their picks do,
    weighted by how much they put in.'"""
    purchases = [t for t in trades if t["type"] == "Purchase" and t["trade_date"]]
    if not purchases:
        return None

    sales_map: dict = defaultdict(list)
    for t in trades:
        if t["type"] == "Sale" and t["trade_date"]:
            sd = _parse_date(t["trade_date"])
            if sd:
                sales_map[t["ticker"]].append(sd)
    for k in sales_map:
        sales_map[k].sort()

    num = den = 0.0
    for p in purchases:
        bd = _parse_date(p["trade_date"])
        prices = price_series.get(p["ticker"], {})
        if not bd or not prices:
            continue
        bp = _nearest_price(prices, bd)
        if not bp:
            continue
        sell_date = today
        for sd in sales_map.get(p["ticker"], []):
            if sd > bd:
                sell_date = sd
                break
        sp = _price_asof(prices, sell_date) or bp
        w = (p.get("amount_low", 0) + p.get("amount_high", 0)) / 2 or 1.0
        num += w * (sp / bp - 1.0)
        den += w
    if den == 0:
        return None
    return round(num / den * 100, 2)


def _follow_simulate(trades: list, capital: float, price_series: dict, today: date,
                     date_field: str = "disclosed", sizing: str = "equal",
                     with_curve: bool = True, with_positions: bool = False) -> dict:
    """
    Event-driven portfolio that mirrors a member's trades in chronological order.

    ``date_field`` — which date you act on:
      "disclosed" → the disclosure date (soonest a follower could act); used for
                    YOUR follow-simulation.
      "trade"     → the member's actual trade date; used to estimate THE MEMBER's
                    own return.
    ``sizing`` — how much each BUY gets:
      "equal"  → capital / number_of_buys (a follower spreading evenly).
      "amount" → proportional to the member's disclosed dollar size (their range
                 midpoint), so bigger member bets carry more weight.
    A BUY invests its slice at that day's price (funded from cash, never margin).
    A SELL liquidates the whole holding of that ticker back to cash. The portfolio
    (cash + live holdings marked to market) is valued on a weekly grid.
    """
    def action_date(t):
        if date_field == "trade":
            return _parse_date(t["trade_date"]) or _parse_date(t["disclosed_date"])
        return _parse_date(t["disclosed_date"] or t["trade_date"])

    events = []
    for t in trades:
        if t["type"] not in ("Purchase", "Sale"):
            continue
        d = action_date(t)
        if not d:
            continue
        mid = (t.get("amount_low", 0) + t.get("amount_high", 0)) / 2 or 1.0
        events.append({"date": d, "type": t["type"], "ticker": t["ticker"], "weight": mid})
    events.sort(key=lambda e: e["date"])

    buys = [e for e in events if e["type"] == "Purchase"]
    empty = {"equity_curve": [], "spy_curve": [], "positions": [],
             "total_return_pct": 0.0, "final_value": round(capital, 2),
             "position_size": None, "num_buys": 0}
    if not buys:
        return empty

    if sizing == "amount":
        tw = sum(e["weight"] for e in buys) or 1.0
        for e in buys:
            e["target"] = capital * e["weight"] / tw
        position_size = None
    else:
        per = capital / len(buys)
        for e in buys:
            e["target"] = per
        position_size = round(per, 2)

    # Anchor the window to a strict 6 months so SPY here equals the actual
    # 6-month SPY growth and the "6MO" labels line up. Your portfolio holds cash
    # from day one and only deploys as the member's trades disclose (so the curve
    # may sit flat at first); SPY is the same capital invested on day one.
    start_date = min(today - timedelta(days=180), events[0]["date"])
    grid = []
    d = start_date
    while d < today:
        grid.append(d)
        d += timedelta(days=7)
    grid.append(today)

    spy_prices = _get_prices("SPY", start_date, today) if with_curve else {}
    spy_base = _nearest_price(spy_prices, start_date) if with_curve else None

    cash = capital
    holdings: dict = {}
    lots: list = []          # per-buy records for the positions breakdown
    ei = 0
    equity_curve, spy_curve = [], []

    def do_event(e):
        nonlocal cash
        prices = price_series.get(e["ticker"], {})
        px = _nearest_price(prices, e["date"])
        if not px or px <= 0:
            return
        if e["type"] == "Purchase":
            spend = min(e["target"], cash)
            if spend > 0:
                holdings[e["ticker"]] = holdings.get(e["ticker"], 0.0) + spend / px
                cash -= spend
                lots.append({"ticker": e["ticker"], "buy_date": str(e["date"]),
                             "buy_price": round(px, 2), "invested": round(spend, 2),
                             "shares": spend / px, "open": True,
                             "exit_date": None, "exit_price": None})
        else:
            sh = holdings.get(e["ticker"], 0.0)
            if sh > 0:
                cash += sh * px
                holdings[e["ticker"]] = 0.0
                for lot in lots:
                    if lot["ticker"] == e["ticker"] and lot["open"]:
                        lot["open"] = False
                        lot["exit_date"] = str(e["date"])
                        lot["exit_price"] = round(px, 2)

    total = capital
    for gd in grid:
        while ei < len(events) and events[ei]["date"] <= gd:
            do_event(events[ei])
            ei += 1
        total = cash
        for tk, sh in holdings.items():
            if sh > 0:
                p = _price_asof(price_series.get(tk, {}), gd) or _nearest_price(price_series.get(tk, {}), gd)
                total += sh * p if p else 0.0
        if with_curve:
            equity_curve.append({"date": str(gd), "value": round(total, 2)})
            if spy_base:
                spx = _price_asof(spy_prices, gd) or spy_base
                spy_curve.append({"date": str(gd), "value": round(capital * spx / spy_base, 2)})
            else:
                spy_curve.append({"date": str(gd), "value": capital})

    positions = []
    if with_positions:
        for lot in lots:
            cur_px = _price_asof(price_series.get(lot["ticker"], {}), today) or lot["buy_price"]
            if lot["open"]:
                value_now = lot["shares"] * cur_px
                ret = (cur_px / lot["buy_price"] - 1) * 100 if lot["buy_price"] else 0.0
                status, exit_price = "HELD", None
            else:
                exit_price = lot["exit_price"] or lot["buy_price"]
                value_now = lot["shares"] * exit_price
                ret = (exit_price / lot["buy_price"] - 1) * 100 if lot["buy_price"] else 0.0
                status = "SOLD"
            positions.append({
                "ticker": lot["ticker"], "buy_date": lot["buy_date"],
                "buy_price": lot["buy_price"], "invested": lot["invested"],
                "status": status, "exit_date": lot["exit_date"], "exit_price": exit_price,
                "current_price": round(cur_px, 2),
                "value_now": round(value_now, 2), "return_pct": round(ret, 2),
            })
        positions.sort(key=lambda p: p["buy_date"], reverse=True)

    total_return_pct = round((total - capital) / capital * 100, 2) if capital > 0 else 0.0
    return {
        "equity_curve": equity_curve,
        "spy_curve": spy_curve,
        "positions": positions,
        "total_return_pct": total_return_pct,
        "final_value": round(total, 2),
        "position_size": position_size,
        "num_buys": len(buys),
    }


def _price_series_for(trades: list, today: date, date_field: str = "disclosed") -> tuple:
    """Fetch one cached price series per unique ticker over the trade window."""
    def adate(t):
        if date_field == "trade":
            return _parse_date(t["trade_date"]) or _parse_date(t["disclosed_date"])
        return _parse_date(t["disclosed_date"] or t["trade_date"])
    dates = [adate(t) for t in trades if t["type"] in ("Purchase", "Sale")]
    dates = [d for d in dates if d]
    if not dates:
        return {}, None
    start_date = min(dates)
    tickers = {t["ticker"] for t in trades if t["type"] in ("Purchase", "Sale")}
    return {tk: _get_prices(tk, start_date, today) for tk in tickers}, start_date


def _simulate(trades: list, capital: float, sizing: str = "equal") -> dict:
    """YOUR follow-simulation: entered on disclosure dates, with the weekly equity
    + SPY curves and a per-position breakdown (detail view). ``sizing`` is "equal"
    (capital split evenly per buy) or "amount" (mirror the member's disclosed size)."""
    today = date.today()
    price_series, start_date = _price_series_for(trades, today, "disclosed")
    if not price_series:
        return {"equity_curve": [], "spy_curve": [], "positions": [],
                "total_return_pct": 0.0, "final_value": round(capital, 2),
                "position_size": None, "num_buys": 0}
    return _follow_simulate(trades, capital, price_series, today,
                            date_field="disclosed",
                            sizing="amount" if sizing == "amount" else "equal",
                            with_curve=True, with_positions=True)


def _spy_return_6mo() -> Optional[float]:
    key = "spy:6mo"
    with _PRICE_LOCK:
        cached = _PRICE_CACHE.get(key)
    if cached and time.time() < cached[1]:
        return cached[0]
    try:
        start = date.today() - timedelta(days=182)
        prices = _get_prices("SPY", start, date.today())
        if not prices:
            return None
        sorted_prices = sorted(prices.items())
        first = sorted_prices[0][1]
        last = sorted_prices[-1][1]
        pct = round((last - first) / first * 100, 2)
        with _PRICE_LOCK:
            _PRICE_CACHE[key] = (pct, time.time() + 3600)
        return pct
    except Exception:
        return None


def _top_sectors(tickers: list) -> list:
    sectors = []
    for ticker in tickers[:8]:
        try:
            info = yf.Ticker(ticker).info or {}
            s = info.get("sector")
            if s and s not in sectors:
                sectors.append(s)
        except Exception:
            pass
        if len(sectors) >= 3:
            break
    return sectors


def _avg_hold_days(purchases: list, sales: list) -> Optional[int]:
    holds = []
    sales_by_ticker: dict = defaultdict(list)
    for s in sales:
        sd = _parse_date(s["disclosed_date"] or s["trade_date"])
        if sd:
            sales_by_ticker[s["ticker"]].append(sd)
    for ticker in sales_by_ticker:
        sales_by_ticker[ticker].sort()

    for buy in purchases:
        bd = _parse_date(buy["disclosed_date"] or buy["trade_date"])
        if not bd:
            continue
        for sd in sales_by_ticker.get(buy["ticker"], []):
            if sd > bd:
                holds.append((sd - bd).days)
                break

    return round(sum(holds) / len(holds)) if holds else None


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _photo_url(member_id: str) -> str:
    """Real Bioguide headshot when member_id is a Bioguide ID, else '' (JS falls
    back to a letter avatar)."""
    if member_id and re.match(r"^[A-Z]\d{6}$", member_id):
        return PHOTO_CDN.format(bioguide_id=member_id)
    return ""


def _empty_perf() -> dict:
    return {
        "total_trades": 0, "purchase_count": 0, "sale_count": 0,
        "win_rate": None, "return_6mo": None, "spy_return_6mo": None,
        "outperformance": None, "most_bought_ticker": None,
        "most_sold_ticker": None, "top_sectors": [], "avg_hold_days": None,
    }
