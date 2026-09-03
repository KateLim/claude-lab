#!/usr/bin/env python3
"""서비스가 제대로 도는지 한 번에 확인한다.

    python3 tools/selfcheck.py                      # 127.0.0.1:8770 검사
    python3 tools/selfcheck.py --base http://호스트:포트/proxy/8770
    python3 tools/selfcheck.py --start              # 서버를 직접 띄워서 검사하고 끈다

서버를 안 띄웠으면 --start 를 쓰면 된다. 검사 항목마다 실제로 받은 값을 같이 찍는다 —
"통과"만 보여 주면 무엇을 확인했는지 알 수 없으니.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PLAN = {"target_monthly_dividend_krw": 4806779, "initial_capital_krw": 50000000,
        "monthly_contribution_krw": 2000000, "horizon_years": 25,
        "risk_preference": "balanced", "us_ratio": 0.75, "drip": True,
        "after_tax": True, "backtest_years": 10}

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = DIM = OFF = ""


class Checks(object):
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.passed = self.failed = 0

    def _url(self, path):
        return "%s/%s" % (self.base, path.lstrip("/"))

    def get(self, path, timeout=90):
        with urllib.request.urlopen(self._url(path), timeout=timeout) as r:
            return r.status, r.headers, r.read()

    def post(self, path, body, timeout=180):
        req = urllib.request.Request(
            self._url(path), data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()

    def check(self, label, fn):
        try:
            note = fn()
            self.passed += 1
            print("  %s✓%s %-34s %s%s%s" % (GREEN, OFF, label, DIM, note, OFF))
        except Exception as exc:                                   # noqa: BLE001
            self.failed += 1
            print("  %s✗%s %-34s %s" % (RED, OFF, label, exc))


def run(base):
    c = Checks(base)
    print("\n검사 대상: %s\n" % c.base)
    # 서버가 아예 안 떠 있으면 12줄의 같은 오류를 찍는 대신 한 줄로 알려준다
    try:
        c.get("/api/health", timeout=10)
    except urllib.error.URLError as exc:
        print("서버에 연결하지 못했습니다: %s" % exc)
        print("서버를 먼저 띄우거나 --start 를 쓰세요:")
        print("    python3 server.py &   또는   python3 tools/selfcheck.py --start\n")
        return 2

    print("화면")

    def page():
        status, headers, body = c.get("/")
        text = body.decode("utf-8", "replace")
        assert status == 200, "HTTP %s" % status
        assert "배당 설계기" in text, "제목이 없습니다"
        assert 'src="app.js"' in text, "스크립트 경로가 상대 경로가 아닙니다"
        return "index.html %d바이트 · 자산 경로 상대" % len(body)
    c.check("첫 화면", page)

    def assets():
        sizes = []
        for name, kind in (("styles.css", "text/css"), ("app.js", "text/javascript"),
                           ("charts.js", "text/javascript")):
            status, headers, body = c.get("/" + name)
            assert status == 200, "%s HTTP %s" % (name, status)
            assert kind in headers.get("Content-Type", ""), \
                "%s 의 Content-Type 이 %s" % (name, headers.get("Content-Type"))
            sizes.append("%s %.0fKB" % (name, len(body) / 1024.0))
        return " · ".join(sizes)
    c.check("CSS·JS", assets)

    def spa():
        for route in ("/plan", "/ticker"):
            status, headers, body = c.get(route)
            assert status == 200 and b"\xeb\xb0\xb0\xeb\x8b\xb9" in body, \
                "%s 가 화면을 돌려주지 않습니다" % route
        try:
            c.get("/nope.css")
            raise AssertionError("없는 자산이 404가 아닙니다")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, "HTTP %s" % exc.code
        return "/plan · /ticker 폴백, 없는 자산은 404"
    c.check("SPA 라우팅", spa)

    print("\n데이터")
    health = {}

    def health_check():
        status, _, body = c.get("/api/health")
        health.update(json.loads(body))
        assert health["status"] == "ok"
        assert health["tickers"] == 36, "종목 %s개" % health["tickers"]
        return "기준월 %s · 종목 %d개 · 최대 백테스트 %d년 · 설명 %s" % (
            health["as_of"], health["tickers"], health["max_backtest_years"],
            health["narrative_engine"])
    c.check("상태", health_check)

    def provenance():
        prov = health["provenance"]
        prices = ", ".join("%s %d" % kv for kv in sorted(prov["prices"].items()))
        fx = "%s %s" % (prov["fx"],
                        ("%.2f원" % prov["fx_rate"]) if prov["fx_rate"] else "(지수)")
        assert sum(prov["prices"].values()) == 36
        return "가격 %s · 환율 %s%s" % (prices, fx, " · 갱신 필요" if health["stale"] else "")
    c.check("데이터 출처", provenance)

    def universe():
        _, _, body = c.get("/api/universe")
        data = json.loads(body)
        assert data["count"] == 36
        top = max(data["tickers"], key=lambda t: t["ttm_yield"])
        return "최고 배당수익률 %s %.2f%%" % (top["name"], top["ttm_yield"] * 100)
    c.check("유니버스", universe)

    def ticker():
        _, _, body = c.get("/api/tickers/086790.KS")
        data = json.loads(body)
        m = data["metrics"]
        assert len(data["price_series"]) > 100
        return "%s · 월 데이터 %d개 · 배당 %d년" % (
            m["name"], len(data["price_series"]), len(data["dividend_annual"]))
    c.check("종목 상세", ticker)

    print("\n계산")

    def goal():
        _, _, body = c.post("/api/goal", {
            "age": 35, "retire_age": 60, "monthly_income_after_tax_krw": 5000000,
            "monthly_spending_krw": 3000000, "current_assets_krw": 50000000,
            "pension_monthly_krw": 1200000})
        data = json.loads(body)
        target = data["scenarios"]["adequate"]["target_monthly_dividend"]
        assert target == 4806779, "적정 노후 목표가 %s" % target
        return "적정 노후 목표 월 %s원 (참조 서비스와 동일)" % "{:,}".format(target)
    c.check("목표 역산", goal)

    plan = {}

    def plan_check():
        started = time.time()
        _, _, body = c.post("/api/plan", PLAN)
        plan.update(json.loads(body))
        weights = sum(h["weight"] for h in plan["portfolio"])
        assert len(plan["portfolio"]) == 10, "종목 %d개" % len(plan["portfolio"])
        assert abs(weights - 1.0) < 1e-9, "비중 합 %.6f" % weights
        assert len(plan["backtest"]["monthly"]) == 121
        assert len(plan["projection"]["monthly"]) == 300
        return "10종목 · 비중합 1.000 · 백테스트 121개월 · 전망 300개월 · %.1f초" % (
            time.time() - started)
    c.check("포트폴리오·백테스트·전망", plan_check)

    def diagnosis():
        d = plan["diagnosis"]
        bt = plan["backtest"]["summary"]
        return "전망 월배당 %s원 (목표의 %.0f%%) · %s · 백테스트 YoC %.2f%% · MDD %.2f%%" % (
            "{:,.0f}".format(d["expected_monthly_dividend"]), d["achievement_ratio"] * 100,
            d["achieve_text"], bt["yield_on_cost"] * 100, bt["mdd"] * 100)
    c.check("진단", diagnosis)

    def cache():
        _, _, body = c.post("/api/plan", PLAN)
        assert json.loads(body)["cached"] is True, "두 번째 요청이 캐시를 안 씁니다"
        return "같은 조건 재요청은 캐시"
    c.check("계산 캐시", cache)

    print("\n설명 (스트리밍)")

    def ai():
        req = urllib.request.Request(
            c._url("/api/ai/strategy"), data=json.dumps(plan).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as res:
            assert res.headers["Content-Type"].startswith("text/event-stream"), \
                "SSE 가 아닙니다: %s" % res.headers["Content-Type"]
            raw = res.read().decode("utf-8")
        deltas, final = [], False
        for block in raw.split("\n\n"):
            if block.startswith("event: delta"):
                deltas.append(json.loads(block.split("data: ", 1)[1])["text"])
            elif block.startswith("event: final"):
                final = True
        text = "".join(deltas)
        assert final, "final 이벤트가 없습니다"
        assert "## 이 전략의 핵심" in text, "설명 형식이 다릅니다"
        assert "투자 권유가 아닙니다" in text, "면책 문구가 없습니다"
        return "%d조각 · %d자 · 4개 섹션" % (len(deltas), len(text))
    c.check("전략 설명", ai)

    print("\n%s통과 %d · 실패 %d%s\n" % (RED if c.failed else GREEN, c.passed, c.failed, OFF))
    return 1 if c.failed else 0


def main():
    ap = argparse.ArgumentParser(description="배당 설계기 자체 점검")
    ap.add_argument("--base", default="http://127.0.0.1:%s" % os.environ.get("PORT", "8770"))
    ap.add_argument("--start", action="store_true", help="서버를 직접 띄워서 검사하고 끈다")
    args = ap.parse_args()

    proc = None
    if args.start:
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                urllib.request.urlopen(args.base + "/api/health", timeout=2).read()
                break
            except Exception:                                      # noqa: BLE001
                time.sleep(0.5)
    try:
        return run(args.base)
    except urllib.error.URLError as exc:
        print("\n서버에 연결하지 못했습니다 (%s): %s" % (args.base, exc))
        print("서버를 먼저 띄우거나 --start 를 쓰세요.\n")
        return 2
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
