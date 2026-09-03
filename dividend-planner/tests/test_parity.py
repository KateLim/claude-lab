#!/usr/bin/env python3
"""참조 서비스 응답(tests/golden/)과 로컬 엔진 결과를 맞춰본다.

세 갈래로 검증한다.
1) 종목 지표: 번들 데이터로 다시 계산한 값이 참조 값과 같은가.
2) 목표 역산: /api/goal 응답과 자릿수까지 같은가.
3) 백테스트·전망 엔진: 참조가 쓴 것과 같은 비중을 넣으면 같은 시계열이 나오는가.
4) 포트폴리오 구성: 같은 종목을 고르고 비중이 근접하는가.

일부 항목은 원본이 일별 데이터를 쓰고 이 서비스는 월말 종가만 쓰기 때문에
구조적으로 조금 다르다 (MDD, 상장 10년 미만 종목의 총수익 CAGR). 해당 항목은
허용 오차를 명시해 두었다 — 공식이 아니라 데이터 해상도 차이다.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
GOLDEN = os.path.join(ROOT, "tests", "golden")

from dividend_planner import engine, goal, portfolio                  # noqa: E402
from dividend_planner.dataset import Dataset, load_pack                # noqa: E402

# 참조 응답과 맞춰볼 때는 data/market.json이 아니라 고정된 스냅샷을 쓴다.
# 실행 중에 데이터를 갱신하면 라이브 데이터가 들어오는데, 그때 테스트가 깨지면
# 회귀인지 데이터가 바뀐 것인지 구분할 수 없다.
DS = Dataset(load_pack(os.path.join(GOLDEN, "market_bundle.json")))
MDD_TOLERANCE = 0.10          # 월말 종가 기준 MDD는 일별보다 얕거나 깊을 수 있다
# 참조 서비스에는 ETF 비중 제약이 없다. 정합성을 볼 때는 제약을 끄고(None) 비교한다.
# 이 서비스의 기본값(ETF 60%)은 의도적으로 다른 포트폴리오를 만든다.
NO_ETF_CONSTRAINT = None
SHORT_HISTORY = {"VICI"}      # 상장 10년 미만 — 총수익 CAGR 창이 원본과 다르다


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as f:
        return json.load(f)


def plan_goldens():
    return sorted(f for f in os.listdir(GOLDEN) if f.startswith("plan_"))


class TickerMetrics(unittest.TestCase):
    EXACT = ["price", "ttm_dividend", "ttm_yield", "div_cagr_5y", "div_cagr_10y", "div_cagr",
             "div_growth_streak", "div_cut_count_10y", "payout_frequency", "volatility",
             "history_years", "first_date", "market", "kind", "sector", "name"]

    def test_metrics_match_reference(self):
        ref = dict((t["symbol"], t) for t in load("universe.json")["tickers"])
        self.assertEqual(len(ref), len(DS.tickers))
        for t in DS.tickers:
            got, want = t.metrics(), ref[t.symbol]
            for key in self.EXACT:
                a, b = got[key], want[key]
                if isinstance(b, float):
                    self.assertAlmostEqual(a, b, delta=max(1e-9, abs(b) * 1e-9),
                                           msg="%s.%s" % (t.symbol, key))
                else:
                    self.assertEqual(a, b, "%s.%s" % (t.symbol, key))
            self.assertAlmostEqual(got["mdd"], want["mdd"], delta=MDD_TOLERANCE,
                                   msg="%s.mdd" % t.symbol)
            if t.symbol not in SHORT_HISTORY:
                self.assertAlmostEqual(got["total_return_cagr"], want["total_return_cagr"],
                                       delta=1e-9, msg="%s.total_return_cagr" % t.symbol)


class Goal(unittest.TestCase):
    def test_goal_matches_reference(self):
        want = load("goal_base.json")
        got = goal.compute({
            "age": 35, "retire_age": 60, "household_type": "couple", "housing": "owned",
            "monthly_income_after_tax_krw": 5000000, "monthly_spending_krw": 3000000,
            "current_assets_krw": 50000000, "pension_monthly_krw": 1200000,
        }, want["assumptions"]["portfolio_yield_used"])
        for key in ("horizon_years", "monthly_contribution_krw", "housing_addon_krw",
                    "monthly_spending_krw", "monthly_income_after_tax_krw"):
            self.assertEqual(got[key], want[key], key)
        for scenario in want["scenarios"]:
            a, b = got["scenarios"][scenario], want["scenarios"][scenario]
            for key in ("label", "living_cost_today", "living_cost_at_retirement",
                        "floor_applied", "floor_value", "target_monthly_dividend"):
                self.assertEqual(a[key], b[key], "%s.%s" % (scenario, key))
            self.assertAlmostEqual(a["required_assets"], b["required_assets"],
                                   delta=abs(b["required_assets"]) * 1e-9)

    def test_estimation_fills_blanks(self):
        got = goal.compute({"age": 35, "retire_age": 60}, 0.027)
        fields = [e["field"] for e in got["estimated_fields"]]
        self.assertEqual(fields, ["monthly_income_after_tax_krw", "monthly_spending_krw",
                                  "monthly_contribution_krw"])
        self.assertEqual(got["monthly_income_after_tax_krw"], 3600000)
        self.assertEqual(got["monthly_spending_krw"], 2340000)
        self.assertEqual(got["monthly_contribution_krw"], 1260000)

    def test_retire_before_now_is_rejected(self):
        with self.assertRaises(ValueError):
            goal.compute({"age": 60, "retire_age": 55}, 0.027)


class Engine(unittest.TestCase):
    """참조 비중을 그대로 넣었을 때 시계열과 요약이 재현되는지."""

    def _assumptions(self, g):
        a = dict(g["assumptions"])
        a["effective_tax_rate"] = a["avg_tax_rate"] if g["request"]["after_tax"] else 0.0
        return a

    def test_backtest_reproduces_reference(self):
        for name in plan_goldens():
            g = load(name)
            r = g["request"]
            got = engine.backtest([dict(h) for h in g["portfolio"]], DS, r["backtest_years"],
                                  r["initial_capital_krw"], r["monthly_contribution_krw"],
                                  r["drip"], r["after_tax"])
            self.assertEqual(len(got["monthly"]), len(g["backtest"]["monthly"]), name)
            for a, b in zip(got["monthly"], g["backtest"]["monthly"]):
                self.assertEqual(a["date"], b["date"], name)
                for key in ("value", "contributed", "dividend", "dividend_gross", "dividend_ttm"):
                    self.assertAlmostEqual(a[key], b[key], delta=max(1e-6, abs(b[key]) * 1e-9),
                                           msg="%s %s %s" % (name, a["date"], key))
            for key, want in g["backtest"]["summary"].items():
                if isinstance(want, str):
                    self.assertEqual(got["summary"][key], want, "%s %s" % (name, key))
                else:
                    self.assertAlmostEqual(got["summary"][key], want,
                                           delta=max(1e-6, abs(want) * 1e-9),
                                           msg="%s summary.%s" % (name, key))

    def test_projection_reproduces_reference(self):
        for name in plan_goldens():
            g = load(name)
            r = g["request"]
            got, achieve = engine.projection(
                self._assumptions(g), r["initial_capital_krw"], r["monthly_contribution_krw"],
                r["horizon_years"], r["drip"], r["target_monthly_dividend_krw"])
            for a, b in zip(got["monthly"], g["projection"]["monthly"]):
                for key in ("month", "value", "dividend", "contributed"):
                    self.assertAlmostEqual(a[key], b[key], delta=max(1e-6, abs(b[key]) * 1e-9),
                                           msg="%s month %s %s" % (name, a["month"], key))
            self.assertEqual(achieve, g["diagnosis"]["achieve_month"], name)
            self.assertEqual(engine.achieve_text(achieve), g["diagnosis"]["achieve_text"], name)

    def test_required_contribution_reproduces_reference(self):
        for name in plan_goldens():
            g = load(name)
            r = g["request"]
            got = engine.required_monthly_contribution(
                self._assumptions(g), r["initial_capital_krw"], r["horizon_years"],
                r["drip"], r["target_monthly_dividend_krw"])
            want = g["diagnosis"]["required_monthly_contribution"]
            self.assertAlmostEqual(got, want, delta=max(1e-3, abs(want) * 1e-9), msg=name)

    def test_assumptions_are_weighted_aggregates(self):
        for name in plan_goldens():
            g = load(name)
            got = engine.assumptions([dict(h) for h in g["portfolio"]], g["request"]["after_tax"])
            for key in ("start_yield", "div_growth_raw", "div_growth", "price_growth_raw",
                        "price_growth", "avg_tax_rate", "haircut", "price_growth_cap"):
                self.assertAlmostEqual(got[key], g["assumptions"][key],
                                       delta=max(1e-12, abs(g["assumptions"][key]) * 1e-9),
                                       msg="%s %s" % (name, key))


class PortfolioConstruction(unittest.TestCase):
    def test_profile_weights_match_reference(self):
        for name in plan_goldens():
            g = load(name)
            r = g["request"]
            got = portfolio.profile_for(r["horizon_years"], r["risk_preference"])
            for key in ("profile", "yield", "growth", "stability", "quality", "smooth"):
                if key == "profile":
                    self.assertEqual(got[key], g["weight_profile"][key], name)
                else:
                    self.assertAlmostEqual(got[key], g["weight_profile"][key], delta=1e-12,
                                           msg="%s %s" % (name, key))

    def test_screening_matches_reference(self):
        for name in plan_goldens():
            g = load(name)
            _, excluded = portfolio.screen(DS.tickers, g["request"]["backtest_years"])
            self.assertEqual([e["symbol"] for e in excluded],
                             [e["symbol"] for e in g["excluded"]], name)
            self.assertEqual([e["reason"] for e in excluded],
                             [e["reason"] for e in g["excluded"]], name)

    def test_selection_and_weights_close_to_reference(self):
        """MDD 해상도 차이가 점수를 미세하게 흔들어 경계 종목이 바뀔 수 있다.
        국내 100% (us_ratio=0)는 섹터 상한이 두 번 걸리는 퇴화 케이스로 제외한다."""
        for name in plan_goldens():
            g = load(name)
            r = g["request"]
            if r["us_ratio"] == 0:
                continue
            holdings, _, _ = portfolio.build(DS.tickers, r["backtest_years"], r["horizon_years"],
                                             r["risk_preference"], r["us_ratio"],
                                             NO_ETF_CONSTRAINT)
            got = dict((h["symbol"], h["weight"]) for h in holdings)
            want = dict((h["symbol"], h["weight"]) for h in g["portfolio"])
            self.assertEqual(len(got), len(want), name)
            overlap = set(got) & set(want)
            self.assertGreaterEqual(len(overlap), len(want) - 1,
                                    "%s: 선정 종목이 2개 이상 다르다 %s" % (name, set(want) ^ set(got)))
            # 공통 종목의 비중 오차만 본다. 경계에서 한 종목이 바뀌면 그 종목의 비중이
            # 통째로 차이로 잡혀서, 나머지 비중이 얼마나 잘 맞는지가 가려진다.
            l1 = sum(abs(got[s] - want[s]) for s in overlap)
            self.assertLess(l1, 0.06, "%s: 공통 종목 비중 L1 오차 %.4f" % (name, l1))
            self.assertAlmostEqual(sum(got.values()), 1.0, delta=1e-9, msg=name)

    def test_sector_cap_is_respected_when_feasible(self):
        for us in (0.5, 0.75, 1.0):
            holdings, _, _ = portfolio.build(DS.tickers, 10, 25, "balanced", us,
                                             NO_ETF_CONSTRAINT)
            for row in portfolio.sector_allocation(holdings):
                if row["sector"] in portfolio.SECTOR_CAP_EXEMPT:
                    continue
                self.assertLessEqual(row["weight"], portfolio.SECTOR_CAP + 1e-9,
                                     "us=%s %s" % (us, row["sector"]))

    def test_etf_ratio_makes_etfs_the_core(self):
        """ETF 비중 제약이 실제로 코어를 만드는지. 기본값은 ETF 중심이어야 한다."""
        holdings, _, profile = portfolio.build(DS.tickers, 10, 25, "balanced", 0.75)
        self.assertAlmostEqual(profile["etf_ratio"], portfolio.DEFAULT_ETF_RATIO, delta=1e-9)
        self.assertGreaterEqual(profile["etf_count"], 5)
        self.assertGreater(profile["etf_weight"], 0.5, "ETF가 코어가 아닙니다")
        self.assertAlmostEqual(sum(h["weight"] for h in holdings), 1.0, delta=1e-9)

    def test_etf_ratio_is_adjustable_end_to_end(self):
        seen = []
        for ratio in (0.0, 0.3, 0.6, 1.0):
            _, _, profile = portfolio.build(DS.tickers, 10, 25, "balanced", 0.75, ratio)
            seen.append(profile["etf_weight"])
        self.assertAlmostEqual(seen[0], 0.0, delta=1e-9, msg="0%인데 ETF가 담겼습니다")
        for a, b in zip(seen, seen[1:]):
            self.assertGreater(b, a - 1e-9, "ETF 비중을 올렸는데 실제 비중이 줄었습니다")

    def test_etf_ratio_cannot_exceed_us_ratio(self):
        """유니버스의 ETF는 모두 미국 상장이다. 미국 비중이 상한이어야 한다."""
        _, _, profile = portfolio.build(DS.tickers, 10, 25, "balanced", 0.4, 0.9)
        self.assertAlmostEqual(profile["etf_ratio_effective"], 0.4, delta=1e-9)
        _, _, kr_only = portfolio.build(DS.tickers, 10, 25, "balanced", 0.0, 0.6)
        self.assertEqual(kr_only["etf_count"], 0)
        self.assertAlmostEqual(kr_only["etf_weight"], 0.0, delta=1e-9)

    def test_zero_share_buckets_get_no_holdings(self):
        """비중 0으로 담긴 종목은 표만 채우고 아무 일도 하지 않는다."""
        holdings, _, _ = portfolio.build(DS.tickers, 10, 25, "balanced", 0.0, 0.6)
        self.assertTrue(all(h["weight"] > 0 for h in holdings),
                        [h["symbol"] for h in holdings if h["weight"] <= 0])
        self.assertTrue(all(h["market"] == "KR" for h in holdings))

    def test_etf_core_survives_short_history_screening(self):
        """15년 백테스트에서는 ETF 3개만 통과한다. 그래도 목표 비중은 맞춰야 한다."""
        _, excluded, profile = portfolio.build(DS.tickers, 15, 25, "balanced", 0.75, 0.6)
        self.assertEqual(profile["etf_count"], 3)
        self.assertAlmostEqual(profile["etf_weight"], 0.6, delta=0.02)
        self.assertIn("SCHD", [e["symbol"] for e in excluded])

    def test_factor_scores_are_universe_percentiles(self):
        scores = portfolio.factor_scores(DS.tickers)
        n = len(DS.tickers)
        for key in ("yield", "growth", "quality", "smooth"):
            values = sorted(scores[t.symbol][key] for t in DS.tickers)
            # 동점은 평균 순위를 쓰므로 최상위가 100 미만일 수 있다 (지급 횟수처럼
            # 값이 몇 가지로 뭉치는 팩터). 다만 백분위 범위 안에는 있어야 한다.
            self.assertGreater(values[0], 0.0, key)
            self.assertLessEqual(values[-1], 100.0 + 1e-9, key)
            self.assertGreaterEqual(values[-1], 100.0 - 100.0 / n * 2, key)
            self.assertLessEqual(values[0], 100.0 / n * 2.5, key)
            # 백분위 합은 순위 합과 같아야 한다 — 동점 처리가 틀리면 여기서 깨진다
            self.assertAlmostEqual(sum(values), (n + 1) / 2.0 * 100.0, delta=1e-9, msg=key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
