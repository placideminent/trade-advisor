"""종목 펀더멘털 요약. 기술적 점수와는 섞지 않고 표시·경고만 한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import requests

from .data import CACHE_DIR, _kr_yahoo_symbols

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://m.stock.naver.com/",
}

BUY_ACTIONS = ("약한 매수", "매수", "강한 매수")
SELL_ACTIONS = ("약한 매도", "매도", "강한 매도")


def _parse_num(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        px = float(val)
        if px != px:  # NaN
            return None
        return px
    text = str(val)
    text = text.replace(",", "").replace(" ", "")
    for token in ("배", "원", "%", "억", "조"):
        text = text.replace(token, "")
    text = text.strip()
    if text in ("", "-", "N/A", "None", "nan"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _infos_map(rows: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        if code:
            out[code] = row
    return out


def _info_num(infos: dict, code: str) -> float | None:
    row = infos.get(code) or {}
    return _parse_num(row.get("value"))


def _info_text(infos: dict, code: str) -> str:
    row = infos.get(code) or {}
    val = row.get("value")
    return "" if val is None else str(val)


def _info_desc(infos: dict, code: str) -> str:
    row = infos.get(code) or {}
    return str(row.get("valueDesc") or row.get("keyDesc") or "")


@dataclass
class Fundamentals:
    market: str
    ticker: str
    name: str = ""
    source: str = ""
    per: float | None = None
    per_asof: str = ""
    forward_per: float | None = None
    eps: float | None = None
    forward_eps: float | None = None
    pbr: float | None = None
    bps: float | None = None
    market_cap: str = ""
    dividend_yield: float | None = None
    dividend: str = ""
    foreign_rate: float | None = None
    high_52w: str = ""
    low_52w: str = ""
    industry_per: float | None = None
    industry_name: str = ""
    summary: str = ""
    sales_margin: float | None = None
    sales_profit_yoy: float | None = None
    sales_margin_asof: str = ""
    op_margin: float | None = None
    op_yoy: float | None = None
    quarters: list[dict] = field(default_factory=list)
    op_down_streak: int = 0
    warnings: list[str] = field(default_factory=list)
    value_label: str = "판단 보류"
    value_reasons: list[str] = field(default_factory=list)
    per_vs_industry: float | None = None
    error: str | None = None

    @property
    def per_gap(self) -> float | None:
        if self.per is None or self.forward_per is None:
            return None
        return self.forward_per - self.per

    @property
    def per_gap_pct(self) -> float | None:
        if self.per is None or self.forward_per is None or self.per == 0:
            return None
        return (self.forward_per / self.per - 1.0) * 100.0


def _norm_period(key: str) -> str:
    digits = "".join(c for c in str(key) if c.isdigit())
    return digits[:6] if len(digits) >= 6 else str(key)


def _yoy_key(key: str, all_keys: list | None = None) -> str | None:
    key = str(key or "")
    keys = [str(k) for k in (all_keys or [])]
    if len(key) >= 7 and key[4] == "." and key[:4].isdigit():
        prefix = f"{int(key[:4]) - 1}{key[4:7]}"
        for k in keys:
            if k.startswith(prefix):
                return k
        return None
    n = _norm_period(key)
    if len(n) == 6 and n.isdigit():
        prevn = f"{int(n[:4]) - 1}{n[4:]}"
        for k in keys:
            if _norm_period(k) == prevn:
                return k
        return prevn
    return None


def _gmap_get(gmap: dict | None, key: str) -> dict | None:
    if not gmap:
        return None
    if key in gmap:
        return gmap[key]
    n = _norm_period(key)
    if n in gmap:
        return gmap[n]
    for k, v in gmap.items():
        if _norm_period(k) == n:
            return v
    return None


def _yoy_pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or abs(prev) < 1e-12:
        return None
    return (cur / prev - 1.0) * 100.0


def _pp_chg(cur: float | None, prev: float | None) -> float | None:
    """이익률처럼 이미 %인 값의 전년 동기 대비 퍼센트포인트."""
    if cur is None or prev is None:
        return None
    return cur - prev


_ROW_ALIASES = {
    "영업이익": ("영업이익", "EBIT"),
    "당기순이익": ("당기순이익",),
    "매출액": ("매출액",),
    "영업이익률": ("영업이익률",),
    "ROE": ("ROE",),
    "부채비율": ("부채비율",),
}


def _col_num(rows_by_title: dict, title: str, key: str) -> float | None:
    if not key:
        return None
    for name in _ROW_ALIASES.get(title, (title,)):
        cell = (rows_by_title.get(name) or {}).get(key) or {}
        if isinstance(cell, dict) and cell.get("value") is not None:
            return _parse_num(cell.get("value"))
    return None


def _col_text(rows_by_title: dict, title: str, key: str) -> str | None:
    if not key:
        return None
    for name in _ROW_ALIASES.get(title, (title,)):
        cell = (rows_by_title.get(name) or {}).get(key) or {}
        if isinstance(cell, dict) and cell.get("value") is not None:
            return str(cell.get("value"))
    return None


def _fmt_yoy(val: float | None) -> str | None:
    if val is None:
        return None
    return f"{val:+.1f}%"


def _fmt_pp(val: float | None) -> str | None:
    if val is None:
        return None
    return f"{val:+.1f}%p"


def _period_from_ts(ts) -> str | None:
    try:
        t = pd.Timestamp(ts)
    except Exception:
        return None
    if t.month not in (3, 6, 9, 12):
        return f"{t.year}{t.month:02d}"
    return f"{t.year}{t.month:02d}"


def _yahoo_gross_map(symbol: str) -> dict[str, dict]:
    """분기별 매출총이익(Gross Profit). key=YYYYMM."""
    import yfinance as yf

    out: dict[str, dict] = {}
    try:
        q = yf.Ticker(symbol).quarterly_financials
    except Exception:
        return out
    if q is None or getattr(q, "empty", True):
        return out
    idx = {str(i): i for i in q.index}

    def _row(name: str):
        for key, raw in idx.items():
            if key.lower() == name.lower():
                return q.loc[raw]
        return None

    gross = _row("Gross Profit")
    rev = _row("Total Revenue")
    if gross is None or rev is None:
        return out
    for col in q.columns:
        key = _period_from_ts(col)
        if not key:
            continue
        g = _parse_num(gross.get(col))
        r = _parse_num(rev.get(col))
        if g is None or r is None or abs(r) < 1e-12:
            continue
        out[key] = {
            "gross": g,
            "revenue": r,
            "margin": g / r * 100.0,
        }
    return out


def _finance_table(payload: dict, gross_map: dict | None = None, limit: int = 4) -> list[dict]:
    info = payload.get("financeInfo") or {}
    titles = info.get("trTitleList") or []
    actual = [t for t in titles if isinstance(t, dict) and str(t.get("isConsensus") or "N") != "Y"]
    actual.sort(key=lambda t: str(t.get("key") or ""))
    if not actual:
        return []
    show = actual[-limit:]
    rows_by_title = {
        str(r.get("title") or ""): r.get("columns") or {}
        for r in (info.get("rowList") or [])
        if isinstance(r, dict)
    }
    gross_map = gross_map or {}
    out = []
    all_keys = [str(t.get("key") or "") for t in actual]
    for col in show:
        key = str(col.get("key") or "")
        prev = _yoy_key(key, all_keys)
        opm = _col_num(rows_by_title, "영업이익률", key)
        prev_opm = _col_num(rows_by_title, "영업이익률", prev) if prev else None
        if opm is None:
            sales = _col_num(rows_by_title, "매출액", key)
            op = _col_num(rows_by_title, "영업이익", key)
            if sales and abs(sales) > 1e-12 and op is not None:
                opm = op / sales * 100.0
        if prev_opm is None and prev:
            ps = _col_num(rows_by_title, "매출액", prev)
            po = _col_num(rows_by_title, "영업이익", prev)
            if ps and abs(ps) > 1e-12 and po is not None:
                prev_opm = po / ps * 100.0
        gnow = _gmap_get(gross_map, key) or {}
        gprev = _gmap_get(gross_map, prev) if prev else None
        gm = gnow.get("margin")
        gm_prev = (gprev or {}).get("margin") if isinstance(gprev, dict) else None
        item = {
            "기간": str(col.get("title") or key).rstrip("."),
            "매출액": _col_text(rows_by_title, "매출액", key),
            "영업이익": _col_text(rows_by_title, "영업이익", key),
            "당기순이익": _col_text(rows_by_title, "당기순이익", key),
            "매출이익률": f"{gm:.2f}" if gm is not None else None,
            "매출이익률증감": _fmt_pp(_pp_chg(gm, gm_prev)),
            "영업이익률": f"{opm:.2f}" if opm is not None else None,
            "영업이익률증감": _fmt_pp(_pp_chg(opm, prev_opm)),
            "ROE": _col_text(rows_by_title, "ROE", key),
            "부채비율": _col_text(rows_by_title, "부채비율", key),
        }
        out.append(item)
    return out


def _latest_margins(
    payload: dict, gross_map: dict | None = None
) -> tuple[float | None, float | None, str, float | None, float | None]:
    """최근 분기 매출이익률·증가율(매출총이익), 영업이익률·증가율."""
    info = payload.get("financeInfo") or {}
    titles = [
        t
        for t in (info.get("trTitleList") or [])
        if isinstance(t, dict) and str(t.get("isConsensus") or "N") != "Y"
    ]
    titles.sort(key=lambda t: str(t.get("key") or ""))
    if not titles:
        return None, None, "", None, None
    rows_by_title = {
        str(r.get("title") or ""): r.get("columns") or {}
        for r in (info.get("rowList") or [])
        if isinstance(r, dict)
    }
    all_keys = [str(t.get("key") or "") for t in titles]
    op_key = str(titles[-1].get("key") or "")
    opm = _col_num(rows_by_title, "영업이익률", op_key)
    prev_opm = _col_num(rows_by_title, "영업이익률", _yoy_key(op_key, all_keys) or "")
    if opm is None:
        sales = _col_num(rows_by_title, "매출액", op_key)
        op = _col_num(rows_by_title, "영업이익", op_key)
        if sales and abs(sales) > 1e-12 and op is not None:
            opm = op / sales * 100.0
    if prev_opm is None:
        pk = _yoy_key(op_key, all_keys)
        if pk:
            ps = _col_num(rows_by_title, "매출액", pk)
            po = _col_num(rows_by_title, "영업이익", pk)
            if ps and abs(ps) > 1e-12 and po is not None:
                prev_opm = po / ps * 100.0
    gross_map = gross_map or {}
    gm = gpp = None
    gm_asof = ""
    for col in reversed(titles):
        key = str(col.get("key") or "")
        gnow = _gmap_get(gross_map, key)
        if not gnow:
            continue
        prev = _yoy_key(key, all_keys)
        gprev = _gmap_get(gross_map, prev) if prev else None
        gm = gnow.get("margin")
        gm_prev = (gprev or {}).get("margin") if isinstance(gprev, dict) else None
        gpp = _pp_chg(gm, gm_prev)
        gm_asof = str(col.get("title") or key).rstrip(".")
        break
    return gm, gpp, gm_asof, opm, _pp_chg(opm, prev_opm)


def _yoy_op_down_streak(payload: dict) -> int:
    """최근 실제 분기가 전년 동기 영업이익보다 낮은 연속 횟수."""
    info = payload.get("financeInfo") or {}
    titles = [
        t
        for t in (info.get("trTitleList") or [])
        if isinstance(t, dict) and str(t.get("isConsensus") or "N") != "Y"
    ]
    titles.sort(key=lambda t: str(t.get("key") or ""))
    op_row = None
    for row in info.get("rowList") or []:
        if isinstance(row, dict) and row.get("title") == "영업이익":
            op_row = row.get("columns") or {}
            break
    if not op_row:
        return 0
    streak = 0
    for col in reversed(titles):
        key = str(col.get("key") or "")
        if len(key) < 6 or not key[:4].isdigit():
            continue
        prev_key = f"{int(key[:4]) - 1}{key[4:]}"
        cur = _parse_num((op_row.get(key) or {}).get("value") if isinstance(op_row.get(key), dict) else None)
        prev = _parse_num((op_row.get(prev_key) or {}).get("value") if isinstance(op_row.get(prev_key), dict) else None)
        if cur is None or prev is None:
            break
        if cur < prev:
            streak += 1
        else:
            break
    return streak


def _industry_per_from_html(code: str) -> tuple[float | None, str]:
    try:
        resp = requests.get(
            f"https://finance.naver.com/item/main.naver?code={code}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"},
            timeout=12,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return None, ""
    m = re.search(
        r"동일업종 PER</a></th>\s*<td>\s*<em>\s*([0-9.,]+)\s*</em>\s*배",
        html,
    )
    per = _parse_num(m.group(1)) if m else None
    name = ""
    for ind in re.finditer(
        r'href="/sise/sise_group_detail\.naver\?type=upjong&no=\d+"[^>]*>([^<]+)</a>',
        html,
    ):
        label = ind.group(1).strip()
        if label and "PER" not in label and "등락" not in label:
            name = label
            break
    return per, name


def _kr_fundamentals(code: str, name: str = "") -> Fundamentals:
    out = Fundamentals(market="KR", ticker=code, name=name, source="Naver")
    integ = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/integration",
        headers=_HEADERS,
        timeout=15,
    )
    integ.raise_for_status()
    js = integ.json()
    infos = _infos_map(js.get("totalInfos") or [])
    out.name = str(js.get("stockName") or name or code)
    out.per = _info_num(infos, "per")
    out.per_asof = _info_desc(infos, "per")
    out.forward_per = _info_num(infos, "cnsPer")
    out.eps = _info_num(infos, "eps")
    out.forward_eps = _info_num(infos, "cnsEps")
    out.pbr = _info_num(infos, "pbr")
    out.bps = _info_num(infos, "bps")
    out.market_cap = _info_text(infos, "marketValue")
    out.dividend_yield = _info_num(infos, "dividendYieldRatio")
    out.dividend = _info_text(infos, "dividend")
    out.foreign_rate = _info_num(infos, "foreignRate")
    out.high_52w = _info_text(infos, "highPriceOf52Weeks")
    out.low_52w = _info_text(infos, "lowPriceOf52Weeks")

    ind_per, ind_name = _industry_per_from_html(code)
    out.industry_per = ind_per
    out.industry_name = ind_name

    q = requests.get(
        f"https://m.stock.naver.com/api/stock/{code}/finance/quarter",
        headers=_HEADERS,
        timeout=15,
    )
    q.raise_for_status()
    qjs = q.json()
    comments = qjs.get("corporationSummary") or {}
    if isinstance(comments, dict):
        bits = [str(comments.get(k) or "").strip() for k in ("comment1", "comment2", "comment3")]
        out.summary = " ".join(b for b in bits if b)
    gross_map: dict[str, dict] = {}
    for symbol in _kr_yahoo_symbols(code):
        gross_map = _yahoo_gross_map(symbol)
        if gross_map:
            break
    out.quarters = _finance_table(qjs, gross_map)
    (
        out.sales_margin,
        out.sales_profit_yoy,
        out.sales_margin_asof,
        out.op_margin,
        out.op_yoy,
    ) = _latest_margins(qjs, gross_map)
    out.op_down_streak = _yoy_op_down_streak(qjs)
    return out


def _naver_world_headers(code: str = "") -> dict:
    ref = "https://m.stock.naver.com/worldstock/"
    if code:
        ref = f"https://m.stock.naver.com/worldstock/stock/{code}"
    return {"User-Agent": "Mozilla/5.0", "Referer": ref}


def _naver_us_codes(ticker: str) -> list[str]:
    t = ticker.strip().upper()
    return [f"{t}.O", f"{t}.N", f"{t}.K", f"{t}.A"]


def _naver_us_json(path: str, code: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.stock.naver.com{path}",
            headers=_naver_world_headers(code),
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        js = resp.json()
        return js if isinstance(js, dict) else None
    except Exception:
        return None


def _peer_industry_per(peers: list, self_code: str) -> float | None:
    pers: list[float] = []
    for peer in (peers or [])[:8]:
        if not isinstance(peer, dict):
            continue
        rc = str(peer.get("reutersCode") or "")
        if not rc or rc == self_code:
            continue
        basic = _naver_us_json(f"/stock/{rc}/basic", rc)
        if not basic:
            continue
        infos = _infos_map(basic.get("stockItemTotalInfos") or [])
        per = _info_num(infos, "per")
        if per is not None and per > 0:
            pers.append(per)
    if not pers:
        return None
    return sum(pers) / len(pers)


def _us_fundamentals(symbol: str) -> Fundamentals:
    import yfinance as yf

    symbol = symbol.strip().upper()
    out = Fundamentals(market="US", ticker=symbol, source="Yahoo")
    naver_code = ""
    basic = integ = fin = None
    for code in _naver_us_codes(symbol):
        basic = _naver_us_json(f"/stock/{code}/basic", code)
        if basic and basic.get("stockItemTotalInfos"):
            naver_code = code
            break
        basic = None
    if naver_code:
        infos = _infos_map(basic.get("stockItemTotalInfos") or [])
        out.source = "Naver·Yahoo"
        out.name = str(basic.get("stockName") or basic.get("stockNameEng") or symbol)
        out.per = _info_num(infos, "per")
        out.per_asof = _info_desc(infos, "per")
        out.eps = _info_num(infos, "eps")
        out.pbr = _info_num(infos, "pbr")
        out.bps = _info_num(infos, "bps")
        out.market_cap = _info_text(infos, "marketValue")
        out.dividend_yield = _info_num(infos, "dividendYieldRatio")
        out.dividend = _info_text(infos, "dividend")
        out.high_52w = _info_text(infos, "highPriceOf52Weeks")
        out.low_52w = _info_text(infos, "lowPriceOf52Weeks")
        ind = basic.get("industryCodeType") or {}
        if isinstance(ind, dict):
            out.industry_name = str(ind.get("industryGroupKor") or "")
        integ = _naver_us_json(f"/stock/{naver_code}/integration", naver_code)
        if integ:
            overview = integ.get("summaries") or {}
            if isinstance(overview, dict):
                out.summary = str(overview.get("summary") or "")
            peers = (integ.get("industryCompareInfo") or {}).get("globalStocks") or []
            out.industry_per = _peer_industry_per(peers, naver_code)
        fin = _naver_us_json(f"/stock/{naver_code}/finance/quarter", naver_code)

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}
    if not out.name or out.name == symbol:
        out.name = str(info.get("shortName") or info.get("longName") or symbol)
    if out.per is None:
        out.per = _parse_num(info.get("trailingPE"))
    out.forward_per = _parse_num(info.get("forwardPE"))
    if out.eps is None:
        out.eps = _parse_num(info.get("trailingEps"))
    out.forward_eps = _parse_num(info.get("forwardEps"))
    if out.pbr is None:
        out.pbr = _parse_num(info.get("priceToBook"))
    if out.bps is None:
        out.bps = _parse_num(info.get("bookValue"))
    if not out.market_cap:
        cap = info.get("marketCap")
        out.market_cap = f"{cap:,.0f}" if isinstance(cap, (int, float)) else str(cap or "")
    if out.dividend_yield is None:
        dy = info.get("dividendYield")
        n = _parse_num(dy)
        if n is not None and 0 < n <= 0.05:
            out.dividend_yield = n * 100.0
        else:
            out.dividend_yield = n
    if not out.high_52w:
        out.high_52w = str(info.get("fiftyTwoWeekHigh") or "")
    if not out.low_52w:
        out.low_52w = str(info.get("fiftyTwoWeekLow") or "")
    if not out.industry_name:
        out.industry_name = str(info.get("industry") or info.get("sector") or "")

    gmap = _yahoo_gross_map(symbol)
    payload = None
    if fin and (fin.get("rowList") or fin.get("trTitleList")):
        payload = {
            "financeInfo": {
                "trTitleList": fin.get("trTitleList") or [],
                "rowList": fin.get("rowList") or [],
            }
        }
        out.quarters = _finance_table(payload, gmap)
        (
            out.sales_margin,
            out.sales_profit_yoy,
            out.sales_margin_asof,
            out.op_margin,
            out.op_yoy,
        ) = _latest_margins(payload, gmap)
    if out.sales_margin is None and gmap:
        keys = sorted(gmap)
        last = keys[-1]
        prev = _yoy_key(last, keys)
        out.sales_margin = gmap[last].get("margin")
        out.sales_profit_yoy = _pp_chg(
            gmap[last].get("margin"),
            (_gmap_get(gmap, prev) or {}).get("margin") if prev else None,
        )
        out.sales_margin_asof = f"{last[:4]}.{last[4:]}" if len(last) >= 6 else last
    if out.op_margin is None:
        n = _parse_num(info.get("operatingMargins"))
        if n is not None:
            out.op_margin = n * 100.0 if abs(n) <= 1.5 else n
    return out


VALUE_STEPS = ("싼 편", "다소 싼 편", "보통", "다소 비싼 편", "비싼 편")


def _shift_value(label: str, delta: int) -> str:
    if label not in VALUE_STEPS:
        return label
    idx = max(0, min(len(VALUE_STEPS) - 1, VALUE_STEPS.index(label) + delta))
    return VALUE_STEPS[idx]


def _judge_value(fund: Fundamentals) -> tuple[str, list[str], float | None]:
    """업종 대비 PER을 기준으로 가격이 싼지/비싼지. 기술적 점수와는 별개."""
    reasons: list[str] = []
    ratio = None
    per_ok = fund.per is not None and fund.per > 0
    ind_ok = fund.industry_per is not None and fund.industry_per > 0

    if per_ok and ind_ok:
        ratio = fund.per / fund.industry_per
        if ratio < 0.70:
            label = "싼 편"
        elif ratio < 1.00:
            label = "다소 싼 편"
        elif ratio < 1.30:
            label = "보통"
        elif ratio < 1.50:
            label = "다소 비싼 편"
        else:
            label = "비싼 편"
        reasons.append(
            f"실적 PER {fund.per:.2f}배 / 업종 {fund.industry_per:.2f}배 = {ratio:.2f}배 → {label}"
        )
    elif fund.per is not None and fund.per < 0:
        if fund.pbr is not None and fund.pbr > 0:
            if fund.pbr < 1:
                label = "싼 편"
                reasons.append(f"적자라 PER 대신 PBR {fund.pbr:.2f}배(1배 미만)로 보면 싼 편")
            elif fund.pbr >= 3:
                label = "비싼 편"
                reasons.append(f"적자라 PER 대신 PBR {fund.pbr:.2f}배(3배 이상)로 보면 비싼 편")
            else:
                return "판단 보류", [f"적자라 PER을 쓰기 어렵고, PBR {fund.pbr:.2f}배로는 단정하기 어렵습니다."], None
        else:
            return "판단 보류", ["적자라 실적 PER로 가격을 판단하기 어렵습니다."], None
    elif fund.pbr is not None and fund.pbr > 0 and not ind_ok:
        if fund.pbr < 1:
            label = "싼 편"
            reasons.append(f"업종 PER이 없어 PBR {fund.pbr:.2f}배(1배 미만)로 보면 싼 편")
        elif fund.pbr >= 3:
            label = "비싼 편"
            reasons.append(f"업종 PER이 없어 PBR {fund.pbr:.2f}배(3배 이상)로 보면 비싼 편")
        else:
            return "판단 보류", [f"업종 PER이 없고 PBR {fund.pbr:.2f}배로는 단정하기 어렵습니다."], None
    else:
        return "판단 보류", ["실적 PER 또는 업종 PER이 없어 상대 평가를 하지 못했습니다."], None

    if per_ok and fund.forward_per is not None:
        fwd_vs = fund.forward_per / fund.per
        if fwd_vs <= 0.80:
            nxt = _shift_value(label, -1)
            if nxt != label:
                reasons.append(
                    f"추정 PER {fund.forward_per:.2f}배가 실적보다 {(1 - fwd_vs) * 100:.0f}% 낮음 → {label}에서 {nxt}"
                )
            else:
                reasons.append(
                    f"추정 PER {fund.forward_per:.2f}배가 실적보다 {(1 - fwd_vs) * 100:.0f}% 낮아 이익 증가가 반영된 상태입니다."
                )
            label = nxt
        elif fwd_vs >= 1.20:
            nxt = _shift_value(label, 1)
            if nxt != label:
                reasons.append(
                    f"추정 PER {fund.forward_per:.2f}배가 실적보다 {(fwd_vs - 1) * 100:.0f}% 높음 → {label}에서 {nxt}"
                )
            else:
                reasons.append(
                    f"추정 PER {fund.forward_per:.2f}배가 실적보다 {(fwd_vs - 1) * 100:.0f}% 높아 이익 감소가 반영된 상태입니다."
                )
            label = nxt
        else:
            reasons.append(
                f"추정 PER {fund.forward_per:.2f}배는 실적 PER과 크게 다르지 않습니다."
            )

    if fund.op_yoy is not None and fund.op_yoy >= 10 and label in ("다소 비싼 편", "비싼 편"):
        nxt = _shift_value(label, -1)
        if nxt != label:
            reasons.append(
                f"영업이익률이 전년 동기 대비 {fund.op_yoy:+.1f}%p → 성장 반영으로 {label}에서 {nxt}"
            )
        label = nxt
    if int(fund.op_down_streak or 0) >= 2 and label in ("싼 편", "다소 싼 편"):
        nxt = _shift_value(label, 1)
        if nxt != label:
            reasons.append(
                f"영업이익이 전년 동기 대비 {fund.op_down_streak}개 분기 연속 감소 → {label}에서 {nxt}"
            )
        label = nxt

    if fund.pbr is not None and fund.pbr > 0 and per_ok and ind_ok:
        if fund.pbr < 1:
            reasons.append(f"PBR {fund.pbr:.2f}배는 1배 미만(자산 대비 참고).")
        elif fund.pbr >= 3:
            reasons.append(f"PBR {fund.pbr:.2f}배는 3배 이상(자산 대비 참고).")

    return label, reasons, ratio


def _build_warnings(fund: Fundamentals, action: str | None) -> list[str]:
    notes: list[str] = []
    buy = action in BUY_ACTIONS
    sell = action in SELL_ACTIONS
    if fund.per is not None and fund.per < 0:
        notes.append("적자라 실적 PER을 해석하기 어렵습니다.")
    if (
        fund.per is not None
        and fund.industry_per is not None
        and fund.industry_per > 0
        and fund.per >= fund.industry_per * 1.5
    ):
        msg = (
            f"실적 PER {fund.per:.1f}배가 업종 {fund.industry_per:.1f}배보다 "
            f"{(fund.per / fund.industry_per - 1) * 100:.0f}% 높습니다."
        )
        if buy:
            msg = "기술적 매수인데 " + msg + " 추격 과열일 수 있습니다."
        notes.append(msg)
    if fund.per is not None and fund.forward_per is not None and fund.per > 0:
        if fund.forward_per > fund.per * 1.2:
            notes.append(
                f"추정 PER({fund.forward_per:.1f})이 실적 PER({fund.per:.1f})보다 높아 "
                "앞으로 이익이 줄어들 것으로 반영된 상태입니다."
            )
    streak = int(fund.op_down_streak or 0)
    if streak >= 2:
        msg = f"영업이익이 전년 동기 대비 {streak}개 분기 연속 감소했습니다."
        if buy:
            msg = "기술적 매수인데 " + msg
        notes.append(msg)
    if sell and fund.pbr is not None and 0 < fund.pbr < 1 and streak == 0:
        notes.append("기술적 매도인데 PBR이 1배 미만입니다. 싸 보일 수 있으나 단기 과열과 구분해야 합니다.")
    return notes


def fmt_per(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:.2f}배"


def fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:.2f}%"


def fmt_pp(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:+.1f}%p"


def per_gap_text(fund: Fundamentals) -> str:
    if fund.per is None or fund.forward_per is None:
        return "실적 PER 또는 추정 PER이 없어 차이를 계산하지 못했습니다."
    gap = fund.per_gap
    pct = fund.per_gap_pct
    if gap is None or pct is None:
        return "-"
    if abs(fund.per) < 1e-9:
        return f"추정 {fund.forward_per:.2f}배 · 실적 PER이 0에 가깝습니다."
    if gap < 0:
        tone = "추정 PER이 더 낮아 앞으로 이익이 늘어날 것으로 반영된 상태입니다."
    elif gap > 0:
        tone = "추정 PER이 더 높아 앞으로 이익이 줄어들 것으로 반영된 상태입니다."
    else:
        tone = "추정과 실적 PER이 같습니다."
    return (
        f"실적 PER {fund.per:.2f}배 · 추정 PER {fund.forward_per:.2f}배 · "
        f"차이 {gap:+.2f}배 ({pct:+.1f}%). {tone}"
    )


def fetch_fundamentals(market: str, ticker: str, action: str | None = None) -> Fundamentals:
    market = (market or "").upper()
    ticker = (ticker or "").strip()
    if market == "CRYPTO":
        return Fundamentals(market=market, ticker=ticker, error="코인은 재무제표가 없어 펀더 요약을 제공하지 않습니다.")
    if not ticker:
        return Fundamentals(market=market, ticker=ticker, error="종목 코드가 없습니다.")

    cache = CACHE_DIR / f"fund5_{market}_{ticker}_{date.today().isoformat()}.json"
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            cached = None

    try:
        if market == "KR":
            code = ticker.zfill(6) if ticker.isdigit() else ticker
            fund = _kr_fundamentals(code)
        elif market == "US":
            fund = _us_fundamentals(ticker.upper())
        else:
            return Fundamentals(market=market, ticker=ticker, error="이 시장은 펀더 요약을 지원하지 않습니다.")
    except Exception as exc:
        if cached:
            fund = Fundamentals(market=market, ticker=ticker, error=None)
            for key, val in cached.items():
                if key in Fundamentals.__dataclass_fields__ and key != "warnings":
                    setattr(fund, key, val)
            fund.warnings = _build_warnings(fund, action)
            fund.value_label, fund.value_reasons, fund.per_vs_industry = _judge_value(fund)
            fund.source = (fund.source or "") + " (캐시)"
            return fund
        return Fundamentals(market=market, ticker=ticker, error=f"펀더 데이터를 받지 못했습니다: {exc}")

    fund.warnings = _build_warnings(fund, action)
    fund.value_label, fund.value_reasons, fund.per_vs_industry = _judge_value(fund)
    try:
        payload = {
            "market": fund.market,
            "ticker": fund.ticker,
            "name": fund.name,
            "source": fund.source,
            "per": fund.per,
            "per_asof": fund.per_asof,
            "forward_per": fund.forward_per,
            "eps": fund.eps,
            "forward_eps": fund.forward_eps,
            "pbr": fund.pbr,
            "bps": fund.bps,
            "market_cap": fund.market_cap,
            "dividend_yield": fund.dividend_yield,
            "dividend": fund.dividend,
            "foreign_rate": fund.foreign_rate,
            "high_52w": fund.high_52w,
            "low_52w": fund.low_52w,
            "industry_per": fund.industry_per,
            "industry_name": fund.industry_name,
            "summary": fund.summary,
            "sales_margin": fund.sales_margin,
            "sales_profit_yoy": fund.sales_profit_yoy,
            "sales_margin_asof": fund.sales_margin_asof,
            "op_margin": fund.op_margin,
            "op_yoy": fund.op_yoy,
            "quarters": fund.quarters,
            "op_down_streak": fund.op_down_streak,
        }
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return fund
