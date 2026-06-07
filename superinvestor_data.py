"""
Superinvestor 13F trade data
============================
Tracks the trades of legendary institutional investors ("superinvestors" —
Buffett, Burry, Pabrai, Ackman, Klarman, Icahn, …) reconstructed from their SEC
Form 13F filings, so the COPY tab can offer a second, real, named source
alongside Congress.

Data is sourced through Dataroma (https://www.dataroma.com), which already solves
the two hard parts of raw 13F data:
  • 13F identifies holdings by CUSIP, not ticker — Dataroma maps them to tickers.
  • 13F is a quarterly *holdings snapshot*, not a transaction feed — Dataroma's
    activity feed diffs quarter-over-quarter into Buy / Add x% / Reduce x% / Sell
    rows, grouped by quarter. We turn each such row into one normalized trade.

The provider boundary mirrors ``congress_data`` on purpose: this module produces
the SAME normalized trade dicts, then delegates all return / win-rate /
follow-simulation / equity-curve math to the generic engine already living in
``congress_data`` (``_compute_performance``, ``_simulate``, ``_fast_perf``,
``attach_trade_prices``, ``_get_prices``). Nothing in ``congress_data`` changes.

Caveats (surfaced in the UI): 13F is long-only (no shorts / cash / option
detail), quarterly granularity (coarser equity curve than Congress) and lagged
~45 days — so this is educational / backtest, not real-time copyable. A "Reduce"
is a partial sale but is modeled as a position close, like Congress sales.

Future self-hosted path (the sellable differentiator): replace the Dataroma
scrape with a direct SEC EDGAR 13F reader (pull each ``<infoTable>``, map CUSIP→
ticker via OpenFIGI, diff consecutive quarters) — drop-in behind ``_fetch_manager``
without touching the engine or UI. Same "easy source now, self-host later" shape
as Congress's FMP → scraper plan.

Public API (matches congress_data):
    member_list()                       → managers with summary performance stats
    member_trades(member_id)            → trades for one manager (180-day window)
    member_performance(member_id)       → detailed performance stats
    simulate_follow(member_id, capital) → equity curve + return
    attach_trade_prices(trades)         → annotate trades with trade-date price
    is_mock()                           → True when SUPERINVESTOR_MOCK is set

Config:
    SUPERINVESTOR_MOCK=1  — serve deterministic demo data (offline UI preview)
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

# Reuse the generic price / performance / simulation engine. These functions
# operate on plain trade dicts and assume nothing Congress-specific, so we feed
# them our 13F-derived trades unchanged.
import congress_data
from congress_data import APIUnavailable  # reused → server maps it to a 503 card


# ── Manager roster ──────────────────────────────────────────────────────────────
# The leaderboard roster is scraped LIVE from Dataroma's home page (~80 managers) —
# see `_scrape_manager_list` / `manager_list`. This hardcoded dict is only a
# FALLBACK, used if that scrape fails and no disk snapshot exists. Keep it to a few
# reliable marquee names.
_FALLBACK_MANAGERS = {
    "BRK":     "Warren Buffett",
    "SAM":     "Michael Burry",
    "PI":      "Mohnish Pabrai",
    "psc":     "Bill Ackman",
    "BAUPOST": "Seth Klarman",
    "ic":      "Carl Icahn",
    "GLRE":    "David Einhorn",
    "HC":      "Li Lu",
    "AM":      "David Tepper",
    "oc":      "Howard Marks",
    "tp":      "Daniel Loeb",
    "TGM":     "Chase Coleman",
    "FS":      "Terry Smith",
    "MKL":     "Tom Gayner",
}

DATAROMA_BASE = "https://www.dataroma.com/m"
DISK_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "superinvestor_cache.json")
# 13F is quarterly and lagged ~45 days, and some managers file sparsely (Burry's
# Scion can sit a quarter out in cash). A Congress-style 180-day window would drop
# marquee names entirely, so we look back ~1 year (≈4 quarters) — enough to always
# show a manager's recent moves. The COPY UI labels this "RETURN" (not "6MO").
# Lower this if you want faster first-load (fewer unique tickers to price).
LOOKBACK_DAYS = 365

# ── Caches ──────────────────────────────────────────────────────────────────────
_MGR_CACHE: dict = {}     # code → (meta dict, expires_ts)
_TRADE_CACHE: dict = {}   # code → (trades list, expires_ts)
_LB_CACHE: dict = {}      # "leaderboard" → (list, expires_ts)
_LIST_CACHE: dict = {}    # "roster" → ({code: name}, expires_ts)
_CACHE_LOCK = threading.Lock()
_DISK_LOCK = threading.Lock()
# 13F is quarterly: filings land in waves ~45 days after each quarter end
# (mid-Feb / May / Aug / Nov), and the data is static for weeks between them — so
# there's no reason to re-scrape often. A 7-day TTL means the expensive full scrape
# runs at most weekly (realistically only turning up new data around filing season),
# and the disk leaderboard cache makes restarts within that window instant.
_TRADES_TTL = 604800      # 7 days (per-manager trades/meta, in-memory)
_LB_TTL = 604800          # 7 days (in-memory + disk leaderboard freshness window)
_LIST_TTL = 604800        # 7 days (roster — changes only when Dataroma adds/drops a manager)

# ── HTTP session (browser UA — Dataroma rejects bare bot agents) ────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

# Quarter → (month, day) of quarter end. 13F is filed within 45 days of quarter end.
_Q_END = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31)}


# ── Parsing patterns ────────────────────────────────────────────────────────────
# Manager display name, e.g. <p id="f_name">Warren Buffett - Berkshire Hathaway</p>
_NAME_RE = re.compile(r'_name"[^>]*>\s*([^<]+?)\s*<')
# Holdings-page metadata lines.
_PVALUE_RE = re.compile(r'Portfolio value:\s*<span>\$([\d,]+)</span>')
_PERIOD_RE = re.compile(r'Period:\s*<span>\s*([^<]+?)\s*</span>')
_PDATE_RE = re.compile(r'Portfolio date:\s*<span>\s*([^<]+?)\s*</span>')
# Quarter header rows on the activity feed: <tr class="q_chg">…<b>Q1</b> &nbsp<b>2026</b>
_QHDR_RE = re.compile(
    r'<tr class="q_chg">.*?<b>\s*(Q[1-4])\s*</b>\s*(?:&nbsp;?)?\s*<b>\s*(\d{4})\s*</b>',
    re.S)
# One activity row (rows are NOT wrapped in <tr>; they start at the stock cell).
# Captures: ticker, asset name, direction (buy/sell), activity text, share change, % change.
_ROW_RE = re.compile(
    r'<td class="stock">\s*<a[^>]*>\s*([A-Za-z0-9.\-]+)<span>\s*-\s*([^<]*?)\s*</span>\s*</a>\s*</td>\s*'
    r'<td class="(buy|sell)">\s*([^<]*?)\s*</td>\s*'
    r'<td class="(?:buy|sell)">\s*([\d,]+)\s*</td>\s*'
    r'<td[^>]*>\s*([^<]*?)\s*</td>',
    re.S)
# Roster links on home.php: <a href="/m/holdings.php?m=CODE">Name - Firm<span ...>…
_MGR_LINK_RE = re.compile(r'<a href="/m/holdings\.php\?m=([^"&]+)"[^>]*>(.*?)</a>', re.S)


# ── Small utilities ─────────────────────────────────────────────────────────────

def is_mock() -> bool:
    return os.getenv("SUPERINVESTOR_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def _cache_get(store: dict, key: str):
    with _CACHE_LOCK:
        entry = store.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(store: dict, key: str, value, ttl: int):
    with _CACHE_LOCK:
        store[key] = (value, time.time() + ttl)


def _trade_id(code: str, ticker: str, period: str, ttype: str) -> str:
    return hashlib.md5(f"{code}:{ticker}:{period}:{ttype}".encode()).hexdigest()[:12]


def _quarter_dates(quarter: str, year: str) -> tuple:
    mo, day = _Q_END[quarter]
    td = date(int(year), mo, day)
    return td, td + timedelta(days=45)


def _get(url: str) -> str:
    """GET a Dataroma page, politely. Raises on any non-200 so callers can fall
    back to the disk snapshot."""
    resp = _SESSION.get(url, timeout=20)
    resp.raise_for_status()
    time.sleep(0.4)  # be gentle — ~28 page loads on a cold leaderboard
    return resp.text


# ── Disk snapshot (survives restarts + transient Dataroma outages) ──────────────

def _disk_all() -> dict:
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                return (json.load(fh) or {}).get("managers", {})
        except Exception:
            return {}


def _disk_get(code: str) -> Optional[dict]:
    return _disk_all().get(code)


def _disk_put(code: str, meta: dict, trades: list) -> None:
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                snap = json.load(fh) or {}
        except Exception:
            snap = {}
        snap.setdefault("managers", {})[code] = {"meta": meta, "trades": trades}
        snap["fetched_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(DISK_CACHE, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
        except Exception:
            pass


def _disk_get_roster() -> Optional[dict]:
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                return (json.load(fh) or {}).get("roster")
        except Exception:
            return None


def _disk_put_roster(roster: dict) -> None:
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                snap = json.load(fh) or {}
        except Exception:
            snap = {}
        snap["roster"] = roster
        snap["fetched_at"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(DISK_CACHE, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
        except Exception:
            pass


def _disk_get_leaderboard() -> Optional[list]:
    """The last computed leaderboard, if still fresh (< _LB_TTL). Lets a server
    restart serve instantly instead of re-scraping ~80 managers + re-pricing every
    ticker (~6 min)."""
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                lb = (json.load(fh) or {}).get("leaderboard")
        except Exception:
            return None
    if not lb:
        return None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(lb["at"])).total_seconds()
        if age < _LB_TTL:
            return lb.get("data")
    except Exception:
        pass
    return None


def _disk_put_leaderboard(data: list) -> None:
    with _DISK_LOCK:
        try:
            with open(DISK_CACHE, "r", encoding="utf-8") as fh:
                snap = json.load(fh) or {}
        except Exception:
            snap = {}
        snap["leaderboard"] = {"at": datetime.now(timezone.utc).isoformat(), "data": data}
        try:
            with open(DISK_CACHE, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
        except Exception:
            pass


# ── Manager roster (scraped live from Dataroma's home page) ─────────────────────

def _scrape_manager_list() -> dict:
    """{code: display name} for every superinvestor on Dataroma's home page (~80)."""
    html = _get(f"{DATAROMA_BASE}/home.php")
    roster: dict = {}
    for code, label in _MGR_LINK_RE.findall(html):
        code = code.strip()
        if not code or code in roster:
            continue
        # Name is the text before the "Updated …" <span>; keep the person part.
        name = label.split("<", 1)[0].replace("&amp;", "&").strip()
        roster[code] = name.partition(" - ")[0].strip() or code
    return roster


def manager_list() -> dict:
    """Full roster {code: name}. Live from Dataroma (cached 24h), falling back to the
    last disk snapshot, then the small hardcoded seed. In mock mode, the mock set."""
    if is_mock():
        return {c: v[0] for c, v in _MOCK_MANAGERS.items()}
    cached = _cache_get(_LIST_CACHE, "roster")
    if cached is not None:
        return cached
    try:
        roster = _scrape_manager_list()
    except Exception:
        roster = {}
    if roster:
        _disk_put_roster(roster)
    else:
        roster = _disk_get_roster() or dict(_FALLBACK_MANAGERS)
    _cache_set(_LIST_CACHE, "roster", roster, _LIST_TTL)
    return roster


# ── Scrape + normalize ──────────────────────────────────────────────────────────

def _fetch_holdings_meta(code: str) -> dict:
    html = _get(f"{DATAROMA_BASE}/holdings.php?m={code}")
    name_m = _NAME_RE.search(html)
    full = name_m.group(1).strip() if name_m else _FALLBACK_MANAGERS.get(code, code)
    person, _, firm = full.partition(" - ")
    pv = _PVALUE_RE.search(html)
    return {
        "name": person.strip() or full,
        "firm": firm.strip(),
        "portfolio_value": int(pv.group(1).replace(",", "")) if pv else 0,
        "period": (_PERIOD_RE.search(html).group(1) if _PERIOD_RE.search(html) else ""),
        "portfolio_date": (_PDATE_RE.search(html).group(1) if _PDATE_RE.search(html) else ""),
    }


def _parse_activity_rows(html: str) -> list:
    """Return raw row tuples in document order, each tagged with its quarter:
    (quarter, year, ticker, asset, direction, activity_text, share_change, pct)."""
    start = html.find('id="grid"')
    body = html[start:] if start >= 0 else html
    # Split into per-quarter sections; re.split keeps the captured (quarter, year).
    parts = _QHDR_RE.split(body)
    rows = []
    for k in range(1, len(parts) - 2, 3):
        quarter, year, section = parts[k], parts[k + 1], parts[k + 2]
        for m in _ROW_RE.finditer(section):
            ticker, asset, direction, act, share_chg, pct = m.groups()
            rows.append((quarter, year, ticker, asset, direction, act, share_chg, pct))
    return rows


def _normalize_rows(code: str, meta: dict, rows: list) -> list:
    pv = meta.get("portfolio_value", 0)
    person = meta.get("name") or _FALLBACK_MANAGERS.get(code, code)
    trades = []
    for quarter, year, ticker, asset, direction, act, share_chg, pct in rows:
        # Dataroma writes share classes with a dot (BRK.B, LEN.B, HEI.A); Yahoo/
        # yfinance expects a hyphen (BRK-B). 13F holds only US equities, so this
        # mapping is safe and lets the engine price class shares too.
        ticker = ticker.strip().upper().replace(".", "-")
        if not ticker:
            continue
        ttype = "Purchase" if direction == "buy" else "Sale"
        td, dd = _quarter_dates(quarter, year)
        try:
            pct_f = abs(float(pct.strip() or 0))
        except ValueError:
            pct_f = 0.0
        amount = int(round(pct_f / 100.0 * pv)) if pv else 0
        period = f"{quarter} {year}"
        trades.append({
            "id": _trade_id(code, ticker, period, ttype),
            "member": person,
            "member_id": code,
            # placeholders so the generic engine (which reads these) is happy;
            # the COPY UI hides party/chamber/state for this source.
            "party": "",
            "chamber": "13F",
            "state": "",
            "ticker": ticker,
            "asset_name": asset.strip() or ticker,
            "type": ttype,
            "amount_low": amount,
            "amount_high": amount,
            "trade_date": td.isoformat(),
            "disclosed_date": dd.isoformat(),
            "disclosure_lag_days": (dd - td).days,
            # extra fields for display (Congress shows party here instead):
            "period": period,
            "activity": act.strip(),
            "share_change": share_chg.replace(",", ""),
        })
    trades.sort(key=lambda t: t["trade_date"], reverse=True)
    return trades


def _fetch_manager(code: str) -> tuple:
    """(meta, trades) for one manager — live with disk-snapshot fallback."""
    try:
        meta = _fetch_holdings_meta(code)
        rows = _parse_activity_rows(_get(f"{DATAROMA_BASE}/m_activity.php?m={code}&typ=a"))
        trades = _normalize_rows(code, meta, rows)
        if not trades:
            raise APIUnavailable(f"No 13F activity parsed for {code}")
        _disk_put(code, meta, trades)
        return meta, trades
    except Exception as e:
        cached = _disk_get(code)
        if cached:
            return cached["meta"], cached["trades"]
        if isinstance(e, APIUnavailable):
            raise
        raise APIUnavailable(f"Dataroma fetch failed for {code}: {e}")


def _trades_for(code: str) -> list:
    """All cached/fetched trades for a manager (full history, unfiltered)."""
    if is_mock():
        return [t for t in _mock_trades() if t["member_id"] == code]
    cached = _cache_get(_TRADE_CACHE, code)
    if cached is not None:
        return cached
    meta, trades = _fetch_manager(code)
    _cache_set(_MGR_CACHE, code, meta, _TRADES_TTL)
    _cache_set(_TRADE_CACHE, code, trades, _TRADES_TTL)
    return trades


def _meta_for(code: str) -> dict:
    cached = _cache_get(_MGR_CACHE, code)
    if cached is not None:
        return cached
    _trades_for(code)  # populates _MGR_CACHE as a side effect
    return _cache_get(_MGR_CACHE, code) or {}


# ── Public API ──────────────────────────────────────────────────────────────────

def member_list() -> list:
    """Every manager on Dataroma's roster (~80) with trades in the lookback window,
    each with summary stats (return + win rate), ranked by return. Reuses the
    Congress engine's shared-price-map approach: one yfinance fetch per unique
    ticker. NOTE: a cold run scrapes ~80 managers (2 pages each) and prices every
    unique ticker, so the first load takes a few minutes — cached 6h afterward, and
    each manager's trades persist to disk so retries are cheaper."""
    cached = _cache_get(_LB_CACHE, "leaderboard")
    if cached is not None:
        return cached
    if not is_mock():
        disk = _disk_get_leaderboard()
        if disk:
            _cache_set(_LB_CACHE, "leaderboard", disk, _LB_TTL)
            return disk

    today = date.today()
    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    roster = manager_list()
    window: dict = {}  # code → in-window trades
    for code in roster:
        try:
            trades = _trades_for(code)
        except APIUnavailable:
            continue  # one manager failing shouldn't blank the whole board
        wt = [t for t in trades if t["trade_date"] >= cutoff]
        if wt:
            window[code] = wt

    if not window:
        raise APIUnavailable(
            "Could not load any superinvestor data right now. Dataroma may be "
            "rate-limiting or temporarily unreachable — retry in a minute.")

    # Shared price map: each unique ticker fetched once over a fixed window.
    win_start = today - timedelta(days=LOOKBACK_DAYS + 10)
    tickers = {t["ticker"] for wt in window.values() for t in wt}
    price_map = {tk: congress_data._get_prices(tk, win_start, today) for tk in tickers}

    members = []
    for code, wt in window.items():
        meta = _meta_for(code)
        ret, win = congress_data._fast_perf(wt, price_map, today)
        members.append({
            "member_id": code,
            "member": meta.get("name") or roster.get(code, code),
            "firm": meta.get("firm", ""),
            "portfolio_value": meta.get("portfolio_value", 0),
            "period": meta.get("period", ""),
            "photo_url": "",            # → JS letter-avatar fallback
            "party": "", "chamber": "13F", "state": "",
            "trade_count": len(wt),
            "return_6mo": ret,
            "win_rate": win,
        })

    members.sort(key=lambda m: (m["return_6mo"] is not None, m["return_6mo"] or 0,
                                m["trade_count"]), reverse=True)
    _cache_set(_LB_CACHE, "leaderboard", members, _LB_TTL)
    if not is_mock():
        _disk_put_leaderboard(members)
    return members


def member_trades(member_id: str) -> list:
    if not is_mock() and member_id not in manager_list():
        raise LookupError(f"Unknown manager {member_id!r}")
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    trades = [t for t in _trades_for(member_id) if t["trade_date"] >= cutoff]
    if not trades:
        raise LookupError(f"No trades found for manager {member_id!r}")
    return trades


def attach_trade_prices(trades: list) -> list:
    return congress_data.attach_trade_prices(trades)


def member_performance(member_id: str) -> dict:
    trades = member_trades(member_id)
    perf = dict(congress_data._compute_performance(member_id, trades))
    meta = _meta_for(member_id)
    # Enrich with fund-specific headline fields the COPY detail view shows in
    # place of Congress's party/chamber/state.
    perf["firm"] = meta.get("firm", "")
    perf["period"] = meta.get("period", "")
    perf["portfolio_value"] = meta.get("portfolio_value", 0)
    return perf


def simulate_follow(member_id: str, capital: float = 10_000.0,
                    sizing: str = "equal") -> dict:
    return congress_data._simulate(member_trades(member_id), capital, sizing=sizing)


# ── Mock data (SUPERINVESTOR_MOCK=1) ────────────────────────────────────────────

_MOCK_MANAGERS = {
    "BRK":  ("Warren Buffett", "Berkshire Hathaway", 263_000_000_000),
    "SAM":  ("Michael Burry", "Scion Asset Management", 75_000_000),
    "psc":  ("Bill Ackman", "Pershing Square", 12_000_000_000),
    "PI":   ("Mohnish Pabrai", "Pabrai Investments", 600_000_000),
}
_MOCK_ASSETS = [
    ("AAPL", "Apple Inc."), ("BAC", "Bank of America Corp."), ("KO", "Coca-Cola Co."),
    ("GOOGL", "Alphabet Inc."), ("AXP", "American Express Co."), ("CVX", "Chevron Corp."),
    ("OXY", "Occidental Petroleum"), ("CB", "Chubb Ltd."), ("AMZN", "Amazon.com Inc."),
    ("V", "Visa Inc."), ("NVDA", "NVIDIA Corp."), ("UBER", "Uber Technologies"),
]


def _mock_trades() -> list:
    cached = _cache_get(_TRADE_CACHE, "__mock__")
    if cached is not None:
        return cached
    import random
    rnd = random.Random(7)
    today = date.today()
    # Two most recent completed quarters relative to today.
    def prev_quarter_end(d: date, back: int) -> tuple:
        q = (d.month - 1) // 3  # 0..3 current quarter index
        # walk back 'back' quarters from the last completed quarter
        idx = q - 1 - back
        year = d.year
        while idx < 0:
            idx += 4
            year -= 1
        quarter = f"Q{idx + 1}"
        return quarter, str(year)

    trades = []
    for code, (person, firm, pv) in _MOCK_MANAGERS.items():
        latest_period = ""
        for back in (0, 1):
            quarter, year = prev_quarter_end(today, back)
            if back == 0:
                latest_period = f"{quarter} {year}"
            td, dd = _quarter_dates(quarter, year)
            if dd > today:
                dd = today
            picks = rnd.sample(_MOCK_ASSETS, rnd.randint(4, 7))
            for ticker, asset in picks:
                ttype = "Purchase" if rnd.random() < 0.65 else "Sale"
                pct = round(rnd.uniform(0.2, 6.0), 2)
                amount = int(pct / 100.0 * pv)
                period = f"{quarter} {year}"
                trades.append({
                    "id": _trade_id(code, ticker, period, ttype) + str(rnd.randint(0, 999)),
                    "member": person, "member_id": code,
                    "party": "", "chamber": "13F", "state": "",
                    "ticker": ticker, "asset_name": asset, "type": ttype,
                    "amount_low": amount, "amount_high": amount,
                    "trade_date": td.isoformat(), "disclosed_date": dd.isoformat(),
                    "disclosure_lag_days": (dd - td).days,
                    "period": period,
                    "activity": ("Add" if ttype == "Purchase" else "Reduce") + f" {pct}%",
                    "share_change": str(rnd.randint(10_000, 5_000_000)),
                })
        # cache meta for the detail/leaderboard views
        _cache_set(_MGR_CACHE, code, {
            "name": person, "firm": firm, "portfolio_value": pv,
            "period": latest_period,
        }, _TRADES_TTL)
    trades.sort(key=lambda t: t["trade_date"], reverse=True)
    _cache_set(_TRADE_CACHE, "__mock__", trades, _TRADES_TTL)
    return trades
