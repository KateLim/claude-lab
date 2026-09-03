"""설명 문장 생성. 두 경로가 있고 인터페이스는 같다.

1) ANTHROPIC_API_KEY가 있고 anthropic SDK가 설치돼 있으면 Claude로 서술한다.
2) 없으면 narrative.py의 규칙 기반 생성기로 서술한다.

어느 경로든 "표에 있는 숫자만 쓴다"는 계약은 같고, 같은 조건이면 캐시에서
즉시 돌려준다. SSE 이벤트(phase/delta/final/error)도 동일하다.
"""
import hashlib
import json
import os

from . import narrative

MODEL = os.environ.get("DIVIDEND_PLANNER_MODEL", "claude-opus-5")
MAX_TOKENS = 4096
CHUNK = 24                  # 로컬 생성기를 스트리밍처럼 흘려보낼 조각 크기

SYSTEM = """당신은 배당 포트폴리오 시뮬레이션 결과를 한국어로 설명하는 애널리스트입니다.

절대 규칙:
- 제공된 JSON에 있는 숫자만 사용합니다. 새로운 수치를 계산하거나 추정하지 않습니다.
- 종목명·비중·점수·수익률은 JSON 값을 그대로 인용합니다.
- 투자 권유·매수 추천 표현을 쓰지 않습니다. 시뮬레이션 결과 설명임을 마지막에 밝힙니다.
- 원화 금액은 "1,234,567원" 또는 "1.2억원"처럼 읽기 쉽게 씁니다.

형식:
- 마크다운. `## 제목` 4개 섹션, 각 섹션은 3~5문장의 한 단락.
- 전략 설명일 때 섹션 제목은 정확히: 이 전략의 핵심 / 왜 이렇게 배분했는가 / 짚어야 할 리스크 / 지금 할 일
- 종목 설명일 때 섹션 제목은 정확히: 무엇이 이 종목을 밀어 올렸나 / 배당 지표 / 비중이 이렇게 정해진 이유
- 마지막에 한 줄로 면책 문구를 덧붙입니다."""


def _digest(kind, payload):
    blob = json.dumps([kind, payload], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def strategy_facts(plan):
    """프롬프트에 넣을 요약. 월별 시계열은 통째로 빼고 요약값만 남긴다."""
    return {
        "request": plan["request"],
        "diagnosis": plan["diagnosis"],
        "weight_profile": plan["weight_profile"],
        "assumptions": plan["assumptions"],
        "sector_allocation": plan["sector_allocation"],
        "backtest_summary": plan["backtest"]["summary"],
        "projection_summary": plan["projection"]["summary"],
        "excluded": plan["excluded"],
        "portfolio": [
            {k: h[k] for k in ("symbol", "name", "market", "kind", "sector", "weight",
                               "ttm_yield", "div_cagr", "div_growth_streak",
                               "div_cut_count_10y", "payout_frequency", "score",
                               "monthly_dividend_contribution")}
            for h in plan["portfolio"]],
    }


def backend():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "local"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "local"
    return "claude"


def _local(text):
    yield "phase", {"phase": "composing", "label": "계산 결과로 설명을 구성하는 중"}
    for i in range(0, len(text), CHUNK):
        yield "delta", {"text": text[i:i + CHUNK]}
    yield "final", {"done": True, "stop_reason": "end_turn", "truncated": False,
                    "engine": "local"}


def _claude(prompt, fallback_text):
    import anthropic
    client = anthropic.Anthropic()
    yield "phase", {"phase": "analyzing", "label": "AI가 근거를 정리하는 중"}
    collected, stop_reason = [], "end_turn"
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                collected.append(text)
                yield "delta", {"text": text}
            stop_reason = stream.get_final_message().stop_reason or "end_turn"
    except Exception as exc:                                  # noqa: BLE001
        if collected:
            yield "error", {"message": "AI 응답이 중단되었습니다 — %s" % exc}
            return
        # 한 글자도 못 받았으면 로컬 생성기로 조용히 갈아탄다
        yield "phase", {"phase": "fallback",
                        "label": "AI 호출 실패 — 계산 결과로 직접 설명합니다"}
        for event in _local(fallback_text):
            if event[0] != "phase":
                yield event
        return
    if stop_reason == "refusal":
        yield "error", {"message": "모델이 응답을 거절했습니다."}
        return
    yield "final", {"done": True, "stop_reason": stop_reason,
                    "truncated": stop_reason == "max_tokens",
                    "engine": "claude", "model": MODEL}


class Explainer(object):
    def __init__(self):
        self.cache = {}

    def stream(self, kind, payload):
        key = _digest(kind, payload)
        if key in self.cache:
            yield "phase", {"phase": "cached", "label": "저장된 분석"}
            yield "delta", {"text": self.cache[key]}
            yield "final", {"done": True, "stop_reason": "end_turn",
                            "truncated": False, "cached": True}
            return

        if kind == "strategy":
            local_text = narrative.strategy(payload)
            facts = strategy_facts(payload)
            ask = "다음 시뮬레이션 결과로 '이 전략을 왜 이렇게 짰는가'를 설명해 주세요."
        else:
            holding = payload["holding"]
            local_text = narrative.ticker(holding, payload.get("tier"))
            facts = payload
            ask = ("다음 팩터 점수와 배당 지표로 '%s이(가) 왜 이 포트폴리오에 선택됐는가'를 "
                   "설명해 주세요." % holding["name"])
        prompt = "%s\n\n```json\n%s\n```" % (
            ask, json.dumps(facts, ensure_ascii=False, indent=1, default=str))

        chunks = []
        source = _claude(prompt, local_text) if backend() == "claude" else _local(local_text)
        for event, data in source:
            if event == "delta":
                chunks.append(data["text"])
            yield event, data
        text = "".join(chunks)
        if text:
            self.cache[key] = text
