"""시장 데이터 공급자.

한 곳이 막히면 다음 곳으로 넘어간다. 어느 공급자에서 어떤 필드를 받았는지 기록해
화면에 그대로 표시한다 — 값의 출처를 감추면 숫자를 믿을 근거가 없어진다.

공급자별로 주는 것이 다르다.
  yahoo         월말 종가 + 월별 배당, 미국·국내 모두, 한 번 호출로 끝난다 (1순위)
  alphavantage  월말 종가 + 월별 배당, 미국만, API 키 필요 (ALPHAVANTAGE_API_KEY)
  nasdaq        일별 종가 -> 월말로 접는다, 미국만, 최근 10년까지
  naver         월별 OHLC, 국내만, 16년치
  frankfurter   원달러 환율 (ECB 기준), 실제 환율 수준까지 받는다
  stooq         프록시 오브 워크(JS) 관문이 있어 쓰지 않는다

배당 이력을 주는 공급자에 닿지 못하면 캐시에 있던 배당 이력을 그대로 쓴다.
가격은 최신으로, 배당은 스냅샷 기준 — 이 상태도 화면에 밝힌다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 30
RETRIES = 2
RETRY_SLEEP = 1.0

# 엔드포인트 주소는 모듈 변수로 둔다 — 테스트에서 가짜 서버로 갈아 끼울 수 있어야
# 네트워크 없이도 파서와 폴백 순서를 검증할 수 있다.
YAHOO_BASE = "https://query1.finance.yahoo.com"
ALPHAVANTAGE_BASE = "https://www.alphavantage.co"
NASDAQ_BASE = "https://api.nasdaq.com"
NAVER_BASE = "https://api.finance.naver.com"
FRANKFURTER_BASE = "https://api.frankfurter.dev"


class ProviderError(Exception):
    """이 공급자로는 못 받았다는 뜻. 다음 공급자로 넘어간다."""


def _fetch(url, accept="application/json"):
    last = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                return res.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP * (attempt + 1))
    raise ProviderError(str(last))


def _json(url):
    body = _fetch(url)
    try:
        return json.loads(body)
    except ValueError:
        raise ProviderError("JSON이 아닌 응답 (%s...)" % body[:60].replace("\n", " "))


def _ym(ts):
    t = time.gmtime(ts)
    return "%04d-%02d" % (t.tm_year, t.tm_mon)


def _month_end_only(daily):
    """[(YYYY-MM-DD, value)] -> [[YYYY-MM, 그 달 마지막 값]] (날짜 오름차순 가정 아님)."""
    last = {}
    for date, value in sorted(daily):
        last[date[:7]] = value
    return [[ym, last[ym]] for ym in sorted(last)]


# ----------------------------------------------------------------------- 환율
def frankfurter_fx(start, end):
    """ECB 기준 원달러 환율. 실제 환율 수준(예: 1362.56)을 그대로 돌려준다."""
    url = ("%s/v1/%s..%s?base=USD&symbols=KRW" % (FRANKFURTER_BASE, start, end))
    data = _json(url)
    rates = data.get("rates") or {}
    daily = [(d, v["KRW"]) for d, v in rates.items() if "KRW" in v]
    if len(daily) < 24:
        raise ProviderError("환율 데이터가 너무 짧습니다 (%d일)" % len(daily))
    return _month_end_only(daily)


def yahoo_fx(start, end):
    prices, _, _ = yahoo("KRW=X")
    return prices


# --------------------------------------------------------------- 종가 + 배당
def yahoo(symbol):
    """월말 종가와 월별 배당을 한 번에. 1순위 공급자."""
    url = ("%s/v8/finance/chart/%s?range=20y&interval=1mo&events=div"
           % (YAHOO_BASE, urllib.parse.quote(symbol)))
    payload = _json(url)
    error = (payload.get("chart") or {}).get("error")
    if error:
        raise ProviderError(str(error))
    result = (payload.get("chart") or {}).get("result")
    if not result:
        raise ProviderError("빈 응답")
    result = result[0]
    closes = result["indicators"]["quote"][0]["close"]
    monthly = {}
    for ts, close in zip(result["timestamp"], closes):
        if close is not None:
            monthly[_ym(ts)] = float(close)
    if len(monthly) < 36:
        raise ProviderError("종가가 너무 짧습니다 (%d개월)" % len(monthly))

    divs = {}
    for row in ((result.get("events") or {}).get("dividends") or {}).values():
        amount = float(row["amount"])
        if amount > 0:
            ym = _ym(row["date"])
            divs[ym] = divs.get(ym, 0.0) + amount

    first = (result.get("meta") or {}).get("firstTradeDate")
    first_date = (time.strftime("%Y-%m-%d", time.gmtime(first)) if first
                  else min(monthly) + "-01")
    return ([[ym, monthly[ym]] for ym in sorted(monthly)],
            [[ym, divs[ym]] for ym in sorted(divs)], first_date)


def alphavantage(symbol, api_key=None):
    """월말 종가 + 월별 배당. 미국 종목만. 무료 키는 하루 호출 수가 적다."""
    key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        raise ProviderError("ALPHAVANTAGE_API_KEY가 없습니다")
    url = ("%s/query?function=TIME_SERIES_MONTHLY_ADJUSTED&symbol=%s&outputsize=full"
           "&apikey=%s" % (ALPHAVANTAGE_BASE, urllib.parse.quote(symbol), key))
    payload = _json(url)
    series = payload.get("Monthly Adjusted Time Series")
    if not series:
        raise ProviderError(payload.get("Note") or payload.get("Information")
                            or payload.get("Error Message") or "시계열이 없습니다")
    prices, divs = [], []
    for date in sorted(series):
        row = series[date]
        prices.append([date[:7], float(row["4. close"])])
        amount = float(row.get("7. dividend amount", 0) or 0)
        if amount > 0:
            divs.append([date[:7], amount])
    if len(prices) < 36:
        raise ProviderError("종가가 너무 짧습니다 (%d개월)" % len(prices))
    return prices, divs, prices[0][0] + "-01"


# ------------------------------------------------------------------ 종가만
def nasdaq(symbol, kind="stock"):
    """일별 종가를 월말로 접는다. 미국 종목, 최근 10년까지. 배당은 주지 않는다."""
    today = time.strftime("%Y-%m-%d")
    start = "%d-01-01" % (int(today[:4]) - 20)
    url = ("%s/api/quote/%s/historical?assetclass=%s&fromdate=%s&todate=%s&limit=9999"
           % (NASDAQ_BASE, urllib.parse.quote(symbol),
              "etf" if kind == "etf" else "stocks", start, today))
    payload = _json(url)
    table = ((payload.get("data") or {}).get("tradesTable") or {})
    rows = table.get("rows")
    if not rows:
        raise ProviderError((payload.get("message") or "가격 이력이 없습니다"))
    daily = []
    for row in rows:
        month, day, year = row["date"].split("/")
        value = float(row["close"].replace("$", "").replace(",", ""))
        daily.append(("%s-%s-%s" % (year, month, day), value))
    prices = _month_end_only(daily)
    if len(prices) < 36:
        raise ProviderError("종가가 너무 짧습니다 (%d개월)" % len(prices))
    return prices, None, prices[0][0] + "-01"


_NAVER_ROW = re.compile(r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)')


def naver(symbol):
    """국내 종목 월별 종가. 16년치가 한 번에 온다. 배당은 주지 않는다."""
    code = symbol.split(".")[0]
    today = time.strftime("%Y%m%d")
    url = ("%s/siseJson.naver?symbol=%s&requestType=1&startTime=%d0101&endTime=%s"
           "&timeframe=month" % (NAVER_BASE, code, int(today[:4]) - 20, today))
    body = _fetch(url, accept="text/plain")
    prices = {}
    for match in _NAVER_ROW.finditer(body):
        date, close = match.group(1), float(match.group(5))
        prices["%s-%s" % (date[:4], date[4:6])] = close
    if len(prices) < 36:
        raise ProviderError("종가가 너무 짧습니다 (%d개월)" % len(prices))
    ordered = [[ym, prices[ym]] for ym in sorted(prices)]
    return ordered, None, ordered[0][0] + "-01"


# ------------------------------------------------------------------- 체인
def price_chain(market):
    """시장별 공급자 순서. 앞에 있는 것이 배당까지 주므로 되면 그걸로 끝난다."""
    if market == "US":
        return [("yahoo", lambda t: yahoo(t["symbol"])),
                ("alphavantage", lambda t: alphavantage(t["symbol"])),
                ("nasdaq", lambda t: nasdaq(t["symbol"], t.get("kind")))]
    return [("yahoo", lambda t: yahoo(t["symbol"])),
            ("naver", lambda t: naver(t["symbol"]))]


def fx_chain():
    return [("frankfurter", frankfurter_fx), ("yahoo", yahoo_fx)]
