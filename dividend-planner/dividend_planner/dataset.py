"""데이터 팩 로딩과 종목 지표 계산.

모든 지표는 데이터 팩(data/market.json) 안의 월말 종가·월별 주당배당금만으로 계산한다.
환율은 종목 지표에 쓰지 않는다 — 수익률·배당수익률은 현지 통화 기준으로 계산해도
환율 상수배가 상쇄되기 때문이고, 환율은 여러 통화를 섞는 백테스트에서만 필요하다.

데이터 팩은 캐시다. 서버가 뜰 때 읽고, 오래됐으면 공급자에서 새로 받아 갈아 끼운다
(refresh.py). 교체는 통째로 새 Dataset을 만들어 원자적으로 바꾸므로, 갱신 중에 들어온
요청도 일관된 스냅샷을 본다.
"""
import json
import math
import os
import statistics
import threading
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# market.json 은 갱신되는 캐시라 git 에서 제외한다. 저장소에는 seed 만 넣어 두고,
# 캐시가 없으면 seed 를 복사해서 시작한다 — 새로 클론한 머신에서도 첫 실행이 그냥 된다.
DATA_PATH = os.path.join(_ROOT, "data", "market.json")
SEED_PATH = os.path.join(_ROOT, "data", "market.seed.json")

TAX = {"US": 0.15, "KR": 0.154}
TR_WINDOW_MONTHS = 120          # 총수익 CAGR / MDD 산출 창 (10년)
MIN_YIELD = 0.01                # 배당 목적 최소 배당수익률
MAX_BACKTEST_YEARS = 15         # 화면 슬라이더 상한
MIN_USABLE_TICKERS = 10         # 포트폴리오 한 벌(10종목)을 채울 수 있는 기간까지만 허용
CUT_THRESHOLD = 0.95            # 전년 대비 5% 이상 줄어든 해만 "삭감"으로 센다
_DAYS_PER_YEAR = 365.25


def _month_end(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 12:
        return date(y, 12, 31)
    return date(y, m + 1, 1).fromordinal(date(y, m + 1, 1).toordinal() - 1)


def _parse_day(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def _max_drawdown(series):
    peak, mdd = None, 0.0
    for v in series:
        peak = v if peak is None or v > peak else peak
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def _total_return_index(dates, closes, divs, months=None):
    """배당 재투자 총수익 지수. months가 주어지면 최근 구간만 사용한다."""
    if months is not None and len(dates) > months + 1:
        dates, closes = dates[-(months + 1):], closes[-(months + 1):]
    shares, idx = 1.0, []
    for i, dt in enumerate(dates):
        if i > 0:
            dps = divs.get(dt, 0.0)
            if dps and closes[i] > 0:
                shares += shares * dps / closes[i]
        idx.append(shares * closes[i])
    return dates, idx


class Ticker(object):
    """한 종목의 원시 시계열 + 파생 지표."""

    def __init__(self, raw, as_of):
        self.symbol = raw["symbol"]
        self.name = raw["name"]
        self.market = raw["market"]
        self.kind = raw["kind"]
        self.sector = raw["sector"]
        self.first_date = raw["first_date"]
        self.price_source = raw.get("price_source", "bundle")
        self.dividend_source = raw.get("dividend_source", "bundle")
        self.dates = [p[0] for p in raw["prices"]]
        self.closes = [p[1] for p in raw["prices"]]
        self.divs = dict((d[0], d[1]) for d in raw["dividends"])
        self.tax = TAX[self.market]
        self.currency = "USD" if self.market == "US" else "KRW"
        self._derive(as_of)

    # ------------------------------------------------------------------ 지표
    def _derive(self, as_of):
        self.price = self.closes[-1]
        self.ttm_dividend = sum(self.divs.get(d, 0.0) for d in self.dates[-12:])
        self.ttm_yield = self.ttm_dividend / self.price if self.price else 0.0

        # 연도별 배당. 무배당 연도도 0으로 채워 연속 축을 만든다 — 그래야 배당을
        # 건너뛴 해가 성장률·삭감 집계에서 사라지지 않는다. 현재 연도는 아직
        # 미완결이므로 성장률·연속증가 계산에서 제외한다.
        this_year = int(as_of[:4])
        annual = {}
        for y in range(int(self.dates[0][:4]), this_year + 1):
            annual[y] = 0.0
        for ym, dps in self.divs.items():
            annual[int(ym[:4])] = annual.get(int(ym[:4]), 0.0) + dps
        self.annual_dividends = annual
        done = sorted(y for y in annual if y < this_year)
        self.completed_years = done

        def cagr_n(n):
            if len(done) < n + 1:
                return None
            return _cagr(annual[done[-1 - n]], annual[done[-1]], n)

        self.div_cagr_5y = cagr_n(5)
        self.div_cagr_10y = cagr_n(10)
        self.div_cagr = self.div_cagr_5y if self.div_cagr_5y is not None else self.div_cagr_10y

        # 연속증가는 "줄지 않았다"(>=) 기준. 월배당주는 달력상 지급 횟수가 11/12회로
        # 흔들려 연간 합계가 동일해지는 해가 생기는데, 그걸 증가 중단으로 보면
        # 실제 배당정책과 어긋난다.
        streak = 0
        for i in range(len(done) - 1, 0, -1):
            if annual[done[i]] >= annual[done[i - 1]]:
                streak += 1
            else:
                break
        self.div_growth_streak = streak
        self.div_cut_count_10y = sum(
            1 for i in range(max(1, len(done) - 10), len(done))
            if annual[done[i]] < annual[done[i - 1]] * CUT_THRESHOLD)

        per_year = {}
        for ym in self.divs:
            per_year[int(ym[:4])] = per_year.get(int(ym[:4]), 0) + 1
        counts = [per_year[y] for y in done if y in per_year]
        self.payout_frequency = float(statistics.median(counts)) if counts else 0.0

        _, tr = _total_return_index(self.dates, self.closes, self.divs, TR_WINDOW_MONTHS)
        self.total_return_cagr = _cagr(tr[0], tr[-1], (len(tr) - 1) / 12.0) or 0.0
        self.mdd = _max_drawdown(tr)

        rets = [self.closes[i] / self.closes[i - 1] - 1.0 for i in range(1, len(self.closes))]
        self.volatility = statistics.stdev(rets) * math.sqrt(12) if len(rets) > 1 else 0.0
        self.history_years = round(
            (_month_end(as_of) - _parse_day(self.first_date)).days / _DAYS_PER_YEAR, 1)
        # 실제로 들고 있는 월 데이터의 길이. 상장은 오래됐어도 데이터가 10년치뿐이면
        # 15년 백테스트에는 못 쓴다 — 둘 중 짧은 쪽이 이 종목의 유효 이력이다.
        self.data_months = len(self.dates)
        self.data_years = round(self.data_months / 12.0, 1)
        self.effective_history_years = min(self.history_years, self.data_years)

    # ------------------------------------------------------------ 직렬화
    def metrics(self):
        return {
            "symbol": self.symbol, "name": self.name, "market": self.market,
            "kind": self.kind, "sector": self.sector, "currency": self.currency,
            "price": self.price, "ttm_dividend": self.ttm_dividend, "ttm_yield": self.ttm_yield,
            "div_cagr_5y": self.div_cagr_5y, "div_cagr_10y": self.div_cagr_10y,
            "div_cagr": self.div_cagr, "div_growth_streak": self.div_growth_streak,
            "div_cut_count_10y": self.div_cut_count_10y,
            "payout_frequency": self.payout_frequency,
            "total_return_cagr": self.total_return_cagr, "volatility": self.volatility,
            "mdd": self.mdd, "history_years": self.history_years, "first_date": self.first_date,
            "data_months": self.data_months, "data_years": self.data_years,
            "effective_history_years": self.effective_history_years,
            "price_source": self.price_source, "dividend_source": self.dividend_source,
        }

    def price_series(self):
        return [{"date": d, "close": c} for d, c in zip(self.dates, self.closes)]

    def dividend_annual(self):
        return [{"year": y, "dividend": self.annual_dividends[y]}
                for y in sorted(self.annual_dividends)]

    def dividend_monthly(self):
        return [{"date": d, "dividend": self.divs[d]} for d in sorted(self.divs)]


class Dataset(object):
    """한 시점의 유니버스 스냅샷. 만들어진 뒤에는 바뀌지 않는다."""

    def __init__(self, raw):
        self.raw = raw
        self.as_of = raw["as_of"]
        self.fetched_at = raw.get("fetched_at")
        self.sources = raw.get("sources") or {}
        # 절대 환율(예: 1362.56)이면 화면에 원화 환산까지 보여줄 수 있다. 예전 팩은
        # 첫 달을 1.0으로 둔 지수만 갖고 있어 상대 변화만 쓴다.
        rows = raw.get("fx_usdkrw") or raw.get("fx_usdkrw_index")
        if not rows:
            raise ValueError("데이터 팩에 원달러 환율 시계열이 없습니다")
        self.fx_absolute = bool(raw.get("fx_absolute", "fx_usdkrw" in raw))
        self.fx = dict((d, v) for d, v in rows)
        self.fx_months = sorted(self.fx)
        self.fx_rate = self.fx[self.fx_months[-1]] if self.fx_absolute else None
        self.tickers = [Ticker(t, self.as_of) for t in raw["tickers"]]
        self.by_symbol = dict((t.symbol, t) for t in self.tickers)
        # 모든 종목이 공유하는 월 축 (가장 긴 시계열)
        self.months = sorted(set(d for t in self.tickers for d in t.dates))
        self.max_backtest_years = self._max_backtest_years()

    def _max_backtest_years(self):
        """포트폴리오를 채울 만큼 종목이 남는 최장 기간. 공급자가 10년치만 주면
        15년 백테스트는 몇 종목만 남겨 의미가 없어지므로 슬라이더 자체를 줄인다."""
        fx_years = len(self.fx_months) // 12
        for years in range(MAX_BACKTEST_YEARS, 3, -1):
            if years > fx_years:
                continue
            usable = sum(1 for t in self.tickers if t.effective_history_years >= years)
            if usable >= MIN_USABLE_TICKERS:
                return years
        return 3

    def get(self, symbol):
        return self.by_symbol.get(symbol)

    def stale(self, now_ym=None):
        """데이터 기준월이 이번 달보다 과거면 오래된 것으로 본다."""
        now = now_ym or date.today().strftime("%Y-%m")
        return self.as_of < now

    def provenance(self):
        prices, dividends = {}, {}
        for t in self.tickers:
            prices[t.price_source] = prices.get(t.price_source, 0) + 1
            dividends[t.dividend_source] = dividends.get(t.dividend_source, 0) + 1
        return {"fx": self.sources.get("fx", "bundle"), "fx_absolute": self.fx_absolute,
                "fx_rate": self.fx_rate, "prices": prices, "dividends": dividends}


_LOCK = threading.Lock()
_DATASET = None


def load_pack(path=DATA_PATH):
    if not os.path.exists(path) and path == DATA_PATH and os.path.exists(SEED_PATH):
        with open(SEED_PATH, encoding="utf-8") as src:
            raw = src.read()
        with open(path, "w", encoding="utf-8") as dst:
            dst.write(raw)
        return json.loads(raw)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dataset():
    """현재 스냅샷. 없으면 캐시 파일에서 만든다."""
    global _DATASET
    with _LOCK:
        if _DATASET is None:
            _DATASET = Dataset(load_pack())
        return _DATASET


def replace(pack):
    """새 팩으로 스냅샷을 통째로 교체한다. 실패하면 기존 것을 그대로 둔다."""
    global _DATASET
    built = Dataset(pack)          # 락 밖에서 만들어 두고
    with _LOCK:                    # 교체만 락 안에서 (읽는 쪽이 멈추지 않는다)
        _DATASET = built
    return built
