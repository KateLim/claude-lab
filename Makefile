# 표준 라이브러리만 쓰는 프로젝트라 설치 단계가 없다. 이 파일은 자주 쓰는 명령의 단축키일
# 뿐이라 make 가 없는 머신에서도 아래 python3 명령을 그대로 쳐도 된다 (README 참고).
PY ?= python3
PORT ?= 8770

.PHONY: help run test check refresh clean

help:
	@echo "make run       서버 실행 (http://127.0.0.1:$(PORT))"
	@echo "make test      테스트 전체 (네트워크 없이 돎)"
	@echo "make check     서버를 띄워 엔드포인트 전체를 실제로 두드림"
	@echo "make refresh   시장 데이터 갱신 (인터넷 필요)"
	@echo "make clean     캐시·임시 파일 정리 (데이터 캐시는 seed 에서 복원됨)"

run:
	$(PY) server.py $(PORT)

test:
	$(PY) -m unittest discover -s tests -t .

check:
	$(PY) tools/selfcheck.py --start

refresh:
	$(PY) tools/refresh_market_data.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f data/market.json data/*.tmp tests/_tmp_*
