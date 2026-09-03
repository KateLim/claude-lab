#!/usr/bin/env python3
"""미래를 위한 배당 설계기 — 로컬 서버.

표준 라이브러리만 쓴다. pip 설치 없이 `python3 server.py`로 바로 뜬다.
정적 파일(web/)과 API를 한 프로세스에서 서빙한다.

  GET  /api/health              엔진·데이터 상태
  GET  /api/universe            유니버스 36종목 지표
  GET  /api/tickers/{symbol}    종목 상세 (지표 + 월말 종가 + 배당 이력)
  POST /api/goal                나이·소득 -> 노후 배당 목표 역산
  POST /api/plan                목표 -> 포트폴리오 + 백테스트 + 전망
  POST /api/ai/strategy         전략 설명 (SSE 스트리밍)
  POST /api/ai/tickers/{symbol} 종목 선정 근거 설명 (SSE 스트리밍)
"""
import argparse
import json
import os
import queue
import sys
import threading
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dividend_planner import ai, engine, goal, portfolio, refresh   # noqa: E402
from dividend_planner import dataset as data_store                  # noqa: E402
from dividend_planner.dataset import DATA_PATH, dataset             # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DEFAULT_PORT = int(os.environ.get("PORT", "8770"))
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
# 이 확장자로 끝나는 요청은 SPA 폴백을 하지 않는다 (아래 _static 주석 참고)
ASSET_SUFFIXES = (".css", ".js", ".json", ".svg", ".ico", ".woff2", ".png", ".jpg", ".map")
PLAN_CACHE_SIZE = 64
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2"}

_explainer = ai.Explainer()
_plan_cache = OrderedDict()
_plan_lock = threading.Lock()
_refresh_lock = threading.Lock()
_refresh_state = {"running": False, "last": None, "error": None}
AUTO_REFRESH = os.environ.get("DIVIDEND_PLANNER_AUTO_REFRESH", "1") != "0"


# --------------------------------------------------------------------- 요청 정규화
def _num(value, default, low=None, high=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    if low is not None:
        v = max(low, v)
    if high is not None:
        v = min(high, v)
    return v


def normalize_plan_request(body):
    ds = dataset()
    risk = body.get("risk_preference")
    return {
        "target_monthly_dividend_krw": _num(body.get("target_monthly_dividend_krw"), 3000000.0, 0),
        "initial_capital_krw": _num(body.get("initial_capital_krw"), 0.0, 0),
        "monthly_contribution_krw": _num(body.get("monthly_contribution_krw"), 1000000.0, 0),
        "horizon_years": int(_num(body.get("horizon_years"), 20, 1, 50)),
        "risk_preference": risk if risk in portfolio.RISK_STABILITY else "balanced",
        "us_ratio": _num(body.get("us_ratio"), 0.75, 0.0, 1.0),
        "etf_ratio": _num(body.get("etf_ratio"), portfolio.DEFAULT_ETF_RATIO, 0.0, 1.0),
        "drip": bool(body.get("drip", True)),
        "after_tax": bool(body.get("after_tax", True)),
        "backtest_years": int(_num(body.get("backtest_years"), 10, 3, ds.max_backtest_years)),
        "solve_mode": "forecast",
    }


# ------------------------------------------------------------------------- 계산
def build_plan(req):
    ds = dataset()
    holdings, excluded, profile = portfolio.build(
        ds.tickers, req["backtest_years"], req["horizon_years"],
        req["risk_preference"], req["us_ratio"], req["etf_ratio"])

    a = engine.assumptions(holdings, req["after_tax"])
    target = req["target_monthly_dividend_krw"]
    proj, achieve = engine.projection(
        a, req["initial_capital_krw"], req["monthly_contribution_krw"],
        req["horizon_years"], req["drip"], target)
    bt = engine.backtest(holdings, ds, req["backtest_years"], req["initial_capital_krw"],
                         req["monthly_contribution_krw"], req["drip"], req["after_tax"])
    required = engine.required_monthly_contribution(
        a, req["initial_capital_krw"], req["horizon_years"], req["drip"], target)

    expected = proj["summary"]["final_monthly_dividend"]
    for h in holdings:
        h["monthly_dividend_contribution"] = h["weight"] * expected

    a["fx_rate"] = ds.fx_rate
    a["fx_source"] = ds.provenance()["fx"]
    portfolio_yield = a["start_yield"] * (1 - a["effective_tax_rate"])
    diagnosis = {
        "achieved": proj["summary"]["achieved"],
        "achievement_ratio": proj["summary"]["achievement_ratio"],
        "expected_monthly_dividend": expected,
        "target": target,
        "shortfall": proj["summary"]["shortfall"],
        "required_monthly_contribution": required,
        "extra_monthly_needed": max(0.0, (required or 0.0) - req["monthly_contribution_krw"]),
        "achieve_month": achieve,
        "achieve_text": engine.achieve_text(achieve),
        "required_assets": target * 12 / portfolio_yield if portfolio_yield else 0.0,
        "portfolio_yield_after_tax": portfolio_yield,
    }
    return {
        "request": req, "diagnosis": diagnosis, "portfolio": holdings,
        "excluded": excluded, "sector_allocation": portfolio.sector_allocation(holdings),
        "weight_profile": profile, "backtest": bt, "projection": proj,
        "assumptions": a, "as_of": ds.as_of, "cached": False,
    }


def cached_plan(req):
    key = json.dumps(req, sort_keys=True)
    with _plan_lock:
        if key in _plan_cache:
            _plan_cache.move_to_end(key)
            hit = dict(_plan_cache[key])
            hit["cached"] = True
            return hit
    plan = build_plan(req)
    with _plan_lock:
        _plan_cache[key] = plan
        while len(_plan_cache) > PLAN_CACHE_SIZE:
            _plan_cache.popitem(last=False)
    return plan


def default_portfolio_yield(horizon_years):
    """목표 역산에 쓰는 기준 배당수익률 — plan 기본 설정과 같은 포트폴리오로 계산한다."""
    ds = dataset()
    holdings, _, _ = portfolio.build(ds.tickers, 10, horizon_years, "balanced", 0.75,
                                     portfolio.DEFAULT_ETF_RATIO)
    a = engine.assumptions(holdings, True)
    return a["start_yield"] * (1 - a["avg_tax_rate"])


def run_refresh(progress=None, only=None):
    """데이터 갱신. 한 번에 하나만 돌고, 성공하면 스냅샷과 캐시를 갈아 끼운다."""
    if not _refresh_lock.acquire(blocking=False):
        raise RuntimeError("이미 갱신이 진행 중입니다")
    try:
        _refresh_state["running"] = True
        _refresh_state["error"] = None
        pack, report = refresh.refresh(data_store.load_pack(DATA_PATH), progress, only)
        refresh.save(pack, DATA_PATH)
        data_store.replace(pack)
        with _plan_lock:
            _plan_cache.clear()          # 데이터가 바뀌면 계산 결과도 다시 만들어야 한다
        _explainer.cache.clear()
        _refresh_state["last"] = report
        return report
    except Exception as exc:                                       # noqa: BLE001
        _refresh_state["error"] = str(exc)
        raise
    finally:
        _refresh_state["running"] = False
        _refresh_lock.release()


def refresh_events(only=None):
    """갱신을 돌리면서 진행 상황을 SSE로 흘려보낸다.

    갱신은 워커 스레드에서 돌리고 진행 이벤트는 큐로 넘겨받는다. 같은 스레드에서
    돌리면 36종목이 다 끝난 뒤에야 첫 줄이 나가서 스트리밍이 아무 의미가 없다.
    """
    events = queue.Queue()
    outcome = {}

    def worker():
        try:
            outcome["report"] = run_refresh(
                lambda **kw: events.put(("progress", kw)), only)
        except Exception as exc:                                   # noqa: BLE001
            outcome["error"] = str(exc)
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = events.get()
        if item is None:
            break
        yield item
    if "error" in outcome:
        yield "error", {"message": outcome["error"]}
        return
    report = outcome["report"]
    yield "final", {"report": report, "as_of": report["as_of"],
                    "provenance": dataset().provenance()}


def auto_refresh_if_stale():
    """서버가 뜰 때 기준월이 지나 있으면 백그라운드로 새로 받는다."""
    ds = dataset()
    if not AUTO_REFRESH or not ds.stale():
        return False

    def worker():
        try:
            report = run_refresh()
            sys.stderr.write("  데이터 갱신 완료 · 기준월 %s · 종목 %d개\n"
                             % (report["as_of"], report["ticker_count"]))
        except Exception as exc:                                   # noqa: BLE001
            sys.stderr.write("  데이터 갱신 실패 (%s) — 캐시로 계속합니다\n" % exc)

    threading.Thread(target=worker, daemon=True).start()
    return True


def ticker_detail(symbol):
    t = dataset().get(symbol)
    if t is None:
        return None
    return {"metrics": t.metrics(), "price_series": t.price_series(),
            "dividend_annual": t.dividend_annual(),
            "dividend_monthly": t.dividend_monthly()}


# ------------------------------------------------------------------------ 핸들러
class Handler(BaseHTTPRequestHandler):
    server_version = "DividendPlanner/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------------- 응답 도우미
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False, allow_nan=False))

    def _error(self, code, message):
        self._json({"error": message}, code)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _sse(self, events):
        """제너레이터를 SSE로 흘려보낸다 (event: 이름 / data: JSON)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for name, data in events:
                chunk = ("event: %s\ndata: %s\n\n"
                         % (name, json.dumps(data, ensure_ascii=False))).encode("utf-8")
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -------------------------------------------------------------------- 라우팅
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                ds = dataset()
                return self._json({
                    "status": "ok", "as_of": ds.as_of, "tickers": len(ds.tickers),
                    "months": len(ds.months), "max_backtest_years": ds.max_backtest_years,
                    "narrative_engine": ai.backend(),
                    "fetched_at": ds.fetched_at, "stale": ds.stale(),
                    "provenance": ds.provenance(),
                    "refreshing": _refresh_state["running"],
                    "last_refresh_error": _refresh_state["error"],
                    "auto_refresh": AUTO_REFRESH,
                })
            if path == "/api/universe":
                ds = dataset()
                return self._json({"count": len(ds.tickers), "as_of": ds.as_of,
                                   "tickers": [t.metrics() for t in ds.tickers]})
            if path.startswith("/api/tickers/"):
                detail = ticker_detail(path[len("/api/tickers/"):])
                if detail is None:
                    return self._error(404, "유니버스에 없는 종목입니다")
                return self._json(detail)
            if path.startswith("/api/"):
                return self._error(404, "없는 엔드포인트입니다")
            return self._static(path)
        except Exception as exc:                                   # noqa: BLE001
            traceback.print_exc()
            return self._error(500, str(exc))

    do_HEAD = do_GET

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            body = self._read_json()
        except ValueError:
            return self._error(400, "JSON 본문을 해석할 수 없습니다")
        try:
            if path == "/api/goal":
                horizon = int(_num(body.get("retire_age"), 60)) - int(_num(body.get("age"), 35))
                py = default_portfolio_yield(max(1, min(50, horizon)))
                return self._json(goal.compute(body, py))
            if path == "/api/plan":
                return self._json(cached_plan(normalize_plan_request(body)))
            if path == "/api/refresh":
                only = body.get("symbols") or None
                return self._sse(refresh_events(set(only) if only else None))
            if path == "/api/ai/strategy":
                return self._sse(_explainer.stream("strategy", body))
            if path.startswith("/api/ai/tickers/"):
                if not isinstance(body.get("holding"), dict):
                    return self._error(400, "holding 정보가 필요합니다")
                return self._sse(_explainer.stream("ticker", body))
            return self._error(404, "없는 엔드포인트입니다")
        except ValueError as exc:
            return self._error(400, str(exc))
        except Exception as exc:                                   # noqa: BLE001
            traceback.print_exc()
            return self._error(500, str(exc))

    # ------------------------------------------------------------------ 정적 파일
    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            # 자산으로 보이는 경로는 없으면 404로 알린다. index.html 을 CSS 자리에
            # 돌려주면 화면이 조용히 깨져서 원인을 찾기 어렵다.
            if rel.endswith(ASSET_SUFFIXES):
                return self._error(404, "없는 파일입니다: %s" % rel)
            target = os.path.join(WEB_DIR, "index.html")   # SPA 라우팅 폴백
        with open(target, "rb") as f:
            data = f.read()
        ctype = MIME.get(os.path.splitext(target)[1], "application/octet-stream")
        self._send(200, data, ctype)


def main():
    ap = argparse.ArgumentParser(description="미래를 위한 배당 설계기 (로컬 서버)")
    ap.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help="바인드 주소 (기본 127.0.0.1). 0.0.0.0 으로 열면 같은 네트워크의 "
                         "누구나 접속할 수 있고 이 서비스에는 인증이 없다")
    args = ap.parse_args()
    port, host = args.port, args.host
    ds = dataset()
    prov = ds.provenance()
    sys.stderr.write(
        "미래를 위한 배당 설계기 (로컬)\n"
        "  데이터 기준월 %s · 종목 %d개 · 월 데이터 %d개월 · 최대 백테스트 %d년\n"
        "  가격 출처 %s · 배당 출처 %s · 환율 %s%s\n"
        "  설명 엔진: %s\n"
        "  http://%s:%d\n"
        % (ds.as_of, len(ds.tickers), len(ds.months), ds.max_backtest_years,
           ", ".join("%s %d" % (k, v) for k, v in sorted(prov["prices"].items())),
           ", ".join("%s %d" % (k, v) for k, v in sorted(prov["dividends"].items())),
           prov["fx"], (" %.2f원" % prov["fx_rate"]) if prov["fx_rate"] else " (지수)",
           "Claude API" if ai.backend() == "claude" else "로컬 규칙 기반",
           host, port))
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.stderr.write("  주의: %s 로 열었습니다 — 인증이 없으니 신뢰하는 망에서만 쓰세요\n" % host)
    if auto_refresh_if_stale():
        sys.stderr.write("  데이터가 오래됐습니다 — 백그라운드로 새로 받는 중\n")
    sys.stderr.write("\n")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
