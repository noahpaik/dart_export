# CI 규칙

## 기본 원칙

- CI 기본 파이프라인은 네트워크 비의존 검증만 수행한다.
- 온라인(DART API) 검증은 수동 트리거(`workflow_dispatch`) 또는 정기 배치(`schedule`) + 시크릿 존재 시에만 수행한다.
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
python3 main.py -h | grep -E -- "--check-network|--step8-strict-trackc|--step8-skip-trackc|--step8-enable-llm-normalize|--step8-normalizer-cache-policy"
```

## 선택 체크 (온라인 CI)

조건:
- `workflow_dispatch`에서 `run_online_checks=true` 또는 `schedule` 이벤트
- `DART_API_KEY` 시크릿 존재
- 회귀 매트릭스/검증 규칙: `config/online_step8_matrix.yaml`

실행:
```bash
python3 main.py --check-network --verbose
python3 main.py --step1-latest-report --company-name 삼성전자 --year 2024 --report-code 11011
```

선택 strict 게이트:
- `workflow_dispatch`에서 `run_online_strict_trackc=true`
- 고정 조합: `삼성전자(2024)`, `SK하이닉스(2024)`
- 실행 스크립트:
```bash
python3 tests/online_trackc_strict_gate.py --candidates 삼성전자,SK하이닉스 --years 2024 --min-success 2 --max-attempts 2
```

선택 Step8 통합 회귀 게이트:
- `workflow_dispatch`에서 `run_online_step8_regression=true`
- 고정 조합: `삼성전자(2024)`, `SK하이닉스(2024)`, `LG전자(2024)`
- 실행 스크립트:
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix base
```

선택 Step8 분기/반기 회귀 게이트:
- `workflow_dispatch`에서 `run_online_step8_multi_report_regression=true`
- 대상 보고서코드: `11012`, `11013`, `11014`
- 실행 스크립트:
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix multi_report
```

선택 Step8 분기/반기 연도 매트릭스 회귀 게이트:
- `workflow_dispatch`에서 `run_online_step8_multi_report_year_matrix_regression=true`
- 입력 연도: `2022,2023,2024`
- 대상 보고서코드: `11012`, `11013`, `11014`
- 실행 스크립트:
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix multi_report_year_matrix
```

선택 Step8 연도 매트릭스 회귀 게이트:
- `workflow_dispatch`에서 `run_online_step8_year_matrix_regression=true`
- 입력 연도: `2022,2023,2024` (report_code=`11011`)
- 실행 스크립트:
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix year_matrix
```

온라인 Step8 회귀 실행 시 메트릭 아티팩트:
- 수동/정기 배치 모두 동일한 경로를 사용한다:
  - `/tmp/step8_online_artifacts/{base,multi_report,multi_report_year_matrix,year_matrix}`
- CI는 회귀 실행 요약을 `/tmp/step8_online_artifacts/{base,multi_report,multi_report_year_matrix,year_matrix}`에 저장한다.
- `tests/collect_step8_warning_metrics.py`로 `metrics.warning_types`/runtime/mode를 집계한다.
- `tests/collect_step8_warning_trends.py`로 최근 N회(`--recent-runs`) 추이 리포트를 생성한다.
  - GitHub Actions API에서 `step8-warning-metrics` 아티팩트를 조회해 history를 보강한다.
  - 품질게이트 임계치(기본): `config/step8_warning_quality_gate.yaml`
  - 연간(`11011`)은 엄격, 분기/반기(`11012/11013/11014`)는 완화 임계치로 분리 운영
  - 임계치 초과 시 `--fail-on-quality-gate`로 온라인 CI 실패 처리
- 업로드 아티팩트 이름: `step8-warning-metrics`
- 핵심 결과 파일:
  - `/tmp/step8_online_artifacts/metrics/step8_warning_metrics.json`
  - `/tmp/step8_online_artifacts/metrics/step8_warning_metrics.md`
  - `/tmp/step8_online_artifacts/metrics/step8_warning_trends.json`
  - `/tmp/step8_online_artifacts/metrics/step8_warning_trends.md`

정기 배치(`schedule`) 기본 실행 범위:
- `Track C Strict Online Gate`
- `Step8 Integration Online Regression`
- `Step8 Multi-Report Regression`
- `Step8 Multi-Report Year-Matrix Regression`
- `Step8 Year-Matrix Regression`
- 이후 메트릭 집계/추이/아티팩트 업로드 단계

## Track C 정책의 CI 적용

- 일반 CI: Track C 미존재를 실패로 보지 않는다(기본 info 정책 유지).
- 기본 strict 게이트: `tests/fixtures/trackc_strict_sample` 파싱이 깨지면 실패 처리한다.
- 온라인 strict 게이트(선택): `tests/online_trackc_strict_gate.py`로 고정 실공시 조합
  (`삼성전자/2024`, `SK하이닉스/2024`)을 검증해 최소 2건 `--step8-strict-trackc` 성공을 요구한다.
- 온라인 Step8 통합 회귀 게이트(선택): `tests/online_step8_integration_gate.py`로
  고정 실공시 조합(`삼성전자/2024`, `SK하이닉스/2024`, `LG전자/2024`)의
  Step8 요약 JSON(`track_a`, `track_b_fallback`, `track_c`, `metrics`)을 검증한다.
- 온라인 Step8 분기/반기 회귀 게이트(선택): 동일 고정 조합으로
  `11012/11013/11014` 각 보고서코드에서 최소 성공 건수(`--min-success-per-report-code`)를 검증한다.
- 온라인 Step8 분기/반기 연도 매트릭스 회귀 게이트(선택): 동일 고정 조합으로
  `11012/11013/11014`와 다개년 입력(`2022,2023,2024`)을 함께 검증한다.
- 온라인 Step8 연도 매트릭스 회귀 게이트(선택): 동일 고정 조합으로
  다개년 입력(`2022,2023,2024`)의 요약 `years` 일치 여부를 검증한다.
