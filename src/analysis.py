"""추세선, 지지/저항, 매물대(일봉 거래량 프로파일). 지정일 이후 데이터는 사용하지 않음."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Level:
    price: float
    kind: str
    strength: float
    touches: int
    note: str = ""


@dataclass
class Analysis:
    as_of: pd.Timestamp
    last_bar: pd.Timestamp
    price: float
    trend: str
    rsi: float
    atr: float
    ma20: float | None
    ma60: float | None
    poc: float
    vah: float
    val: float
    supports: list[Level] = field(default_factory=list)
    resistances: list[Level] = field(default_factory=list)
    volume_nodes: list[Level] = field(default_factory=list)
    vp_centers: np.ndarray = field(default_factory=lambda: np.array([]))
    vp_volumes: np.ndarray = field(default_factory=lambda: np.array([]))
    up_line: tuple[float, float, float, float] | None = None
    down_line: tuple[float, float, float, float] | None = None
    swing_highs: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    swing_lows: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    df: pd.DataFrame | None = None


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def find_swings(df: pd.DataFrame, left: int = 4, right: int = 4) -> tuple[list, list]:
    highs: list[tuple[pd.Timestamp, float, int]] = []
    lows: list[tuple[pd.Timestamp, float, int]] = []
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    idx = df.index
    n = len(df)
    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        if h[i] >= window_h.max() and (window_h == h[i]).sum() == 1:
            highs.append((idx[i], float(h[i]), i))
        if l[i] <= window_l.min() and (window_l == l[i]).sum() == 1:
            lows.append((idx[i], float(l[i]), i))
    return highs, lows


def _line_through(p0: tuple[int, float], p1: tuple[int, float], x_end: int) -> tuple[float, float, float, float] | None:
    x0, y0 = p0
    x1, y1 = p1
    if x1 == x0:
        return None
    slope = (y1 - y0) / (x1 - x0)
    y_end = y1 + slope * (x_end - x1)
    return (float(x0), float(y0), float(x_end), float(y_end))


def volume_profile(df: pd.DataFrame, bins: int = 48) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    """일봉 [저가, 고가] 구간에 거래량을 균등 분배한 가격대 히스토그램."""
    pmin = float(df["low"].min())
    pmax = float(df["high"].max())
    if not np.isfinite(pmin) or not np.isfinite(pmax) or pmax <= pmin:
        pmax = pmin * 1.01 if pmin else 1.0
        pmin = pmin if pmin else 0.0
    edges = np.linspace(pmin, pmax, bins + 1)
    vol = np.zeros(bins, dtype=float)
    lows = df["low"].to_numpy()
    highs = df["high"].to_numpy()
    volumes = df["volume"].to_numpy()

    for lo, hi, v in zip(lows, highs, volumes):
        if not np.isfinite(v) or v <= 0:
            continue
        if not np.isfinite(lo) or not np.isfinite(hi):
            continue
        if hi <= lo:
            i = int(np.clip(np.searchsorted(edges, lo, side="right") - 1, 0, bins - 1))
            vol[i] += float(v)
            continue
        i0 = int(np.clip(np.searchsorted(edges, lo, side="right") - 1, 0, bins - 1))
        i1 = int(np.clip(np.searchsorted(edges, hi, side="left") - 1, 0, bins - 1))
        if i1 < i0:
            i0, i1 = i1, i0
        n = i1 - i0 + 1
        vol[i0 : i1 + 1] += float(v) / n

    centers = (edges[:-1] + edges[1:]) / 2.0
    poc_i = int(np.argmax(vol)) if vol.sum() > 0 else bins // 2
    poc = float(centers[poc_i])

    total = float(vol.sum())
    if total <= 0:
        return centers, vol, poc, float(centers[-1]), float(centers[0])

    target = total * 0.70
    lo_i = hi_i = poc_i
    covered = float(vol[poc_i])
    while covered < target and (lo_i > 0 or hi_i < bins - 1):
        left = vol[lo_i - 1] if lo_i > 0 else -1
        right = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if right >= left:
            hi_i += 1
            covered += float(vol[hi_i])
        else:
            lo_i -= 1
            covered += float(vol[lo_i])
    vah = float(centers[hi_i])
    val = float(centers[lo_i])
    return centers, vol, poc, vah, val


def _local_maxima(values: np.ndarray, order: int = 2) -> list[int]:
    peaks = []
    n = len(values)
    for i in range(order, n - order):
        window = values[i - order : i + order + 1]
        if values[i] == window.max() and values[i] > 0:
            peaks.append(i)
    return peaks


def cluster_prices(prices: list[float], threshold: float) -> list[tuple[float, int]]:
    if not prices:
        return []
    prices = sorted(float(p) for p in prices if np.isfinite(p))
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        if p - clusters[-1][-1] <= threshold:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [(sum(c) / len(c), len(c)) for c in clusters]


def analyze(df: pd.DataFrame, as_of=None) -> Analysis:
    if df is None or df.empty:
        raise ValueError("분석할 일봉이 없습니다.")

    work = df.copy()
    if as_of is not None:
        cutoff = pd.Timestamp(as_of) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        work = work.loc[work.index <= cutoff]
    if work.empty:
        raise ValueError("지정일 이전 일봉이 없습니다.")

    work["ma20"] = work["close"].rolling(20).mean()
    work["ma60"] = work["close"].rolling(60).mean()
    work["rsi"] = rsi(work["close"])
    work["atr"] = atr(work)

    last = work.iloc[-1]
    price = float(last["close"])
    last_atr = float(last["atr"]) if pd.notna(last["atr"]) else max(price * 0.02, 1e-8)
    last_rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
    ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else None
    ma60 = float(last["ma60"]) if pd.notna(last["ma60"]) else None

    highs, lows = find_swings(work)
    n = len(work)
    x_end = n - 1

    up_line = None
    if len(lows) >= 2:
        up_line = _line_through((lows[-2][2], lows[-2][1]), (lows[-1][2], lows[-1][1]), x_end)

    down_line = None
    if len(highs) >= 2:
        down_line = _line_through((highs[-2][2], highs[-2][1]), (highs[-1][2], highs[-1][1]), x_end)

    structure = "sideways"
    if len(lows) >= 2 and len(highs) >= 2:
        hl = lows[-1][1] > lows[-2][1]
        hh = highs[-1][1] > highs[-2][1]
        ll = lows[-1][1] < lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]
        if hl and hh:
            structure = "up"
        elif ll and lh:
            structure = "down"

    ma_trend = "sideways"
    if ma20 is not None and ma60 is not None:
        if ma20 > ma60 * 1.005 and price > ma20:
            ma_trend = "up"
        elif ma20 < ma60 * 0.995 and price < ma20:
            ma_trend = "down"

    if ma60 is None:
        # 1~3개월처럼 봉이 짧으면 스윙 2개만으로 상승/하락을 확정하지 않는다.
        if structure == "up" and ma20 is not None and price > ma20:
            trend = "up"
        elif structure == "down" and ma20 is not None and price < ma20:
            trend = "down"
        else:
            trend = "sideways"
    elif structure == ma_trend:
        trend = structure
    elif structure != "sideways":
        trend = structure
    else:
        trend = ma_trend

    centers, vols, poc, vah, val = volume_profile(work)
    peak_idx = _local_maxima(vols, order=2)
    if not peak_idx and vols.sum() > 0:
        peak_idx = [int(np.argmax(vols))]
    peak_idx = sorted(peak_idx, key=lambda i: vols[i], reverse=True)[:6]
    max_vol = float(vols.max()) if len(vols) else 1.0

    volume_nodes: list[Level] = []
    for i in peak_idx:
        volume_nodes.append(
            Level(
                price=float(centers[i]),
                kind="volume_node",
                strength=float(vols[i] / max_vol) if max_vol else 0.0,
                touches=int(round(10 * vols[i] / max_vol)) if max_vol else 0,
                note="주요 매물대",
            )
        )

    cluster_th = max(last_atr * 0.4, price * 0.008)
    clustered = cluster_prices([p for _, p, _ in highs + lows], cluster_th)

    supports: list[Level] = []
    resistances: list[Level] = []
    for mid, touches in clustered:
        kind = "support" if mid <= price else "resistance"
        note = f"스윙 {touches}회 군집"
        level = Level(price=mid, kind=kind, strength=float(touches), touches=int(touches), note=note)
        if kind == "support":
            supports.append(level)
        else:
            resistances.append(level)

    # 매물대 중 현재가 아래는 지지, 위는 저항 보강
    for node in volume_nodes:
        if abs(node.price - price) / price < 0.004:
            continue
        if node.price < price:
            supports.append(
                Level(
                    price=node.price,
                    kind="support",
                    strength=node.strength * 3,
                    touches=node.touches,
                    note="매물대 지지",
                )
            )
        else:
            resistances.append(
                Level(
                    price=node.price,
                    kind="resistance",
                    strength=node.strength * 3,
                    touches=node.touches,
                    note="매물대 저항",
                )
            )

    for extra_price, extra_note in ((val, "밸류 하단 VAL"), (poc, "POC 집중 매물"), (vah, "밸류 상단 VAH")):
        if extra_price < price * 0.996:
            supports.append(Level(price=extra_price, kind="support", strength=2.0, touches=1, note=extra_note))
        elif extra_price > price * 1.004:
            resistances.append(Level(price=extra_price, kind="resistance", strength=2.0, touches=1, note=extra_note))

    supports = _dedupe_levels(supports, cluster_th, below=True, price=price)
    resistances = _dedupe_levels(resistances, cluster_th, below=False, price=price)

    return Analysis(
        as_of=pd.Timestamp(as_of) if as_of is not None else work.index[-1],
        last_bar=work.index[-1],
        price=price,
        trend=trend,
        rsi=last_rsi,
        atr=last_atr,
        ma20=ma20,
        ma60=ma60,
        poc=poc,
        vah=vah,
        val=val,
        supports=supports[:5],
        resistances=resistances[:5],
        volume_nodes=volume_nodes,
        vp_centers=centers,
        vp_volumes=vols,
        up_line=up_line,
        down_line=down_line,
        swing_highs=[(t, p) for t, p, _ in highs[-8:]],
        swing_lows=[(t, p) for t, p, _ in lows[-8:]],
        df=work,
    )


def _dedupe_levels(levels: list[Level], threshold: float, below: bool, price: float) -> list[Level]:
    if not levels:
        return []
    ordered = sorted(levels, key=lambda x: x.price, reverse=not below)
    kept: list[Level] = []
    for lv in ordered:
        if below and lv.price > price:
            continue
        if not below and lv.price < price:
            continue
        if any(abs(lv.price - k.price) <= threshold for k in kept):
            # 더 강한 쪽 유지
            for i, k in enumerate(kept):
                if abs(lv.price - k.price) <= threshold and lv.strength > k.strength:
                    kept[i] = lv
            continue
        kept.append(lv)
    kept.sort(key=lambda x: abs(price - x.price))
    return kept
