# dart_export

DART 공시 데이터를 수집/정규화해 재무모델 엑셀(`data/*_model.xlsx`)을 생성하는 파이프라인입니다.

## 핵심 기능

- Track A: DART 재무제표 API(`fnlttSinglAcntAll`) 다개년 수집
- Track C: XBRL(`*_pre.xml`) 주석 구조 파싱
- Track B fallback: HTML/XML 원문 테이블 파싱 + 정규화
- Step8: End-to-End 실행 + 엑셀 출력
- Step8 요약 JSON 출력: `<output>_summary.json`
  - 스키마 버전 필드: `schema_version` (현재 `1.0`)
  - Track C 입력 소스: `track_c.source` (`xbrl_dir(option)`/`document.xml`/`fnlttXbrl.xml`)
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

## 문서

- 구현 현황: `dart_pipeline_implementation_steps.md`
- 운영 가이드: `docs/OPERATIONS.md`
- CI 규칙: `docs/CI_RULES.md`

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
python3 tests/online_trackc_strict_gate.py --years 2024 --min-success 1 --max-attempts 4
```
