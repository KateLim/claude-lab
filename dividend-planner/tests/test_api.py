#!/usr/bin/env python3
"""로컬 서버를 실제로 띄워 HTTP로 두드려 보는 스모크 테스트."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server                                                    # noqa: E402
from dividend_planner import dataset as data_store               # noqa: E402
from tests.test_providers import Fake, FAIL, CALLS               # noqa: E402

PLAN = {
    "target_monthly_dividend_krw": 4806779, "initial_capital_krw": 50000000,
    "monthly_contribution_krw": 2000000, "horizon_years": 25,
    "risk_preference": "balanced", "us_ratio": 0.75, "drip": True,
    "after_tax": True, "backtest_years": 10,
}


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def post(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_health(self):
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["tickers"], 36)
        self.assertIn(body["narrative_engine"], ("local", "claude"))

    def test_universe(self):
        _, body = self.get("/api/universe")
        self.assertEqual(body["count"], 36)
        self.assertIn("ttm_yield", body["tickers"][0])

    def test_ticker_detail_and_404(self):
        _, body = self.get("/api/tickers/086790.KS")
        self.assertEqual(body["metrics"]["name"], "하나금융지주")
        self.assertGreater(len(body["price_series"]), 100)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/tickers/NOPE")
        self.assertEqual(ctx.exception.code, 404)

    def test_goal(self):
        _, body = self.post("/api/goal", {
            "age": 35, "retire_age": 60, "monthly_income_after_tax_krw": 5000000,
            "monthly_spending_krw": 3000000, "current_assets_krw": 50000000,
            "pension_monthly_krw": 1200000})
        self.assertEqual(body["monthly_contribution_krw"], 2000000)
        self.assertEqual(body["scenarios"]["adequate"]["target_monthly_dividend"], 4806779)

    def test_goal_rejects_bad_ages(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/goal", {"age": 60, "retire_age": 50})
        self.assertEqual(ctx.exception.code, 400)

    def test_plan_shape_and_cache(self):
        server._plan_cache.clear()      # 다른 테스트가 이미 채워 놨을 수 있다
        _, body = self.post("/api/plan", PLAN)
        self.assertFalse(body["cached"])
        self.assertEqual(len(body["portfolio"]), 10)
        self.assertAlmostEqual(sum(h["weight"] for h in body["portfolio"]), 1.0, delta=1e-9)
        self.assertEqual(len(body["projection"]["monthly"]), 300)
        self.assertEqual(len(body["backtest"]["monthly"]), 121)
        self.assertTrue(body["diagnosis"]["achieve_text"].endswith("후"))
        _, again = self.post("/api/plan", PLAN)
        self.assertTrue(again["cached"])

    def test_plan_clamps_out_of_range_input(self):
        _, body = self.post("/api/plan", dict(PLAN, horizon_years=999, backtest_years=99,
                                              us_ratio=5, etf_ratio=-1,
                                              risk_preference="unknown"))
        r = body["request"]
        self.assertEqual(r["horizon_years"], 50)
        self.assertEqual(r["backtest_years"], 15)
        self.assertEqual(r["us_ratio"], 1.0)
        self.assertEqual(r["etf_ratio"], 0.0)
        self.assertEqual(r["risk_preference"], "balanced")

    def test_plan_defaults_to_an_etf_core(self):
        body = self.post("/api/plan", dict(PLAN))[1]
        wp = body["weight_profile"]
        self.assertEqual(body["request"]["etf_ratio"], 0.6)
        self.assertGreater(wp["etf_weight"], 0.5)
        etf_names = [h["name"] for h in body["portfolio"] if h["kind"] == "etf"]
        self.assertGreaterEqual(len(etf_names), 5, etf_names)

    def test_plan_honours_a_requested_etf_ratio(self):
        low = self.post("/api/plan", dict(PLAN, etf_ratio=0.2))[1]["weight_profile"]
        high = self.post("/api/plan", dict(PLAN, etf_ratio=0.9))[1]["weight_profile"]
        self.assertLess(low["etf_weight"], high["etf_weight"])
        self.assertAlmostEqual(low["etf_weight"], 0.2, delta=0.05)

    def test_ai_strategy_streams_sse(self):
        plan = self.post("/api/plan", PLAN)[1]
        req = urllib.request.Request(
            self.base + "/api/ai/strategy",
            data=json.dumps(plan).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as res:
            self.assertTrue(res.headers["Content-Type"].startswith("text/event-stream"))
            raw = res.read().decode("utf-8")
        events = [b for b in raw.split("\n\n") if b.strip()]
        names = [b.split("\n")[0].replace("event: ", "") for b in events]
        self.assertEqual(names[0], "phase")
        self.assertEqual(names[-1], "final")
        text = "".join(json.loads(b.split("data: ")[1])["text"]
                       for b in events if b.startswith("event: delta"))
        self.assertIn("## 이 전략의 핵심", text)
        self.assertIn("투자 권유가 아닙니다", text)

    def test_ai_ticker_requires_holding(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/ai/tickers/MAIN", {})
        self.assertEqual(ctx.exception.code, 400)

    def test_health_reports_data_provenance(self):
        _, body = self.get("/api/health")
        prov = body["provenance"]
        self.assertIn("prices", prov)
        self.assertIn("dividends", prov)
        self.assertEqual(sum(prov["prices"].values()), 36)
        self.assertIsInstance(body["stale"], bool)
        self.assertFalse(body["refreshing"])

    def test_static_spa_fallback(self):
        for route in ("/plan?target=1", "/ticker?symbol=MAIN"):
            with urllib.request.urlopen(self.base + route, timeout=30) as r:
                self.assertIn("배당 설계기", r.read().decode("utf-8"))

    def test_missing_asset_is_404_not_the_html_shell(self):
        """CSS 자리에 index.html 을 돌려주면 화면이 조용히 깨진다."""
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/does-not-exist.css")
        self.assertEqual(ctx.exception.code, 404)


class BasePathIndependence(unittest.TestCase):
    """경로 접두어가 붙은 곳(code-server의 /proxy/<포트>/ 등)에서도 열려야 한다.

    깨지는 방식이 조용하다 — CSS 요청에 index.html 이 돌아오면 화면만 밋밋해지고
    콘솔에는 아무 것도 안 남는다. 그래서 규칙을 테스트로 못 박아 둔다.
    """

    WEB = os.path.join(ROOT, "web")

    def _read(self, name):
        with open(os.path.join(self.WEB, name), encoding="utf-8") as f:
            return f.read()

    def test_html_references_assets_relatively(self):
        html = self._read("index.html")
        for tag in ('href="/styles.css"', 'src="/app.js"', 'src="/charts.js"'):
            self.assertNotIn(tag, html, "자산을 절대 경로로 불러오고 있습니다: %s" % tag)
        for tag in ('href="styles.css"', 'src="app.js"', 'src="charts.js"'):
            self.assertIn(tag, html, "상대 경로 참조가 없습니다: %s" % tag)

    def test_app_js_has_no_absolute_paths(self):
        app = self._read("app.js")
        for bad in ('fetch("/', "fetch('/", 'get("/', 'post("/', 'href="/'):
            self.assertNotIn(bad, app, "절대 경로가 남아 있습니다: %s" % bad)
        self.assertIn("document.currentScript", app, "기준 경로를 계산하지 않습니다")

    def test_routes_stay_one_segment_deep(self):
        """라우트가 두 칸 이상 깊어지면 상대 경로로 적은 자산이 엉뚱한 곳을 가리킨다."""
        app = self._read("app.js")
        for bad in ('go("ticker/', 'go("plan/'):
            self.assertNotIn(bad, app, "라우트가 한 칸보다 깊습니다: %s" % bad)


class RefreshEndpoint(unittest.TestCase):
    """/api/refresh 를 가짜 공급자에 붙여 SSE 진행 스트림을 확인한다.

    실제 data/market.json 을 건드리지 않도록 임시 파일로 갈아 끼우고, 테스트가 끝나면
    프로세스 안의 스냅샷도 원래대로 되돌린다.
    """

    @classmethod
    def setUpClass(cls):
        from dividend_planner import providers
        cls.providers = providers
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.fake_base = "http://127.0.0.1:%d" % cls.fake.server_address[1]
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.saved_bases = {k: getattr(providers, k) for k in
                           ("YAHOO_BASE", "NASDAQ_BASE", "NAVER_BASE",
                            "FRANKFURTER_BASE", "ALPHAVANTAGE_BASE", "RETRY_SLEEP")}
        for key in ("YAHOO_BASE", "NASDAQ_BASE", "NAVER_BASE", "FRANKFURTER_BASE",
                    "ALPHAVANTAGE_BASE"):
            setattr(providers, key, cls.fake_base)
        providers.RETRY_SLEEP = 0.0

        cls.tmp_pack = os.path.join(ROOT, "tests", "_tmp_api_pack.json")
        with open(os.path.join(ROOT, "tests", "golden", "market_bundle.json"),
                  encoding="utf-8") as src:
            with open(cls.tmp_pack, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        cls.saved_path = server.DATA_PATH
        server.DATA_PATH = cls.tmp_pack
        cls.saved_dataset = data_store.dataset()

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.fake.shutdown()
        cls.fake.server_close()
        for key, value in cls.saved_bases.items():
            setattr(cls.providers, key, value)
        server.DATA_PATH = cls.saved_path
        data_store.replace(cls.saved_dataset.raw)      # 원래 스냅샷 복구
        server._plan_cache.clear()
        if os.path.exists(cls.tmp_pack):
            os.remove(cls.tmp_pack)

    def _sse(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as res:
            raw = res.read().decode("utf-8")
        events = []
        for block in raw.split("\n\n"):
            if not block.strip():
                continue
            name = block.split("\n")[0].replace("event: ", "")
            data = json.loads(block.split("data: ", 1)[1])
            events.append((name, data))
        return events

    def test_refresh_streams_progress_and_swaps_the_snapshot(self):
        del CALLS[:]
        FAIL.clear()
        events = self._sse("/api/refresh", {"symbols": ["SCHD", "086790.KS"]})
        names = [n for n, _ in events]
        self.assertIn("progress", names)
        self.assertEqual(names[-1], "final")
        progress = [d for n, d in events if n == "progress"]
        self.assertTrue(all("step" in p and "total" in p for p in progress))
        report = events[-1][1]["report"]
        self.assertEqual(sorted(t["symbol"] for t in report["tickers"]),
                         ["086790.KS", "SCHD"])
        self.assertEqual(report["fx"]["source"], "frankfurter")
        self.assertTrue(report["fx"]["absolute"])
        # 파일이 실제로 갈아 끼워졌고 프로세스 안 스냅샷도 새 것이다
        with open(self.tmp_pack, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertTrue(saved["fx_absolute"])
        self.assertIsNotNone(data_store.dataset().fx_rate)
        with urllib.request.urlopen(self.base + "/api/health", timeout=30) as r:
            health = json.loads(r.read().decode("utf-8"))
        self.assertEqual(health["provenance"]["fx"], "frankfurter")

    def test_refresh_reports_failure_without_breaking_the_service(self):
        FAIL.clear()
        for prefix in ("/v8/finance/chart/", "/query?", "/api/quote/", "/siseJson.naver"):
            FAIL.add(prefix)
        try:
            events = self._sse("/api/refresh", {"symbols": ["SCHD"]})
        finally:
            FAIL.clear()
        report = events[-1][1]["report"]
        self.assertEqual([f["symbol"] for f in report["failed"]], ["SCHD"])
        # 갱신이 실패해도 계산은 계속 되어야 한다
        req = urllib.request.Request(
            self.base + "/api/plan", data=json.dumps(PLAN).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            plan = json.loads(r.read().decode("utf-8"))
        self.assertEqual(len(plan["portfolio"]), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
