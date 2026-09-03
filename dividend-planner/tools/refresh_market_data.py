#!/usr/bin/env python3
"""시장 데이터를 공급자에서 새로 받아 data/market.json 을 갱신한다.

서버의 갱신 버튼(POST /api/refresh)과 같은 코드를 쓴다. 이 스크립트는 그걸
명령줄에서 돌리는 얇은 껍데기다 — cron 에 걸어 두거나 서버를 띄우기 전에
한 번 받아 두는 데 쓴다.

    python3 tools/refresh_market_data.py                 # 받아서 저장
    python3 tools/refresh_market_data.py --dry-run       # 받아만 보고 저장 안 함
    python3 tools/refresh_market_data.py --only SCHD,KO  # 일부 종목만

공급자 순서와 무엇을 주는지는 dividend_planner/providers.py 에 적어 뒀다.
배당 이력을 주는 공급자(야후·알파밴티지)에 닿지 못하면 캐시의 배당을 그대로 쓰고
그 사실을 리포트에 남긴다.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dividend_planner import refresh                    # noqa: E402
from dividend_planner.dataset import DATA_PATH, Dataset, load_pack   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="시장 데이터 갱신")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않고 결과만 보여준다")
    ap.add_argument("--only", help="쉼표로 구분한 종목 코드 (나머지는 캐시 유지)")
    ap.add_argument("--out", default=DATA_PATH)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    def progress(**kw):
        if args.quiet:
            return
        detail = kw.get("source") and "%s %s개월" % (kw["source"], kw.get("months", "?"))
        sys.stderr.write("  %2d/%-2d %-12s %s%s\n" % (
            kw["step"], kw["total"], kw["symbol"], detail or "받는 중…",
            " · " + kw["note"] if kw.get("note") else ""))

    cached = load_pack(args.out) if os.path.exists(args.out) else None
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    pack, report = refresh.refresh(cached, progress, only)

    fx = report["fx"]
    sys.stderr.write(
        "\n기준월 %s · 종목 %d개 · 환율 %s %s\n"
        % (report["as_of"], report["ticker_count"], fx["source"],
           ("%.2f원" % fx["latest"]) if fx["latest"] else "(지수)"))
    if report["failed"]:
        sys.stderr.write("갱신 실패 %d개: %s\n" % (
            len(report["failed"]), ", ".join(f["symbol"] for f in report["failed"])))
    for warning in report["warnings"]:
        sys.stderr.write("  · %s\n" % warning)

    Dataset(pack)              # 저장 전에 실제로 읽히는 팩인지 확인한다
    if args.dry_run:
        sys.stderr.write("--dry-run: 저장하지 않았습니다\n")
        return 0
    refresh.save(pack, args.out)
    sys.stderr.write("저장: %s\n" % args.out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write(
            "\n갱신 실패: %s\n"
            "기존 데이터 팩으로 서비스는 계속 동작합니다.\n" % exc)
        sys.exit(1)
