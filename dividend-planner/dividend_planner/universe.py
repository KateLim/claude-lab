"""유니버스 정의 — 배당 목적으로 선별한 36종목.

데이터 갱신 스크립트(tools/refresh_market_data.py)와 번들 데이터 팩이 공유하는
단일 출처다. 종목을 넣거나 빼려면 여기만 고치고 갱신 스크립트를 다시 돌린다.
이름·섹터는 화면 표기용 라벨이며, 점수 계산에는 섹터만 (30% 상한) 쓰인다.
"""

UNIVERSE = [
    {"symbol": "SCHD",       "name": "Schwab 미국 배당주 ETF",       "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "VYM",        "name": "Vanguard 고배당 ETF",        "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "DGRO",       "name": "iShares 배당성장 ETF",        "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "VIG",        "name": "Vanguard 배당성장 ETF",       "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "HDV",        "name": "iShares 코어 고배당 ETF",      "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "SPHD",       "name": "S&P500 고배당 저변동 ETF",      "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "NOBL",       "name": "S&P 배당귀족 ETF",            "market": "US",  "kind": "etf",   "sector": "ETF"},
    {"symbol": "JNJ",        "name": "존슨앤드존슨",                  "market": "US",  "kind": "stock", "sector": "헬스케어"},
    {"symbol": "ABBV",       "name": "애브비",                     "market": "US",  "kind": "stock", "sector": "헬스케어"},
    {"symbol": "PG",         "name": "프록터앤드갬블",                 "market": "US",  "kind": "stock", "sector": "필수소비재"},
    {"symbol": "KO",         "name": "코카콜라",                    "market": "US",  "kind": "stock", "sector": "필수소비재"},
    {"symbol": "PEP",        "name": "펩시코",                     "market": "US",  "kind": "stock", "sector": "필수소비재"},
    {"symbol": "MO",         "name": "알트리아",                    "market": "US",  "kind": "stock", "sector": "필수소비재"},
    {"symbol": "XOM",        "name": "엑슨모빌",                    "market": "US",  "kind": "stock", "sector": "에너지"},
    {"symbol": "CVX",        "name": "셰브론",                     "market": "US",  "kind": "stock", "sector": "에너지"},
    {"symbol": "VZ",         "name": "버라이즌",                    "market": "US",  "kind": "stock", "sector": "통신"},
    {"symbol": "MCD",        "name": "맥도날드",                    "market": "US",  "kind": "stock", "sector": "경기소비재"},
    {"symbol": "HD",         "name": "홈디포",                     "market": "US",  "kind": "stock", "sector": "경기소비재"},
    {"symbol": "TXN",        "name": "텍사스인스트루먼트",               "market": "US",  "kind": "stock", "sector": "기술"},
    {"symbol": "LMT",        "name": "록히드마틴",                   "market": "US",  "kind": "stock", "sector": "산업재"},
    {"symbol": "ADP",        "name": "오토매틱데이터프로세싱",             "market": "US",  "kind": "stock", "sector": "산업재"},
    {"symbol": "O",          "name": "리얼티인컴 (월배당 리츠)",          "market": "US",  "kind": "stock", "sector": "리츠"},
    {"symbol": "MAIN",       "name": "메인스트리트캐피털 (월배당)",         "market": "US",  "kind": "stock", "sector": "금융"},
    {"symbol": "STAG",       "name": "스택인더스트리얼 (월배당)",          "market": "US",  "kind": "stock", "sector": "리츠"},
    {"symbol": "VICI",       "name": "비치프로퍼티스",                 "market": "US",  "kind": "stock", "sector": "리츠"},
    {"symbol": "AMT",        "name": "아메리칸타워",                  "market": "US",  "kind": "stock", "sector": "리츠"},
    {"symbol": "NEE",        "name": "넥스트에라에너지",                "market": "US",  "kind": "stock", "sector": "유틸리티"},
    {"symbol": "DUK",        "name": "듀크에너지",                   "market": "US",  "kind": "stock", "sector": "유틸리티"},
    {"symbol": "005930.KS",  "name": "삼성전자",                    "market": "KR",  "kind": "stock", "sector": "기술"},
    {"symbol": "033780.KS",  "name": "KT&G",                    "market": "KR",  "kind": "stock", "sector": "필수소비재"},
    {"symbol": "086790.KS",  "name": "하나금융지주",                  "market": "KR",  "kind": "stock", "sector": "금융"},
    {"symbol": "105560.KS",  "name": "KB금융",                    "market": "KR",  "kind": "stock", "sector": "금융"},
    {"symbol": "055550.KS",  "name": "신한지주",                    "market": "KR",  "kind": "stock", "sector": "금융"},
    {"symbol": "017670.KS",  "name": "SK텔레콤",                   "market": "KR",  "kind": "stock", "sector": "통신"},
    {"symbol": "030200.KS",  "name": "KT",                      "market": "KR",  "kind": "stock", "sector": "통신"},
    {"symbol": "010950.KS",  "name": "S-Oil",                   "market": "KR",  "kind": "stock", "sector": "에너지"},
]

FX_SYMBOL = "KRW=X"          # 원달러 환율 (야후 파이낸스 티커)

BY_SYMBOL = dict((t["symbol"], t) for t in UNIVERSE)
