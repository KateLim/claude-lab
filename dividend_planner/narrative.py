"""계산 결과만으로 한국어 설명을 만든다 (외부 모델 없이 동작하는 기본 경로).

원칙은 원본 서비스와 같다 — 표에 없는 숫자를 새로 만들지 않는다. 모든 문장은
plan 응답 안의 값에서 직접 끌어온다. 같은 입력이면 같은 문장이 나온다.
"""


def won(v):
    """1,234,567원 / 123만원 / 1.2억원 형태로 줄여 쓴다."""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e8:
        n = v / 1e8
        return "%s억원" % (("%.0f" if abs(n) >= 100 else "%.1f") % n)
    if a >= 1e4:
        return "{:,}만원".format(int(round(v / 1e4)))
    return "{:,}원".format(int(round(v)))


def exact(v):
    return "{:,}원".format(int(round(v)))


def pct(v, digits=1):
    return "-" if v is None else ("%." + str(digits) + "f%%") % (v * 100)


def particle(word, pair):
    """은/는, 이/가, 와/과, 로/으로 — 앞 글자의 종성으로 고른다."""
    with_jong, without = pair.split("/")
    ch = (word or "").strip()[-1:] or ""
    code = ord(ch) if ch else 0
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        has = jong not in (0, 8) if pair == "으로/로" else jong != 0
    elif ch.isdigit():
        has = ch not in "2459"
    elif ch.isalpha():
        # 알파벳은 한국어로 읽은 소리로 판단한다. 엘·엠·엔·알만 받침이 남고
        # 에프·비·티 같은 나머지는 받침이 없다 ("ETF는", "ETF로").
        has = ch.lower() in "lmnr"
    else:
        return without
    return with_jong if has else without


def _join(names, limit=4):
    head = names[:limit]
    joined = "·".join(head)
    return joined + (" 등" if len(names) > limit else "")


PROFILE_TEXT = {
    "income": ("인컴 편향",
               "남은 기간이 8년 미만이라 배당이 커질 시간이 부족해 지금의 배당수익률을 가장 크게 보기"),
    "balanced": ("균형",
                 "남은 기간이 8~15년이라 현재 배당수익률과 배당성장률을 비슷한 비중으로 보기"),
    "growth": ("배당성장 편향",
               "남은 기간이 15년 이상이라 지금의 배당수익률보다 배당이 매년 얼마나 늘어나는지를 더 크게 보기"),
}


def strategy(plan):
    """전략 전체에 대한 4단 설명 (마크다운)."""
    req, dg, a = plan["request"], plan["diagnosis"], plan["assumptions"]
    prof = plan["weight_profile"]
    port = plan["portfolio"]
    bt = plan["backtest"]["summary"]
    label, why = PROFILE_TEXT[prof["profile"]]

    top = sorted(port, key=lambda h: -h["weight"])
    streakers = [h["name"] for h in port
                 if h["kind"] != "etf" and h["div_cut_count_10y"] == 0
                 and h["div_growth_streak"] >= 10]
    # ETF의 분배금 감소와 기업의 배당 삭감은 다른 신호다. 한 문장에 묶으면 그 차이가 지워진다.
    cutters = [(h["name"], h["div_cut_count_10y"]) for h in port
               if h["div_cut_count_10y"] > 0 and h["kind"] != "etf"]
    etf_cutters = [(h["name"], h["div_cut_count_10y"]) for h in port
                   if h["div_cut_count_10y"] > 0 and h["kind"] == "etf"]
    etfs = [h["name"] for h in port if h["kind"] == "etf"]
    kr = [h for h in port if h["market"] == "KR"]
    sectors = plan["sector_allocation"][:3]

    out = []
    out.append("## 이 전략의 핵심")
    core = ("월 %s의 배당 목표에 현재 자산 %s, 매월 %s 적립으로 접근하는 계획입니다. "
            "%d년 뒤 전망 월 배당은 목표의 %s인 %s입니다."
            % (exact(req["target_monthly_dividend_krw"]), won(req["initial_capital_krw"]),
               won(req["monthly_contribution_krw"]), req["horizon_years"],
               pct(dg["achievement_ratio"], 2), exact(dg["expected_monthly_dividend"])))
    core += (" 목표 도달 시점은 %s로 계산됩니다." % dg["achieve_text"] if dg["achieve_text"]
             else " 이 조건에서는 50년 안에 목표에 닿지 않습니다.")
    if dg["achieved"]:
        core += " 목표 시점 기준으로 필요 총자산은 %s입니다." % won(dg["required_assets"])
    elif dg["required_monthly_contribution"] is None:
        core += (" 목표 시점에 %s이 부족하고, 월 적립액만으로는 이 기간 안에 메울 수 없습니다."
                 % exact(dg["shortfall"]))
    else:
        core += (" 목표 시점에 %s이 부족하며, 이를 메우려면 월 적립액이 %s 필요합니다."
                 % (exact(dg["shortfall"]), exact(dg["required_monthly_contribution"])))
    capped = a["price_growth"] >= a["price_growth_cap"] - 1e-12
    core += (" 전망은 배당성장률 %s(과거 %s에 %g배 할인), 주가상승률 %s(과거 %s에 %s)를 가정한 값입니다."
             % (pct(a["div_growth"], 2), pct(a["div_growth_raw"], 2), a["haircut"],
                pct(a["price_growth"], 2), pct(a["price_growth_raw"], 2),
                ("상한 %s 적용" % pct(a["price_growth_cap"])) if capped
                else ("%g배 할인" % a["haircut"])))
    out.append(core)

    out.append("## 왜 이렇게 배분했는가")
    alloc = ("가중치 프로파일은 '%s'(%s)입니다 — %s 때문입니다. "
             "그래서 배당성장 %s, 배당수익률 %s, 안정성 %s의 가중치로 36종목을 줄 세웠습니다."
             % (prof["profile"], label, why, pct(prof["growth"]), pct(prof["yield"]),
                pct(prof["stability"])))
    if streakers:
        joined = _join(streakers)
        alloc += (" 개별주 쪽에서는 %s%s 최근 10년 배당 삭감이 없고 연속 증가 기록이 길어"
                  " 상위에 올랐습니다." % (joined, particle(joined, "은/는")))
    if etfs:
        etf_weight = sum(h["weight"] for h in port if h["kind"] == "etf")
        alloc += (" 이 계획의 코어는 ETF입니다 — %s %d종목이 비중 %s를 맡아 종목 하나가 "
                  "어긋나도 전체가 덜 흔들리게 했습니다."
                  % (_join(etfs, 3), len(etfs), pct(etf_weight, 1)))
    if kr:
        joined_kr = _join(["%s(%s)" % (h["name"], pct(h["weight"], 2)) for h in kr], 3)
        alloc += (" %s%s 원화 배당수익률을 보완했습니다."
                  % (joined_kr, particle(joined_kr, "으로/로")))
    else:
        alloc += " 국내 비중은 0%로 설정되어 전량 미국 종목입니다."
    alloc += (" 섹터로는 %s 순으로 배분되었고, 한 섹터가 30%%를 넘지 않도록 제약을 걸었습니다"
              " (ETF는 자체 분산되어 제외)."
              % ", ".join("%s %s" % (s["sector"], pct(s["weight"], 2)) for s in sectors))
    out.append(alloc)

    out.append("## 짚어야 할 리스크")
    risk = ("과거 %d년 백테스트에서 최대낙폭은 %s였습니다 — 같은 전략을 실제로 굴렸다면 평가액이 "
            "그만큼 줄어드는 구간을 견뎌야 했다는 뜻입니다."
            % (req["backtest_years"], pct(bt["mdd"], 2)))
    if cutters:
        risk += (" 개별주 중 %s는 최근 10년 배당 삭감 이력이 있어 배당 안정성 측면에서 주의가 필요합니다."
                 % ", ".join("%s(삭감 %d회)" % (n, c) for n, c in cutters[:4]))
    else:
        risk += " 보유 개별주 중 최근 10년 배당을 삭감한 종목은 없습니다."
    if etf_cutters:
        risk += (" %s는 연간 분배금이 줄어든 해가 있습니다 — 기업이 배당을 깎은 것과 달리 구성 종목"
                 " 교체와 시장 상황에 따라 분배금이 오르내린 결과지만, 받는 쪽에서 그해 현금흐름이"
                 " 줄어든 것은 같습니다."
                 % ", ".join("%s(%d회)" % (n, c) for n, c in etf_cutters[:3]))
    risk += (" 전망 가정 자체도 과거 배당성장률 %s·주가상승률 %s에 보수적 할인을 적용한 값이므로, "
             "시장 환경에 따라 %s·%s조차 달성되지 않을 수 있습니다."
             % (pct(a["div_growth_raw"], 2), pct(a["price_growth_raw"], 2),
                pct(a["div_growth"], 2), pct(a["price_growth"], 2)))
    out.append(risk)

    out.append("## 지금 할 일")
    planned = exact(req["monthly_contribution_krw"])
    if dg["required_monthly_contribution"] is None:
        return_note = "전망 기준으로는 월 적립액을 올려서 이 기간 안에 목표를 채울 수 없습니다 — "
    else:
        return_note = ("전망 기준 필요 월 적립액은 %s입니다 — 현재 계획한 월 %s%s 비교하면 "
                       % (exact(dg["required_monthly_contribution"]), planned,
                          particle(planned, "과/와")))
    todo = return_note
    if dg["extra_monthly_needed"] <= 0:
        todo += "적립액을 유지하는 것이 우선입니다."
    else:
        extra = exact(dg["extra_monthly_needed"])
        todo += ("월 %s%s 더 넣거나 목표 시점을 늦추는 두 선택지가 있습니다."
                 % (extra, particle(extra, "을/를")))
    todo += (" 백테스트에서는 최근 12개월 월평균 배당이 %s, 투입 원금 대비 배당수익률(YoC)이 %s로 확인되니 "
             "실제 입금액과 YoC 추이를 정기적으로 확인하세요."
             % (exact(bt["monthly_dividend_avg_12m"]), pct(bt["yield_on_cost"], 2)))
    if cutters:
        todo += " 삭감 이력이 있는 개별주의 배당 공시는 분기마다 확인하는 편이 안전합니다."
    out.append(todo)

    out.append("이 내용은 계산된 시뮬레이션 결과를 문장으로 옮긴 것이며, 특정 종목이나 전략에 대한 투자 권유가 아닙니다.")
    return "\n\n".join(out)


TIER_TEXT = {"top": "팩터 점수 상위 30%", "good": "핵심 팩터에서 평균 이상",
             "diversify": "섹터·현금흐름 분산 목적"}


def ticker(holding, tier=None):
    """개별 종목이 왜 선택됐는지 — 팩터 점수와 배당 지표만 근거로."""
    fs = holding["factors"]
    order = sorted(fs.items(), key=lambda kv: -kv[1]["contribution"])
    strong = [v["label"] for _, v in order[:2]]
    weak = order[-1][1]
    usd = holding.get("currency", "USD" if holding["market"] == "US" else "KRW") == "USD"

    def per_share(v):
        return ("$%.4f" % v) if usd else ("{:,.0f}원".format(v))
    out = []
    out.append("## 무엇이 이 종목을 밀어 올렸나")
    s = ("%s의 종합 점수는 %.1f점입니다. 기여도가 가장 큰 팩터는 %s%s, "
         % (holding["name"], holding["score"], "·".join(strong),
            particle(strong[-1], "으로/로")))
    s += ", ".join("%s %.1f점 × 가중치 %s = %.1f"
                   % (v["label"], v["score"], pct(v["weight"]), v["contribution"])
                   for _, v in order[:3])
    s += " 순으로 쌓였습니다. 반대로 %s는 %.1f점에 그쳐 점수를 끌어내렸습니다." % (weak["label"], weak["score"])
    out.append(s)

    out.append("## 배당 지표")
    d = ("현재 배당수익률 %s(주당 연 %s), 5년 배당성장률 %s, 연 %g회 지급입니다."
         % (pct(holding["ttm_yield"], 2), per_share(holding["ttm_dividend"]),
            pct(holding["div_cagr"], 2), holding["payout_frequency"]))
    if holding["div_cut_count_10y"] == 0:
        d += " 최근 10년간 배당 삭감은 없었고 연속 증가는 %d년입니다." % holding["div_growth_streak"]
    else:
        d += (" 다만 최근 10년간 %d회 삭감이 있었고 현재 연속 증가는 %d년입니다."
              % (holding["div_cut_count_10y"], holding["div_growth_streak"]))
    d += (" 변동성은 연 %s, 최대낙폭은 %s, 데이터 이력은 %s년입니다."
          % (pct(holding["volatility"], 1), pct(holding["mdd"], 2), holding["history_years"]))
    out.append(d)

    out.append("## 비중이 이렇게 정해진 이유")
    w = ("이 조건에서 비중은 %s입니다. 비중은 종합 점수의 제곱에 비례해 정해지고, "
         "여기에 ETF 비중·미국 비중·섹터 30%% 상한이 함께 걸립니다 — "
         "그래서 점수 순서와 비중 순서가 어긋날 수 있습니다." % pct(holding["weight"], 2))
    if tier:
        w += " 이 종목은 %s에 해당합니다." % TIER_TEXT.get(tier, tier)
    w += (" 전망 기준 이 종목이 만들어내는 월 배당 기여액은 %s입니다."
          % exact(holding.get("monthly_dividend_contribution") or 0))
    out.append(w)

    out.append("위 숫자는 화면의 팩터 표·배당 지표와 같은 값이며, 투자 권유가 아닙니다.")
    return "\n\n".join(out)
