"""종목 펀더멘털 요약. 기술적 점수와는 섞지 않고 표시·경고만 한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import requests

from .data import CACHE_DIR

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
        return float(val)
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
    return str(row.get("valueDesc") or "")


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
    quarters: list[dict] = field(default_factory=list)
    op_down_streak: int = 0
    warnings: list[str] = field(default_factory=list)
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


def _finance_table(payload: dict, limit: int = 4) -> list[dict]:
    info = payload.get("financeInfo") or {}
    titles = info.get("trTitleList") or []
    actual = [t for t in titles if isinstance(t, dict) and str(t.get("isConsensus") or "N") != "Y"]
    actual.sort(key=lambda t: str(t.get("key") or ""))
    actual = actual[-limit:]
    if not actual:
        return []
    wanted = ("매출액", "영업이익", "당기순이익", "영업이익률", "ROE", "부채비율")
    rows_by_title = {
        str(r.get("title") or ""): r.get("columns") or {}
        for r in (info.get("rowList") or [])
        if isinstance(r, dict)
    }
    out = []
    for col in actual:
        key = str(col.get("key") or "")
        item = {"기간": str(col.get("title") or key).rstrip(".")}
        for name in wanted:
            cell = (rows_by_title.get(name) or {}).get(key) or {}
            item[name] = cell.get("value") if isinstance(cell, dict) else None
        out.append(item)
    return out


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
    out.quarters = _finance_table(qjs)
    out.op_down_streak = _yoy_op_down_streak(qjs)
    return out


def _yahoo_fundamentals(symbol: str) -> Fundamentals:
    import yfinance as yf

    out = Fundamentals(market="US", ticker=symbol, source="Yahoo")
    info = yf.Ticker(symbol).info or {}
    out.name = str(info.get("shortName") or info.get("longName") or symbol)
    out.per = _parse_num(info.get("trailingPE"))
    out.forward_per = _parse_num(info.get("forwardPE"))
    out.eps = _parse_num(info.get("trailingEps"))
    out.forward_eps = _parse_num(info.get("forwardEps"))
    out.pbr = _parse_num(info.get("priceToBook"))
    out.bps = _parse_num(info.get("bookValue"))
    cap = info.get("marketCap")
    out.market_cap = f"{cap:,.0f}" if isinstance(cap, (int, float)) else str(cap or "")
    dy = info.get("dividendYield")
    if isinstance(dy, (int, float)) and 0 < dy < 1:
        out.dividend_yield = dy * 100.0
    else:
        out.dividend_yield = _parse_num(dy)
    out.high_52w = str(info.get("fiftyTwoWeekHigh") or "")
    out.low_52w = str(info.get("fiftyTwoWeekLow") or "")
    out.industry_name = str(info.get("industry") or info.get("sector") or "")
    return out


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

    cache = CACHE_DIR / f"fund_{market}_{ticker}_{date.today().isoformat()}.json"
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
            fund = _yahoo_fundamentals(ticker.upper())
        else:
            return Fundamentals(market=market, ticker=ticker, error="이 시장은 펀더 요약을 지원하지 않습니다.")
    except Exception as exc:
        if cached:
            fund = Fundamentals(market=market, ticker=ticker, error=None)
            for key, val in cached.items():
                if key in Fundamentals.__dataclass_fields__ and key != "warnings":
                    setattr(fund, key, val)
            fund.warnings = _build_warnings(fund, action)
            fund.source = (fund.source or "") + " (캐시)"
            return fund
        return Fundamentals(market=market, ticker=ticker, error=f"펀더 데이터를 받지 못했습니다: {exc}")

    fund.warnings = _build_warnings(fund, action)
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
            "quarters": fund.quarters,
            "op_down_streak": fund.op_down_streak,
        }
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return fund
