"""시장 데이터 갱신 — 공급자에서 받아 데이터 팩을 새로 만든다.

받은 것만 갈아 끼운다. 어떤 공급자에서 가격을 받았고 배당은 어디서 왔는지 종목마다
기록해서 화면에 그대로 띄운다. 배당 이력을 주는 공급자에 닿지 못하면 캐시의 배당을
그대로 쓰고 그 사실을 밝힌다 — 조용히 옛 숫자를 새 숫자처럼 보여주지 않는다.

진행 상황은 콜백으로 흘려보낸다. 서버는 이걸 SSE로 그대로 중계한다.
"""
import json
import os
import time

from . import providers
from .universe import FX_SYMBOL, UNIVERSE

HISTORY_YEARS = 20
MIN_MONTHS = 36
# 한 공급자가 이만큼 연달아 실패하면 이번 갱신에서는 더 두드리지 않는다. 야후가 IP를
# 막아버린 경우 36종목마다 재시도하면 몇 분을 그냥 버린다.
GIVE_UP_AFTER = 2
# 캐시와 새 데이터를 이어 붙일 때, 겹치는 구간의 중위 오차가 이보다 크면 서로 다른
# 보정 기준(액면분할·배당 조정)을 쓰는 것으로 보고 이어 붙이지 않는다.
STITCH_TOLERANCE = 0.02
STITCH_MIN_OVERLAP = 6
# 상장일을 진짜로 아는 공급자. 나머지는 시계열 시작일밖에 모르므로 캐시 값을 지킨다.
KNOWS_LISTING_DATE = {"yahoo", "alphavantage"}


def _range():
    now = time.gmtime()
    return ("%04d-%02d-01" % (now.tm_year - HISTORY_YEARS, now.tm_mon),
            time.strftime("%Y-%m-%d", now))


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0


def stitch_prices(live, cached):
    """새 시계열 앞쪽에 캐시의 오래된 달을 이어 붙인다.

    공급자가 10년치만 주면 16년 백테스트를 못 하게 된다. 겹치는 구간이 충분히
    일치할 때만 이어 붙이고, 아니면 새 데이터만 쓴다 — 서로 다른 기준의 가격을
    한 줄로 이으면 그 이음매가 가짜 급등락으로 남는다.
    """
    if not cached:
        return live, False
    live_map, cached_map = dict(live), dict(cached)
    overlap = sorted(set(live_map) & set(cached_map))
    older = [ym for ym in sorted(cached_map) if ym < min(live_map)]
    if not older:
        return live, False
    if len(overlap) < STITCH_MIN_OVERLAP:
        return live, False
    diffs = [abs(live_map[ym] - cached_map[ym]) / cached_map[ym]
             for ym in overlap if cached_map[ym]]
    if not diffs or _median(diffs) > STITCH_TOLERANCE:
        return live, False
    return [[ym, cached_map[ym]] for ym in older] + live, True


def stitch_dividends(live, cached):
    """배당도 같은 식으로 합친다. 겹치는 달은 새 데이터가 이긴다."""
    if not cached:
        return live, False
    if not live:
        return cached, True
    merged = dict((ym, amount) for ym, amount in cached if ym < min(ym for ym, _ in live))
    if not merged:
        return live, False
    merged.update(dict(live))
    return [[ym, merged[ym]] for ym in sorted(merged)], True


def _cached(pack, symbol):
    for row in (pack or {}).get("tickers", []):
        if row["symbol"] == symbol:
            return row
    return None


def refresh(pack=None, progress=None, only=None):
    """새 데이터 팩과 리포트를 돌려준다. pack은 기존 캐시(배당 폴백에 쓴다)."""
    say = progress or (lambda **kw: None)
    start, end = _range()
    total = len(UNIVERSE) + 1
    report = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "fx": None, "tickers": [], "failed": [], "warnings": []}

    # ------------------------------------------------------------------ 환율
    say(step=1, total=total, symbol=FX_SYMBOL, label="원달러 환율")
    fx_rows, fx_source, fx_absolute = None, None, True
    for name, fn in providers.fx_chain():
        try:
            fx_rows, fx_source = fn(start, end), name
            break
        except providers.ProviderError as exc:
            report["warnings"].append("환율 %s 실패: %s" % (name, exc))
    if fx_rows is None:
        if not pack:
            raise RuntimeError("환율을 받지 못했고 쓸 수 있는 캐시도 없습니다")
        fx_rows = pack.get("fx_usdkrw") or pack.get("fx_usdkrw_index")
        fx_source = (pack.get("sources") or {}).get("fx", "cache")
        fx_absolute = bool(pack.get("fx_absolute"))
        report["warnings"].append("환율은 캐시 값을 그대로 씁니다")
    report["fx"] = {"source": fx_source, "months": len(fx_rows),
                    "absolute": fx_absolute,
                    "latest": fx_rows[-1][1] if fx_absolute else None}
    say(step=1, total=total, symbol=FX_SYMBOL, label="원달러 환율",
        source=fx_source, months=len(fx_rows))

    # ------------------------------------------------------------------ 종목
    tickers = []
    strikes, dead = {}, set()
    for index, meta in enumerate(UNIVERSE, start=2):
        symbol = meta["symbol"]
        if only and symbol not in only:
            row = _cached(pack, symbol)
            if row:
                tickers.append(row)
            continue
        say(step=index, total=total, symbol=symbol, label=meta["name"])
        prices = divs = first_date = None
        source = None
        errors = []
        for name, fn in providers.price_chain(meta["market"]):
            if name in dead:
                continue
            try:
                prices, divs, first_date = fn(meta)
                source = name
                strikes[name] = 0
                break
            except providers.ProviderError as exc:
                errors.append("%s: %s" % (name, exc))
                strikes[name] = strikes.get(name, 0) + 1
                if strikes[name] >= GIVE_UP_AFTER:
                    dead.add(name)
                    report["warnings"].append(
                        "%s 공급자를 이번 갱신에서 제외합니다 (%d회 연속 실패: %s)"
                        % (name, strikes[name], exc))

        cache_row = _cached(pack, symbol)
        if prices is None:
            if cache_row:
                tickers.append(cache_row)
                report["failed"].append({"symbol": symbol, "errors": errors,
                                         "kept": "cache"})
                say(step=index, total=total, symbol=symbol, label=meta["name"],
                    source="cache", note="갱신 실패 — 캐시 유지")
            else:
                report["failed"].append({"symbol": symbol, "errors": errors,
                                         "kept": None})
                say(step=index, total=total, symbol=symbol, label=meta["name"],
                    note="갱신 실패 — 데이터 없음")
            continue

        price_source = source
        prices, extended = stitch_prices(prices, (cache_row or {}).get("prices"))
        if extended:
            price_source = source + "+cache"

        dividend_source = source
        if not divs:
            if cache_row and cache_row.get("dividends"):
                divs = cache_row["dividends"]
                dividend_source = "cache"
            else:
                divs = []
                dividend_source = None
                report["warnings"].append("%s 배당 이력이 없습니다" % symbol)
        else:
            divs, merged = stitch_dividends(divs, (cache_row or {}).get("dividends"))
            if merged:
                dividend_source = source + "+cache"

        if source not in KNOWS_LISTING_DATE and cache_row and cache_row.get("first_date"):
            # 시계열 시작일을 상장일로 오해하지 않는다 (10년치만 받은 종목이 신규 상장으로 보임)
            first_date = min(cache_row["first_date"], first_date)

        tickers.append(dict(meta, first_date=first_date, prices=prices, dividends=divs,
                            price_source=price_source, dividend_source=dividend_source))
        report["tickers"].append({"symbol": symbol, "price_source": price_source,
                                  "dividend_source": dividend_source,
                                  "months": len(prices), "dividends": len(divs)})
        note = None
        if dividend_source == "cache":
            note = "배당은 캐시"
        elif extended:
            note = "이력 이어 붙임"
        say(step=index, total=total, symbol=symbol, label=meta["name"],
            source=price_source, months=len(prices), dividends=len(divs), note=note)
        time.sleep(0.15)

    if not tickers:
        raise RuntimeError("갱신된 종목이 하나도 없습니다")

    as_of = max(t["prices"][-1][0] for t in tickers)
    # 기준월 이후의 환율은 쓰지 않으니 잘라낸다. 다만 잘라서 남는 게 없으면
    # (가격 시계열이 환율보다 훨씬 짧은 경우) 통째로 들고 간다 — 환율이 비면 못 돈다.
    clipped = [row for row in fx_rows if row[0] <= as_of]
    if len(clipped) >= 24:
        fx_rows = clipped
    else:
        report["warnings"].append(
            "환율 시계열이 기준월(%s) 이후 구간까지만 있어 그대로 보관합니다" % as_of)
    report["fx"]["months"] = len(fx_rows)
    new_pack = {
        "as_of": as_of,
        "fetched_at": report["started_at"],
        "currency": {"US": "USD", "KR": "KRW"},
        "fx_absolute": fx_absolute,
        "fx_usdkrw": fx_rows,
        "sources": {"fx": fx_source, "price_chain_us": [n for n, _ in providers.price_chain("US")],
                    "price_chain_kr": [n for n, _ in providers.price_chain("KR")]},
        "tickers": tickers,
    }
    report["as_of"] = as_of
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["ticker_count"] = len(tickers)
    return new_pack, report


def save(pack, path):
    """같은 디렉터리에 임시 파일로 쓰고 바꿔치기한다 — 중간에 죽어도 팩이 깨지지 않는다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path
