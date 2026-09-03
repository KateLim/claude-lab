"""나이·소득만으로 노후 배당 목표를 역산한다.

목표 금액을 모르는 사용자를 위해 (1) 비운 항목은 통계로 추정하고 무엇을 추정했는지
밝히며, (2) 은퇴 후 필요 생활비를 현재 지출의 70%로 잡되 국민연금연구원 노후생활비
통계보다 낮아지면 통계값을 하한으로 쓰고, (3) 물가상승률로 은퇴 시점 금액으로 환산한
뒤 연금을 빼서 "배당으로 채워야 하는 월 금액"을 만든다.
"""
REPLACEMENT_RATIO = 0.7          # 은퇴 후 필요 생활비 / 현재 지출
INFLATION = 0.025
PROPENSITY_TO_CONSUME = 0.65     # 세후 소득 대비 평균 소비성향

# 연령대별 세후 월 중위소득 (통계 기반 기본값)
MEDIAN_INCOME_BY_AGE = {20: 2600000, 30: 3600000, 40: 4100000, 50: 4000000, 60: 2800000}

HOUSING_ADDON = {"owned": 0, "owned_loan": 0, "jeonse": 150000, "monthly_rent": 500000}

# 국민연금연구원 노후보장패널 기준 노후생활비 (가구 구성별 하한)
LIVING_COST_FLOOR = {
    "single":          {"minimum": 1240000, "adequate": 2050000, "comfortable": 2800000},
    "couple":          {"minimum": 1980000, "adequate": 3240000, "comfortable": 4400000},
    "couple_children": {"minimum": 2400000, "adequate": 3800000, "comfortable": 5200000},
    "single_parent":   {"minimum": 1800000, "adequate": 2800000, "comfortable": 3800000},
}
SCENARIOS = [("minimum", "최소 노후", 0.78), ("adequate", "적정 노후", 1.0),
             ("comfortable", "여유 노후", 1.3)]
FLOOR_SOURCE = "국민연금연구원 노후보장패널 기준 노후생활비"


def median_income(age):
    """가장 가까운 연령대의 중위소득. 동점이면 아래 연령대를 쓴다."""
    band = min(MEDIAN_INCOME_BY_AGE, key=lambda b: (abs(age - b), b))
    return MEDIAN_INCOME_BY_AGE[band]


def compute(profile, portfolio_yield):
    age = int(profile["age"])
    retire_age = int(profile["retire_age"])
    if retire_age <= age:
        raise ValueError("은퇴 나이가 현재 나이보다 커야 합니다")
    horizon = retire_age - age
    household = profile.get("household_type") or "couple"
    housing = profile.get("housing") or "owned"

    estimated = []
    income = profile.get("monthly_income_after_tax_krw")
    if income is None:
        income = median_income(age)
        estimated.append({"field": "monthly_income_after_tax_krw", "label": "세후 월 소득",
                          "value": income, "basis": "%d대 중위소득 통계" % (age // 10 * 10)})
    spending = profile.get("monthly_spending_krw")
    if spending is None:
        spending = round(income * PROPENSITY_TO_CONSUME)
        estimated.append({"field": "monthly_spending_krw", "label": "월 지출",
                          "value": spending,
                          "basis": "세후 소득 × 평균 소비성향 %d%%" % (PROPENSITY_TO_CONSUME * 100)})
    contribution = max(0, round(income - spending))
    estimated.append({"field": "monthly_contribution_krw", "label": "월 투자 가능액",
                      "value": contribution, "basis": "세후 소득 − 월 지출"})

    addon = HOUSING_ADDON.get(housing, 0)
    floors = LIVING_COST_FLOOR.get(household, LIVING_COST_FLOOR["couple"])
    pension = profile.get("pension_monthly_krw") or 0
    inflator = (1 + INFLATION) ** horizon

    scenarios = {}
    for key, label, multiplier in SCENARIOS:
        raw = spending * REPLACEMENT_RATIO * multiplier + addon
        floor = floors[key]
        today = round(max(raw, floor))
        at_retirement_exact = today * inflator
        at_retirement = round(at_retirement_exact)
        target_exact = max(0.0, at_retirement_exact - pension)
        target = max(0, round(at_retirement - pension))
        scenarios[key] = {
            "key": key, "label": label,
            "living_cost_today": today,
            "living_cost_at_retirement": at_retirement,
            "floor_applied": floor > raw,
            "floor_value": floor,
            "target_monthly_dividend": target,
            # 필요 총자산은 반올림 전 값으로 계산해 표시값과 자잘한 차이가 없게 한다
            "required_assets": target_exact * 12 / portfolio_yield if portfolio_yield else 0.0,
        }

    return {
        "age": age, "retire_age": retire_age, "horizon_years": horizon,
        "monthly_income_after_tax_krw": income, "monthly_spending_krw": spending,
        "household_type": household, "housing": housing, "housing_addon_krw": addon,
        "pension_monthly_krw": pension,
        "current_assets_krw": profile.get("current_assets_krw") or 0,
        "monthly_contribution_krw": contribution,
        "scenarios": scenarios,
        "estimated_fields": estimated,
        "assumptions": {
            "replacement_ratio": REPLACEMENT_RATIO,
            "inflation": INFLATION,
            "propensity_to_consume": PROPENSITY_TO_CONSUME,
            "portfolio_yield_used": portfolio_yield,
            "living_cost_floor_source": FLOOR_SOURCE,
        },
    }
