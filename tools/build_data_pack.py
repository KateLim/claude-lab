#!/usr/bin/env python3
"""로컬 데이터 팩(data/market.json) 생성기.

입력은 종목별 월말 종가 / 월별 주당배당금 스냅샷이다. 스냅샷 디렉터리를 인자로
넘기면(기본 SNAP_DIR) 하나의 JSON으로 합친다. USD/KRW 월별 지수는 별도 파일에서
읽어 그대로 싣는다. 절대 환율 수준은 계산에 영향이 없으므로(구매·평가·배당 모두
환율에 선형이라 상수배가 상쇄된다) 첫 달을 1.0으로 정규화한 지수로 보관한다.
"""
import json, os, sys

SNAP_DIR = sys.argv[1] if len(sys.argv) > 1 else "snap"
FX_FILE = sys.argv[2] if len(sys.argv) > 2 else "fx_derived.json"
UNIVERSE = sys.argv[3] if len(sys.argv) > 3 else "universe.json"
OUT = sys.argv[4] if len(sys.argv) > 4 else "data/market.json"

uni = json.load(open(UNIVERSE, encoding="utf-8"))["tickers"]
fx = json.load(open(FX_FILE, encoding="utf-8"))
tickers = []
for t in uni:
    d = json.load(open(os.path.join(SNAP_DIR, t["symbol"] + ".json"), encoding="utf-8"))
    m = d["metrics"]
    tickers.append({
        "symbol": t["symbol"], "name": t["name"], "market": t["market"],
        "kind": t["kind"], "sector": t["sector"], "first_date": m["first_date"],
        "prices": [[p["date"], p["close"]] for p in d["price_series"]],
        "dividends": [[x["date"], x["dividend"]] for x in d["dividend_monthly"] if x["dividend"] > 0],
    })

fx_dates = sorted(fx)
base = fx[fx_dates[0]]
pack = {
    "as_of": max(p[0] for t in tickers for p in t["prices"]),
    "currency": {"US": "USD", "KR": "KRW"},
    "fx_usdkrw_index": [[d, fx[d] / base] for d in fx_dates],
    "tickers": tickers,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
print("wrote %s: %d tickers, prices %s~%s, fx %d months"
      % (OUT, len(tickers), min(p[0] for t in tickers for p in t["prices"]), pack["as_of"], len(pack["fx_usdkrw_index"])))
