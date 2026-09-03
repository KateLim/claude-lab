"""백테스트(실측)와 전망(가정) 엔진.

- 백테스트: 실제 월말 종가·주당배당금·원달러 환율만 사용한다. 매달 적립액으로
  목표 비중대로 매수하고, 배당은 원천징수 후 재투자하며, 1월에 목표 비중으로
  리밸런싱한다. 전망이 아니라 "같은 전략을 실제로 굴렸다면" 이다.
- 전망: 시작 배당수익률에서 출발해 배당은 배당성장률, 주가는 상한을 씌운
  주가상승률로 자란다고 가정한 결정론적 모형이다.
"""
BUY_FEE = 0.001
HAIRCUT = 0.7                # 과거 성장률에 곱하는 보수적 할인
PRICE_GROWTH_CAP = 0.07      # 주가상승률 상한 (연)
INFLATION = 0.025
REBALANCE_NOTE = "연 1회(1월) 목표 비중 복귀"
ACHIEVE_SEARCH_MONTHS = 600  # 목표 도달 시점은 투자 기간을 넘겨서도 찾아본다


def _max_drawdown(values):
    peak, mdd = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


class Holding(object):
    """백테스트용 종목 뷰 — 원화 환산 가격과 배당을 제공한다."""

    def __init__(self, holding, ticker, ds, after_tax):
        self.symbol = holding["symbol"]
        self.weight = holding["weight"]
        self.is_usd = ticker.market == "US"
        self.tax = ticker.tax if after_tax else 0.0
        self._close = dict(zip(ticker.dates, ticker.closes))
        self._divs = ticker.divs
        self._fx = ds.fx

    def _rate(self, ym):
        return self._fx.get(ym, 1.0) if self.is_usd else 1.0

    def price(self, ym):
        return self._close[ym] * self._rate(ym)

    def dividend_per_share(self, ym):
        return self._divs.get(ym, 0.0) * self._rate(ym)


def backtest(holdings, ds, years, initial_capital, monthly_contribution, drip, after_tax):
    months = ds.months[-(years * 12 + 1):]
    hs = [Holding(h, ds.get(h["symbol"]), ds, after_tax) for h in holdings]
    months = [m for m in months if all(m in h._close for h in hs)]

    shares = dict((h.symbol, 0.0) for h in hs)
    rows, cash_dividends = [], 0.0
    div_history = []
    for i, ym in enumerate(months):
        cash = initial_capital + monthly_contribution if i == 0 else monthly_contribution
        for h in hs:
            shares[h.symbol] += cash * h.weight * (1 - BUY_FEE) / h.price(ym)

        gross, net = 0.0, 0.0
        for h in hs:
            dps = h.dividend_per_share(ym)
            if dps:
                gross += shares[h.symbol] * dps
                net += shares[h.symbol] * dps * (1 - h.tax)
        if drip and net:
            for h in hs:
                shares[h.symbol] += net * h.weight * (1 - BUY_FEE) / h.price(ym)
        else:
            cash_dividends += net

        value = sum(shares[h.symbol] * h.price(ym) for h in hs)
        if ym.endswith("-01"):          # 연 1회 리밸런싱 (수수료 없음)
            for h in hs:
                shares[h.symbol] = value * h.weight / h.price(ym)

        div_history.append(net)
        rows.append({
            "date": ym, "value": value,
            "contributed": initial_capital + monthly_contribution * (i + 1),
            "dividend": net, "dividend_gross": gross,
            "dividend_ttm": sum(div_history[-12:]),
        })

    n = len(rows)
    last12 = sum(div_history[-12:])
    contributed = rows[-1]["contributed"]
    # 재투자를 끄면 배당은 이미 통장으로 나간 돈이므로 평가액에 더하지 않는다.
    # 받은 현금은 cash_dividends로 따로 보고한다.
    final_value = rows[-1]["value"]
    summary = {
        "months": n,
        "final_value": rows[-1]["value"],
        "contributed": contributed,
        "cash_dividends": cash_dividends,
        "monthly_dividend_avg_12m": last12 / 12.0,
        "dividend_total": sum(div_history),
        "yield_on_cost": last12 / contributed if contributed else 0.0,
        "current_yield": last12 / rows[-1]["value"] if rows[-1]["value"] else 0.0,
        "total_return_cagr": (final_value / contributed) ** (12.0 / n) - 1.0 if n else 0.0,
        "mdd": _max_drawdown([r["value"] for r in rows]),
        "period": "%s ~ %s" % (rows[0]["date"], rows[-1]["date"]),
    }
    return {"monthly": rows, "summary": summary}


def assumptions(holdings, after_tax):
    """포트폴리오 비중으로 가중한 전망 가정."""
    start_yield = sum(h["weight"] * h["ttm_yield"] for h in holdings)
    div_growth_raw = sum(h["weight"] * (h["div_cagr"] or 0.0) for h in holdings)
    price_growth_raw = sum(h["weight"] * (h["total_return_cagr"] - h["ttm_yield"])
                           for h in holdings)
    avg_tax = sum(h["weight"] * (0.15 if h["market"] == "US" else 0.154) for h in holdings)
    return {
        "start_yield": start_yield,
        "div_growth_raw": div_growth_raw,
        "div_growth": div_growth_raw * HAIRCUT,
        "price_growth_raw": price_growth_raw,
        "price_growth": min(price_growth_raw * HAIRCUT, PRICE_GROWTH_CAP),
        "haircut": HAIRCUT,
        "price_growth_cap": PRICE_GROWTH_CAP,
        "avg_tax_rate": avg_tax,
        "tax_us": 0.15,
        "tax_kr": 0.154,
        "buy_fee": BUY_FEE,
        "inflation": INFLATION,
        "rebalance": REBALANCE_NOTE,
        "effective_tax_rate": avg_tax if after_tax else 0.0,
    }


def _project(a, initial, monthly, months, drip, target=None):
    """월별 전망을 돌려준다. target을 주면 처음 도달한 달도 함께 찾는다."""
    growth = a["price_growth"] / 12.0
    yield_m = a["start_yield"] / 12.0 * (1 - a["effective_tax_rate"])
    drift = ((1 + a["div_growth"]) / (1 + a["price_growth"])) ** (1.0 / 12.0)
    value, rows, achieve = initial, [], None
    factor = 1.0
    for t in range(1, months + 1):
        value = value * (1 + growth) + monthly
        factor *= drift
        dividend = value * yield_m * factor
        if drip:
            value += dividend
        if target is not None and achieve is None and dividend >= target:
            achieve = t
        rows.append({"month": t, "value": value, "dividend": dividend,
                     "contributed": initial + monthly * t})
    return rows, achieve


def projection(a, initial, monthly, horizon_years, drip, target):
    months = horizon_years * 12
    rows, achieve = _project(a, initial, monthly, months, drip, target)
    if achieve is None:      # 기간 안에 못 닿으면 언제 닿는지까지는 알려준다
        _, achieve = _project(a, initial, monthly, ACHIEVE_SEARCH_MONTHS, drip, target)
    final = rows[-1]
    summary = {
        "final_monthly_dividend": final["dividend"],
        "final_value": final["value"],
        "contributed": final["contributed"],
        "target": target,
        "achieved": final["dividend"] >= target,
        "achieve_month": achieve,
        "shortfall": max(0.0, target - final["dividend"]),
        "achievement_ratio": final["dividend"] / target if target else 0.0,
    }
    return {"monthly": rows, "summary": summary}, achieve


def required_monthly_contribution(a, initial, horizon_years, drip, target):
    """목표 배당을 정확히 채우는 월 적립액 (이분 탐색)."""
    months = horizon_years * 12

    def final_div(c):
        rows, _ = _project(a, initial, c, months, drip)
        return rows[-1]["dividend"]

    if final_div(0.0) >= target:
        return 0.0
    lo, hi = 0.0, 1e9
    if final_div(hi) < target:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if final_div(mid) < target:
            lo = mid
        else:
            hi = mid
    return lo


def achieve_text(months):
    if months is None:
        return None
    return "%d년 %d개월 후" % (months // 12, months % 12)
