#!/usr/bin/env bash
# 로컬 실행 스크립트. 표준 라이브러리만 쓰므로 설치 단계가 없다.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-${PORT:-8770}}"
exec python3 server.py "$PORT"
