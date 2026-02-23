# CI 규칙

## 기본 원칙

- CI 기본 파이프라인은 네트워크 비의존 검증만 수행한다.
- 온라인(DART API) 검증은 수동 트리거(`workflow_dispatch`) + 시크릿 존재 시에만 수행한다.
- Track C 품질게이트는 샘플/픽스처가 준비된 경우에만 `strict` 모드로 검증한다.

## 필수 체크 (기본 CI)

1. Python 문법 검증
```bash
python3 -m py_compile main.py src/*.py
```

2. 설정 스키마 검증
```bash
python3 main.py --check-config
```

3. 단위 테스트
```bash
python3 -m unittest discover -s tests -v
```

4. Track C strict 샘플 게이트(고정 fixture)
```bash
python3 main.py --step3-parse-xbrl --xbrl-dir tests/fixtures/trackc_strict_sample
```

5. 필수 CLI 플래그 확인
```bash
python3 main.py -h | grep -E -- "--check-network|--step8-strict-trackc|--step8-skip-trackc"
```

## 선택 체크 (온라인 CI)

조건:
- `workflow_dispatch`에서 `run_online_checks=true`
- `DART_API_KEY` 시크릿 존재

실행:
```bash
python3 main.py --check-network --verbose
python3 main.py --step1-latest-report --company-name 삼성전자 --year 2024 --report-code 11011
```

선택 strict 게이트:
- `workflow_dispatch`에서 `run_online_strict_trackc=true`
- 실행 스크립트:
```bash
python3 tests/online_trackc_strict_gate.py --years 2024 --min-success 1 --max-attempts 4
```

## Track C 정책의 CI 적용

- 일반 CI: Track C 미존재를 실패로 보지 않는다(기본 info 정책 유지).
- 기본 strict 게이트: `tests/fixtures/trackc_strict_sample` 파싱이 깨지면 실패 처리한다.
- 온라인 strict 게이트(선택): `tests/online_trackc_strict_gate.py`로 실공시 샘플을 자동 탐색해
  최소 1건 이상 `--step8-strict-trackc` 성공을 요구한다.
