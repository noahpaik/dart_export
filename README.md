# dart_export

DART 공시 데이터를 수집/정규화해 재무모델 엑셀(`data/*_model.xlsx`)을 생성하는 파이프라인입니다.

## 핵심 기능

- Track A: DART 재무제표 API(`fnlttSinglAcntAll`) 다개년 수집
- Track C: XBRL(`*_pre.xml`) 주석 구조 파싱
- Track B fallback: HTML/XML 원문 테이블 파싱 + 정규화
- Step6 선택 LLM 정규화: 호출 예산(`--step8-llm-max-calls`) + 캐시 정책(`--step8-normalizer-cache-policy`)
- Step8: End-to-End 실행 + 엑셀 출력
- Step8 요약 JSON 출력: `<output>_summary.json`
  - 스키마 버전 필드: `schema_version` (현재 `1.1`)
  - Track C 입력 소스: `track_c.source` (`xbrl_dir(option)`/`document.xml`/`fnlttXbrl.xml`)
  - 운영 지표: `metrics.runtime_ms`, `metrics.normalizer.cache_hit_rate`, `metrics.warning_types`
- 네트워크 진단: `--check-network` / `--check-network --verbose`

## 빠른 시작

1. 의존성 설치
```bash
python3 -m pip install -r requirements.txt
```

2. API 키 설정
```bash
cp config/settings.yaml config/settings.local.yaml  # 선택
# 또는 .env 파일에 DART_API_KEY=... 추가
```

3. 설정 검증
```bash
python3 main.py --check-config
```

4. 네트워크 점검
```bash
python3 main.py --check-network
python3 main.py --check-network --verbose
```

## 주요 실행 예시

- 최신 공시 조회
```bash
python3 main.py --step1-latest-report --company-name 삼성전자 --year 2024 --report-code 11011
```

- 파이프라인 실행(기본)
```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024
```

- 요약 JSON 경로 지정
```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024 --step8-summary-path data/samsung_summary.json
```

- Track C 엄격 모드
```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024 --step8-strict-trackc
```

- Step6 LLM 정규화(선택)
```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024 \
  --step8-enable-llm-normalize \
  --step8-llm-max-calls 10 \
  --step8-normalizer-cache-policy read_write
```

## Track C 운영 정책

- 기본 모드: Track C 입력(`*_pre.xml`)이 없으면 실패하지 않고 `info`로 건너뜁니다.
- 엄격 모드: `--step8-strict-trackc` 사용 시 Track C 미존재/파싱 실패를 즉시 오류 처리합니다.
- 옵션 충돌: `--step8-skip-trackc`와 `--step8-strict-trackc`는 함께 사용할 수 없습니다.
- Step8 로그에는 `trackc_mode`가 출력됩니다.
  - `parsed`
  - `skipped(option)`
  - `skipped(no_xbrl_dir)`
  - `failed(parse_error)`

## Track B 부문매출 정책

- `segment_rows=0`이어도 단일사업부문 공시(`부문별 기재 생략`)인 경우 정상 케이스로 `info`를 출력합니다.
- Step8 로그에는 `segment_mode`가 출력됩니다.
  - `parsed`
  - `skipped(single_segment_notice)`
  - `skipped(no_segment_data)`
  - `skipped(no_docs)`
- 예시 로그:
  - `Track B segment_revenue: 단일사업부문 공시로 부문별 매출 데이터가 없어 생략.`

## Step6 정규화 옵션

- 캐시 정책(`--step8-normalizer-cache-policy`)
  - `read_write` (기본): 캐시 읽기/쓰기
  - `read_only`: 캐시 읽기만, 미스 시 계산 후 캐시 미저장
  - `bypass`: 캐시 무시
- LLM 옵션(`--step8-enable-llm-normalize`)
  - 기본 비활성화
  - `--step8-llm-max-calls`로 1회 실행당 호출 상한 제한
  - `--step8-llm-min-unmapped`/`--step8-llm-max-unmapped`로 unmapped 계정 수 범위 제한

## 문서

- 구현 현황: `dart_pipeline_implementation_steps.md`
- 운영 가이드: `docs/OPERATIONS.md`
- CI 규칙: `docs/CI_RULES.md`
- 릴리스 노트: `docs/releases/v0.1.0.md`

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

Track C 고정 샘플 게이트:
```bash
python3 main.py --step3-parse-xbrl --xbrl-dir tests/fixtures/trackc_strict_sample
```

Track C 온라인 strict 게이트(선택):
```bash
python3 tests/online_trackc_strict_gate.py --candidates 삼성전자,SK하이닉스 --years 2024 --min-success 2 --max-attempts 2
```

Step8 온라인 통합 회귀 게이트(선택):
```bash
python3 tests/online_step8_integration_gate.py --companies 삼성전자,SK하이닉스,LG전자 --years 2024 --max-retries 3
```

Step8 온라인 분기/반기 회귀 게이트(선택):
```bash
python3 tests/online_step8_integration_gate.py \
  --companies 삼성전자,SK하이닉스,LG전자 \
  --years 2024 \
  --report-codes 11012,11013,11014 \
  --max-retries 2 \
  --min-success-per-report-code 1
```

Step8 온라인 연도 매트릭스 회귀 게이트(선택):
```bash
python3 tests/online_step8_integration_gate.py \
  --companies 삼성전자,SK하이닉스,LG전자 \
  --years 2022,2023,2024 \
  --report-codes 11011 \
  --max-retries 2 \
  --min-success-per-report-code 1
```

Step8 온라인 분기/반기 연도 매트릭스 회귀 게이트(선택):
```bash
python3 tests/online_step8_integration_gate.py \
  --companies 삼성전자,SK하이닉스,LG전자 \
  --years 2022,2023,2024 \
  --report-codes 11012,11013,11014 \
  --max-retries 2 \
  --min-success-per-report-code 1
```

Step8 경고 메트릭 집계(로컬):
```bash
python3 tests/collect_step8_warning_metrics.py \
  --summary-root /tmp/step8_online_artifacts \
  --output-json /tmp/step8_online_artifacts/metrics/step8_warning_metrics.json \
  --output-md /tmp/step8_online_artifacts/metrics/step8_warning_metrics.md
```

Step8 경고 메트릭 추이 리포트(로컬, 최근 5회):
```bash
python3 tests/collect_step8_warning_trends.py \
  --current-metrics-json /tmp/step8_online_artifacts/metrics/step8_warning_metrics.json \
  --history-dir /tmp/step8_online_artifacts/history \
  --output-json /tmp/step8_online_artifacts/metrics/step8_warning_trends.json \
  --output-md /tmp/step8_online_artifacts/metrics/step8_warning_trends.md \
  --recent-runs 5
```

온라인 CI에서 Step8 회귀를 실행하면 아티팩트 `step8-warning-metrics`가 업로드되며,
요약/집계/추이 결과(`metrics/step8_warning_metrics.json`, `metrics/step8_warning_metrics.md`,
`metrics/step8_warning_trends.json`, `metrics/step8_warning_trends.md`)를 함께 확인할 수 있습니다.

Track B `segment_revenue` 수동 회귀검증(로컬 `data/raw` 샘플 필요):
```bash
python3 tests/manual_segment_revenue_regression.py
```
