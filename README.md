# claude-lab

Claude Code 로 만든 실습 모음.

| 디렉터리 | 내용 |
| --- | --- |
| [`dividend-planner/`](dividend-planner/) | 노후 배당 목표를 역산하고 포트폴리오를 설계하는 로컬 웹 서비스. 파이썬 표준 라이브러리만 사용, 빌드 단계 없음. |

`claude-projects.tar.gz` 는 프로젝트 백업 아카이브입니다.

## 배당 설계기 실행

```bash
cd dividend-planner
python3 -m unittest discover -s tests -t .   # 테스트 55개 (네트워크 없이 돎)
python3 tools/selfcheck.py --start           # 엔드포인트 12개를 실제로 두드림
python3 server.py                            # http://127.0.0.1:8770
```

자세한 내용은 [`dividend-planner/README.md`](dividend-planner/README.md), 작업 인계 메모는
[`dividend-planner/CLAUDE.md`](dividend-planner/CLAUDE.md) 를 보세요.
