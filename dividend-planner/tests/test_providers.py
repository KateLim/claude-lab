#!/usr/bin/env python3
"""공급자 파서와 갱신 로직 검증. 가짜 서버를 띄워 네트워크 없이 결정적으로 돈다.

검증하는 것은 세 가지다.
  · 각 공급자의 응답을 우리가 쓰는 모양으로 정확히 옮기는가
  · 앞 공급자가 막히면 다음으로 넘어가고, 계속 막히면 그만 두드리는가
  · 새 데이터와 캐시를 합칠 때 이력이 늘어나되 이음매가 튀지 않는가
"""
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dividend_planner import providers, refresh                     # noqa: E402
from dividend_planner.dataset import Dataset                        # noqa: E402

CALLS = []                      # 어떤 경로를 몇 번 두드렸는지
FAIL = set()                    # 이 접두어로 시작하는 경로는 429로 막는다


def _months(count, start_year=2016, start_month=9, price=100.0):
    out, y, m = [], start_year, start_month
    for i in range(count):
        out.append(("%04d-%02d" % (y, m), price + i))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _epoch(ym, day=28):
    return int(time.mktime(time.strptime("%s-%02d" % (ym, day), "%Y-%m-%d")))


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path
        CALLS.append(path)
        for prefix in FAIL:
            if path.startswith(prefix):
                return self._send(429, "Too Many Requests", "text/plain")

        if path.startswith("/v1/"):                       # frankfurter
            rates = {}
            for ym, _ in _months(30, 2024, 1):
                for day in (14, 28):
                    rates["%s-%02d" % (ym, day)] = {"KRW": 1300.0 + day}
            return self._send(200, json.dumps({"base": "USD", "rates": rates}))

        if path.startswith("/v8/finance/chart/"):         # yahoo
            months = _months(40)
            return self._send(200, json.dumps({"chart": {"result": [{
                "meta": {"firstTradeDate": _epoch("1998-03", 12)},
                "timestamp": [_epoch(ym) for ym, _ in months],
                "indicators": {"quote": [{"close": [v for _, v in months]}]},
                "events": {"dividends": {
                    "1": {"amount": 0.5, "date": _epoch(months[0][0])},
                    "2": {"amount": 0.6, "date": _epoch(months[3][0])}}},
            }], "error": None}}))

        if path.startswith("/api/quote/"):                # nasdaq
            months = _months(40)
            rows = [{"date": "%s/%s/%s" % (ym[5:7], "28", ym[:4]), "close": "$%.2f" % v}
                    for ym, v in reversed(months)]
            return self._send(200, json.dumps({"data": {
                "symbol": "X", "totalRecords": len(rows),
                "tradesTable": {"rows": rows}}}))

        if path.startswith("/siseJson.naver"):            # naver
            months = _months(40, 2010, 1, 30000.0)
            body = " [['날짜','시가','고가','저가','종가','거래량','외국인소진율'],\n"
            for ym, v in months:
                body += '["%s%s28", %.1f, %.1f, %.1f, %.1f, 1000, 50.0],\n' % (
                    ym[:4], ym[5:7], v, v, v, v)
            return self._send(200, body + "]", "text/plain")

        if path.startswith("/query?"):                    # alphavantage
            months = _months(40)
            series = dict(("%s-28" % ym, {"4. close": "%.4f" % v,
                                          "7. dividend amount": "0.2500" if i % 3 == 0 else "0.0000"})
                          for i, (ym, v) in enumerate(months))
            return self._send(200, json.dumps({"Monthly Adjusted Time Series": series}))

        return self._send(404, "{}")


class ProviderBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.saved = {k: getattr(providers, k) for k in
                     ("YAHOO_BASE", "NASDAQ_BASE", "NAVER_BASE", "FRANKFURTER_BASE",
                      "ALPHAVANTAGE_BASE", "RETRY_SLEEP")}
        for key in ("YAHOO_BASE", "NASDAQ_BASE", "NAVER_BASE", "FRANKFURTER_BASE",
                    "ALPHAVANTAGE_BASE"):
            setattr(providers, key, cls.base)
        providers.RETRY_SLEEP = 0.0          # 테스트에서 재시도 대기는 의미가 없다

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.saved.items():
            setattr(providers, key, value)
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        del CALLS[:]
        FAIL.clear()


class Parsers(ProviderBase):
    def test_frankfurter_returns_month_end_absolute_rates(self):
        rows = providers.frankfurter_fx("2024-01-01", "2026-06-30")
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0][0], "2024-01")
        # 같은 달에 14일·28일 값이 오면 마지막 값만 남아야 한다
        self.assertAlmostEqual(rows[0][1], 1328.0, delta=1e-9)

    def test_yahoo_returns_prices_dividends_and_listing_date(self):
        prices, divs, first = providers.yahoo("KO")
        self.assertEqual(len(prices), 40)
        self.assertEqual(prices[0][0], "2016-09")
        self.assertEqual(len(divs), 2)
        self.assertAlmostEqual(divs[0][1], 0.5)
        self.assertTrue(first.startswith("1998-03"), first)

    def test_nasdaq_folds_daily_into_month_end_and_has_no_dividends(self):
        prices, divs, _ = providers.nasdaq("KO", "stock")
        self.assertEqual(len(prices), 40)
        self.assertIsNone(divs)
        self.assertEqual(prices[0], ["2016-09", 100.0])

    def test_naver_parses_korean_monthly_rows(self):
        prices, divs, _ = providers.naver("086790.KS")
        self.assertEqual(len(prices), 40)
        self.assertIsNone(divs)
        self.assertEqual(prices[0], ["2010-01", 30000.0])

    def test_alphavantage_reads_monthly_dividend_column(self):
        prices, divs, _ = providers.alphavantage("KO", api_key="k")
        self.assertEqual(len(prices), 40)
        self.assertEqual(len(divs), 14)
        self.assertAlmostEqual(divs[0][1], 0.25)

    def test_alphavantage_without_key_is_skipped(self):
        saved = os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        try:
            with self.assertRaises(providers.ProviderError):
                providers.alphavantage("KO")
        finally:
            if saved:
                os.environ["ALPHAVANTAGE_API_KEY"] = saved

    def test_blocked_provider_raises_after_retries(self):
        FAIL.add("/v8/finance/chart/")
        with self.assertRaises(providers.ProviderError):
            providers.yahoo("KO")
        self.assertEqual(len(CALLS), providers.RETRIES + 1)


class PackIntegrity(unittest.TestCase):
    def test_dataset_rejects_a_pack_without_fx(self):
        with self.assertRaises(ValueError):
            Dataset({"as_of": "2026-09", "tickers": []})


class Stitch(unittest.TestCase):
    def test_extends_history_when_overlap_agrees(self):
        live = [["2016-%02d" % m, 100.0 + m] for m in range(1, 13)]
        cache = [["2010-01", 50.0]] + [["2016-%02d" % m, 100.0 + m + 0.3] for m in range(1, 13)]
        merged, extended = refresh.stitch_prices(live, cache)
        self.assertTrue(extended)
        self.assertEqual(merged[0], ["2010-01", 50.0])
        self.assertEqual(len(merged), 13)

    def test_refuses_to_stitch_across_different_price_bases(self):
        live = [["2016-%02d" % m, 100.0] for m in range(1, 13)]
        cache = [["2010-01", 50.0]] + [["2016-%02d" % m, 400.0] for m in range(1, 13)]
        merged, extended = refresh.stitch_prices(live, cache)
        self.assertFalse(extended)
        self.assertEqual(len(merged), 12)

    def test_no_overlap_means_no_stitch(self):
        live = [["2016-%02d" % m, 100.0] for m in range(1, 13)]
        cache = [["2010-%02d" % m, 50.0] for m in range(1, 4)]
        merged, extended = refresh.stitch_prices(live, cache)
        self.assertFalse(extended)

    def test_dividends_merge_with_live_winning_overlap(self):
        merged, changed = refresh.stitch_dividends(
            [["2016-01", 1.0]], [["2010-01", 0.5], ["2016-01", 0.9]])
        self.assertTrue(changed)
        self.assertEqual(merged, [["2010-01", 0.5], ["2016-01", 1.0]])


class RefreshFlow(ProviderBase):
    def _pack(self):
        return {"as_of": "2019-12", "fx_absolute": False,
                "fx_usdkrw_index": [[ym, 1.0] for ym, _ in _months(120, 2010, 1)],
                "tickers": [{"symbol": "SCHD", "name": "x", "market": "US", "kind": "etf",
                             "sector": "ETF", "first_date": "2011-10-20",
                             "prices": _months(60, 2012, 1, 40.0),
                             "dividends": [["2012-03", 0.2], ["2013-03", 0.3]]}]}

    def test_yahoo_path_gives_everything(self):
        pack, report = refresh.refresh(self._pack(), only={"SCHD"})
        row = [t for t in report["tickers"] if t["symbol"] == "SCHD"][0]
        self.assertTrue(row["price_source"].startswith("yahoo"), row["price_source"])
        self.assertTrue(row["dividend_source"].startswith("yahoo"), row["dividend_source"])
        self.assertTrue(pack["fx_absolute"])
        self.assertEqual(report["fx"]["source"], "frankfurter")
        self.assertGreater(report["fx"]["latest"], 1000)

    def test_falls_back_and_keeps_cached_dividends(self):
        FAIL.add("/v8/finance/chart/")     # 야후 차단
        FAIL.add("/query?")                # 알파밴티지도 차단
        pack, report = refresh.refresh(self._pack(), only={"SCHD"})
        row = [t for t in report["tickers"] if t["symbol"] == "SCHD"][0]
        self.assertTrue(row["price_source"].startswith("nasdaq"), row["price_source"])
        self.assertEqual(row["dividend_source"], "cache")
        ticker = pack["tickers"][0]
        self.assertEqual(ticker["dividends"], self._pack()["tickers"][0]["dividends"])

    def test_gives_up_on_a_provider_that_keeps_failing(self):
        FAIL.add("/v8/finance/chart/")
        refresh.refresh(self._pack(), only=None)
        yahoo_calls = len([c for c in CALLS if c.startswith("/v8/")])
        # 재시도 포함해도 GIVE_UP_AFTER번의 시도로 끝나야 한다 (36종목 x 3회가 아니라)
        self.assertLessEqual(yahoo_calls, refresh.GIVE_UP_AFTER * (providers.RETRIES + 1))

    def test_report_lists_failures_and_keeps_cache(self):
        for prefix in ("/v8/finance/chart/", "/query?", "/api/quote/", "/siseJson.naver"):
            FAIL.add(prefix)
        pack, report = refresh.refresh(self._pack(), only={"SCHD"})
        self.assertEqual([f["symbol"] for f in report["failed"]], ["SCHD"])
        self.assertEqual(report["failed"][0]["kept"], "cache")
        self.assertEqual(pack["tickers"][0]["prices"],
                         self._pack()["tickers"][0]["prices"])

    def test_new_pack_loads_as_a_dataset(self):
        pack, _ = refresh.refresh(self._pack(), only={"SCHD"})
        ds = Dataset(pack)
        self.assertTrue(ds.fx_absolute)
        self.assertIsNotNone(ds.fx_rate)
        self.assertEqual(len(ds.tickers), 1)
        self.assertEqual(ds.provenance()["fx"], "frankfurter")

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        pack, _ = refresh.refresh(self._pack(), only={"SCHD"})
        target = os.path.join(ROOT, "tests", "_tmp_pack.json")
        try:
            refresh.save(pack, target)
            self.assertTrue(os.path.isfile(target))
            self.assertFalse(os.path.exists(target + ".tmp"))
            with open(target, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["as_of"], pack["as_of"])
        finally:
            for path in (target, target + ".tmp"):
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
