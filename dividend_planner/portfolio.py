"""팩터 점수 · 종목 선정 · 목표 비중 계산.

점수는 유니버스 36종목 안에서의 백분위(동점은 평균 순위)이며, 프로파일 가중치를
곱해 종합 점수를 만든다. 비중은 종합 점수의 제곱에 비례한다 — 제곱을 쓰면 점수
차이가 비중 차이로 더 또렷하게 드러나면서도 하위 종목이 0으로 사라지지 않는다.

점수와 별개로 세 개의 구성 제약이 걸린다.
  · ETF 비중  — 기본 60%. ETF를 코어로 깔고 개별주로 수익률을 보완하는 구성이다.
  · 미국 비중 — 나머지는 국내.
  · 섹터 상한 — 한 섹터 30% (ETF는 자체 분산되어 제외).
그래서 점수 순서와 비중 순서는 어긋날 수 있다. 어긋난 이유는 화면에 적어 둔다.
"""
from .dataset import MIN_YIELD

FACTOR_LABELS = [
    ("yield", "현재 배당수익률"),
    ("growth", "배당성장률"),
    ("stability", "안정성"),
    ("quality", "총수익"),
    ("smooth", "현금흐름 평탄화"),
]

# 남은 기간이 길수록 "지금의 수익률"보다 "배당이 얼마나 빨리 커지는가"가 중요하다.
BASE_PROFILES = {
    "income":   {"yield": 0.45, "growth": 0.10, "stability": 0.25, "quality": 0.10, "smooth": 0.10},
    "balanced": {"yield": 0.30, "growth": 0.25, "stability": 0.25, "quality": 0.15, "smooth": 0.05},
    "growth":   {"yield": 0.20, "growth": 0.35, "stability": 0.25, "quality": 0.15, "smooth": 0.05},
}
RISK_STABILITY = {"conservative": 0.35, "balanced": 0.25, "aggressive": 0.15}
BASE_STABILITY = 0.25
STABILITY_MIX = {"nvol": 0.20, "nmdd": 0.15, "streak": 0.35, "ncut": 0.30}
SECTOR_CAP = 0.30
SECTOR_CAP_EXEMPT = {"ETF"}     # ETF는 그 자체로 분산되어 있어 섹터 상한에서 제외
PORTFOLIO_SIZE = 10
# 추천 포트폴리오의 기본 성격. ETF를 코어로 깔면 종목 하나가 어긋나도 전체가
# 덜 흔들리고, 섹터·종목 분산을 따로 챙기지 않아도 된다. None을 넘기면 제약 없이
# 점수만으로 고른다 (참조 서비스와 같은 동작 — 정합성 테스트가 이 경로를 쓴다).
DEFAULT_ETF_RATIO = 0.6


def profile_for(horizon_years, risk_preference):
    if horizon_years >= 15:
        name = "growth"
    elif horizon_years >= 8:
        name = "balanced"
    else:
        name = "income"
    base = BASE_PROFILES[name]
    stability = RISK_STABILITY.get(risk_preference, BASE_STABILITY)
    scale = (1.0 - stability) / (1.0 - BASE_STABILITY)
    weights = {"profile": name, "stability": stability}
    for key in ("yield", "growth", "quality", "smooth"):
        weights[key] = base[key] * scale
    return weights


def _percentile(items, key):
    """오름차순 평균 순위 백분위 (1위 = 100/n, 최고 = 100)."""
    ordered = sorted(items, key=key)
    n = len(ordered)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and key(ordered[j + 1]) == key(ordered[i]):
            j += 1
        pct = ((i + 1) + (j + 1)) / 2.0 / n * 100.0
        for k in range(i, j + 1):
            out[ordered[k].symbol] = pct
        i = j + 1
    return out


def factor_scores(tickers):
    """유니버스 전체 기준 팩터별 백분위 점수. 필터와 무관하게 항상 36종목 기준."""
    neg_inf = float("-inf")
    p = {
        "yield": _percentile(tickers, lambda t: t.ttm_yield),
        "growth": _percentile(tickers, lambda t: t.div_cagr if t.div_cagr is not None else neg_inf),
        "quality": _percentile(tickers, lambda t: t.total_return_cagr),
        "smooth": _percentile(tickers, lambda t: t.payout_frequency),
        "nvol": _percentile(tickers, lambda t: -t.volatility),
        "nmdd": _percentile(tickers, lambda t: t.mdd),
        "streak": _percentile(tickers, lambda t: t.div_growth_streak),
        "ncut": _percentile(tickers, lambda t: -t.div_cut_count_10y),
    }
    scores = {}
    for t in tickers:
        stability = sum(p[k][t.symbol] * w for k, w in STABILITY_MIX.items())
        scores[t.symbol] = {
            "yield": p["yield"][t.symbol],
            "growth": p["growth"][t.symbol],
            "stability": stability,
            "quality": p["quality"][t.symbol],
            "smooth": p["smooth"][t.symbol],
        }
    return scores


def screen(tickers, backtest_years):
    """유니버스를 통과 종목과 제외 종목으로 나눈다."""
    eligible, excluded = [], []
    for t in tickers:
        if t.effective_history_years < backtest_years:
            # 상장이 오래됐어도 받아온 데이터가 짧으면 그 길이가 한계다
            if t.data_years < t.history_years:
                reason = ("보유 데이터 %s년 (%s개월) < 백테스트 %d년"
                          % (t.data_years, t.data_months, backtest_years))
            else:
                reason = ("%s 상장 · 데이터 이력 %s년 < 백테스트 %d년"
                          % (t.first_date, t.history_years, backtest_years))
            excluded.append({"symbol": t.symbol, "name": t.name, "reason": reason})
        elif t.ttm_yield < MIN_YIELD:
            excluded.append({
                "symbol": t.symbol, "name": t.name,
                "reason": "배당수익률 %.2f%% < 최소 기준 %g%% — 배당 목적에 부적합"
                          % (t.ttm_yield * 100, MIN_YIELD * 100)})
        else:
            eligible.append(t)
    return eligible, excluded


def _bucket_weights(picked, scores, share):
    """한 바구니 안에서 종합점수² 비례 비중을 만들고 share 합계에 맞춘다."""
    total = sum(scores[t.symbol] ** 2 for t in picked)
    if total <= 0:
        return dict((t.symbol, share / len(picked)) for t in picked)
    return dict((t.symbol, scores[t.symbol] ** 2 / total * share) for t in picked)


def _allocate_counts(buckets, size):
    """바구니별 목표 비중에 맞춰 종목 수를 나눈다. 후보가 모자라면 남은 자리를
    비중이 큰 다른 바구니로 넘긴다 — 열 자리를 비워 두는 것보다 낫다."""
    counts = {}
    for name, bucket in buckets.items():
        counts[name] = min(int(size * bucket["share"] + 0.5), len(bucket["candidates"]))
    # 목표 비중이 0인 바구니는 자리를 받지 않는다. 비중 0으로 담긴 종목은 표만
    # 채우고 아무 일도 하지 않는다.
    order = [n for n in sorted(buckets, key=lambda n: -buckets[n]["share"])
             if buckets[n]["share"] > 0]
    for name in buckets:
        if buckets[name]["share"] <= 0:
            counts[name] = 0
    while sum(counts.values()) > size:
        for name in reversed(order):
            if counts[name] > 0:
                counts[name] -= 1
                break
    while sum(counts.values()) < size:
        room = [n for n in order if counts[n] < len(buckets[n]["candidates"])]
        if not room:
            break
        counts[room[0]] += 1
    return counts


def _buckets(eligible, composite, us_ratio, etf_ratio):
    """(이름 -> {목표 비중, 후보}) 로 나눈다.

    ETF는 국내에 없으므로 ETF 비중은 미국 비중을 넘을 수 없다. 넘겨받은 값이 크면
    미국 비중까지 깎고, 깎았다는 사실을 호출한 쪽에 돌려준다.
    """
    by = lambda rows: sorted(rows, key=lambda t: -composite[t.symbol])
    if etf_ratio is None:
        return {
            "us": {"share": us_ratio, "candidates": by([t for t in eligible if t.market == "US"])},
            "kr": {"share": 1.0 - us_ratio, "candidates": by([t for t in eligible if t.market == "KR"])},
        }, None
    etf_share = min(etf_ratio, us_ratio)
    return {
        "etf": {"share": etf_share,
                "candidates": by([t for t in eligible
                                  if t.market == "US" and t.kind == "etf"])},
        "us": {"share": us_ratio - etf_share,
               "candidates": by([t for t in eligible
                                 if t.market == "US" and t.kind != "etf"])},
        "kr": {"share": 1.0 - us_ratio,
               "candidates": by([t for t in eligible if t.market == "KR"])},
    }, etf_share


def _apply_sector_cap(weights, sector_of):
    """한 섹터가 상한을 넘지 않도록 눌러 담고, 남은 비중을 여유 있는 종목에 배분."""
    w = dict(weights)
    for _ in range(50):
        totals = {}
        for s, v in w.items():
            totals[sector_of[s]] = totals.get(sector_of[s], 0.0) + v
        over = [sec for sec, v in totals.items()
                if sec not in SECTOR_CAP_EXEMPT and v > SECTOR_CAP + 1e-12]
        if not over:
            break
        excess = 0.0
        for sec in over:
            factor = SECTOR_CAP / totals[sec]
            for s in w:
                if sector_of[s] == sec:
                    excess += w[s] * (1.0 - factor)
                    w[s] *= factor
        room = [s for s in w if sector_of[s] in SECTOR_CAP_EXEMPT
                or totals[sector_of[s]] <= SECTOR_CAP + 1e-12]
        base = sum(w[s] for s in room)
        if not room or base <= 0:
            break
        for s in room:
            w[s] += excess * w[s] / base
    total = sum(w.values())
    if total > 0:
        w = dict((s, v / total) for s, v in w.items())
    return w


def build(tickers, backtest_years, horizon_years, risk_preference, us_ratio,
          etf_ratio=DEFAULT_ETF_RATIO):
    """유니버스 -> (보유 종목 목록, 제외 목록, 프로파일). 비중까지 확정한다."""
    all_scores = factor_scores(tickers)
    profile = profile_for(horizon_years, risk_preference)
    eligible, excluded = screen(tickers, backtest_years)

    composite = {}
    for t in eligible:
        composite[t.symbol] = sum(all_scores[t.symbol][k] * profile[k]
                                  for k, _ in FACTOR_LABELS)

    buckets, etf_share = _buckets(eligible, composite, us_ratio, etf_ratio)
    counts = _allocate_counts(buckets, PORTFOLIO_SIZE)
    for name, bucket in buckets.items():
        bucket["picked"] = bucket["candidates"][:counts[name]]

    # 비어 버린 바구니의 몫은 남은 바구니에 비중대로 나눠 준다
    live = [name for name in buckets if buckets[name]["picked"]]
    spare = sum(buckets[n]["share"] for n in buckets if n not in live)
    if spare > 0 and live:
        base = sum(buckets[n]["share"] for n in live)
        for name in live:
            bucket = buckets[name]
            bucket["share"] += (spare * bucket["share"] / base if base > 0
                                else spare / len(live))

    weights, picked = {}, []
    for name in buckets:
        bucket = buckets[name]
        if bucket["picked"]:
            weights.update(_bucket_weights(bucket["picked"], composite, bucket["share"]))
            picked.extend(bucket["picked"])

    sector_of = dict((t.symbol, t.sector) for t in picked)
    weights = _apply_sector_cap(weights, sector_of)

    holdings = []
    for t in picked:
        factors = {}
        for key, label in FACTOR_LABELS:
            score = all_scores[t.symbol][key]
            factors[key] = {"label": label, "score": round(score, 1),
                            "weight": profile[key],
                            "contribution": round(score * profile[key], 1)}
        holdings.append({
            "symbol": t.symbol, "name": t.name, "market": t.market, "kind": t.kind,
            "sector": t.sector, "currency": t.currency, "weight": weights[t.symbol],
            "price": t.price, "ttm_yield": t.ttm_yield, "ttm_dividend": t.ttm_dividend,
            "div_cagr": t.div_cagr, "div_growth_streak": t.div_growth_streak,
            "div_cut_count_10y": t.div_cut_count_10y,
            "payout_frequency": t.payout_frequency,
            "total_return_cagr": t.total_return_cagr, "volatility": t.volatility,
            "mdd": t.mdd, "history_years": t.history_years,
            "score": composite[t.symbol], "factors": factors,
        })
    holdings.sort(key=lambda h: -h["weight"])
    profile = dict(profile,
                   us_ratio=us_ratio,
                   etf_ratio=etf_ratio,
                   etf_ratio_effective=etf_share,
                   etf_weight=sum(h["weight"] for h in holdings if h["kind"] == "etf"),
                   etf_count=sum(1 for h in holdings if h["kind"] == "etf"))
    return holdings, excluded, profile


def sector_allocation(holdings):
    totals = {}
    for h in holdings:
        totals[h["sector"]] = totals.get(h["sector"], 0.0) + h["weight"]
    return [{"sector": s, "weight": w}
            for s, w in sorted(totals.items(), key=lambda kv: -kv[1])]
