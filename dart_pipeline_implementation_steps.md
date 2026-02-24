# DART 파이프라인 구현 현황 및 다음 작업

업데이트 일시: 2026-02-24

## 1. 구현 원칙

- 기본 경로는 `Track A + Track C` 우선
- `Track B(문서 분류 + HTML/XML 테이블 파싱)`는 fallback
- 모든 단계는 재실행 가능(idempotent)하게 유지

## 2. 현재 진행 현황 요약

| Step | 상태 | 비고 |
|---|---|---|
| Step 0 | 완료 | 스캐폴딩/설정/CLI 기본 진입점 완료 |
| Step 1 | 완료 | DART 연동 + corp_code DB + 최신공시/원문 다운로드 + 네트워크 복원력 보강 |
| Step 2 | 완료 | `fnlttSinglAcntAll` 수집 + 시계열 병합 구현 + 네트워크 복원력 보강 |
| Step 3 | 완료 | XBRL `pre/lab` 파싱 + 역할/계정/멤버 추출 구현 |
| Step 4 | 완료 | `cal.xml` 검증 + `def.xml` 구조 파싱 구현 |
| Step 5 | 진행중 | 문서분류/HTML(XML 포함) 파싱 구현 완료, 품질 튜닝 남음 |
| Step 6 | 진행중 | 정규화/캐시 연동 + LLM 옵션/예산/캐시정책 반영 완료, 운영 튜닝 남음 |
| Step 7 | 완료 | `excel_writer.py` 구현 + Step8 파이프라인 통합 완료 |
| Step 8 | 진행중 | E2E 구동/Track B 반영 완료, Track C 미존재 정책(정보/엄격옵션/상태코드) 반영 완료 |
| Step 9 | 진행중 | `tests/` 스켈레톤 + 핵심 단위 테스트 + CI 연동 완료, 통합 회귀검증은 남음 |

## 3. 이번 사이클에서 완료한 핵심 항목

### A. 최신 공시 조회/필터 정확도 수정

- `src/dart_api.py`의 `report_code` 필터 버그 수정
- 기존: `report_nm` 문자열에 `11011` 코드 포함 여부 비교
- 개선: 보고서명/월 기준 판별(`사업/반기/1분기/3분기`)로 정확 매칭

### B. Step8 Track B fallback 실동작 연결

- `main.py` Step8 fallback 입력 확장자에 `.xml` 추가
- `src/document_classifier.py`에서 `.xml` 지원
- 감사보고서(`audit`)라도 주석 신호가 강하면 `notes`로 분류 허용
- `src/html_parser.py` XML 파싱 경고 억제(`XMLParsedAsHTMLWarning`)
- `main.py` 연도 컬럼 추출 보강
  - `2024` 같은 직접 연도 외에 `당기/전기/전전기`를 최신공시 기준연도로 매핑
  - `제55기/제54기` 같은 회기 표기를 최신공시 기준연도로 보정
- `src/html_parser.py` 부문매출 분류 정밀화
  - `영업부문` 키워드만으로 매칭하지 않고 `매출/수익` 동시 존재 조건 적용
- `main.py` 부문매출 보강 로직 추가
  - notes 문서에서 `segment_revenue`가 비어 있으면 `business` 문서를 추가 탐색
  - 테이블 품질 필터(부문 컬럼/연도 컬럼/숫자행 조건) 통과 시만 반영
  - 단일사업부문 공시 문구 감지 시 `info`로 정상 스킵 사유 출력
  - Step8 `TrackB fallback`에 `segment_mode` 상태코드 출력
- Step8 실행 요약 JSON 자동 저장 추가
  - 기본 경로: `<output>_summary.json`
  - 사용자 지정 경로: `--step8-summary-path`

### C. 네트워크 안정성 및 진단 기능 추가

- `src/dart_api.py`
  - `requests` 세션 재시도(`Retry`, `HTTPAdapter`) 추가
  - connect/read timeout 분리
  - 네트워크 예외 분류 보강(`dns`, `timeout`, `ssl`, `connection`, `http`)
- `src/financial_statements.py`
  - `fnlttSinglAcntAll` 호출 세션 재시도(`Retry`, `HTTPAdapter`) 추가
  - connect/read timeout 분리
  - 네트워크 예외 분류 보강(`dns`, `timeout`, `ssl`, `connection`, `http`)
- `main.py`
  - `--check-network` 추가
  - `--check-network --verbose` 추가
  - DNS/HTTPS/API key 상태, 응답시간(ms), 재시도 횟수 출력

### D. Track C 미존재 정책 반영 (1순위 착수/구현)

- `main.py` Step8 옵션 추가
  - `--step8-strict-trackc`: Track C 미파싱(디렉토리 없음/파싱실패) 시 즉시 실패
- 기본 정책 변경
  - Track C 미존재(`*_pre.xml` 없음)는 `warning`이 아니라 `info`로 출력
- 상태코드 출력 추가
  - Step8 로그에 `trackc_mode` 출력 (`parsed`, `skipped(option)`, `skipped(no_xbrl_dir)`, `failed(parse_error)`)

### E. 실측 실행 결과(2022~2024, 회귀 샘플)

- 삼성전자
  - TrackA: `BS=69`, `IS=19`, `CF=36`
  - TrackB fallback: `docs=2`, `tables=4`, `sga_rows=8`, `segment_rows=5`
  - Excel write: `판관비 cells=16 matched=8`, `부문별매출 cells=15 matched=5`
- 현대자동차
  - TrackA: `BS=72`, `IS=25`, `CF=46`
  - TrackB fallback: `docs=1`, `tables=7`, `sga_rows=6`, `segment_rows=2`
  - Excel write: `판관비 cells=10 matched=6`, `부문별매출 cells=6 matched=2`
- SK하이닉스
  - TrackA: `BS=68`, `IS=0`, `CF=41`
  - TrackB fallback: `docs=2`, `tables=1`, `sga_rows=9`, `segment_rows=0`
  - 비고: 공시에 단일사업부문 안내가 있어 부문매출 추출이 없는 케이스
- Track C 미존재 기본 출력
  - `TrackC: mode=skipped(no_xbrl_dir)` + `infos` 항목으로 표시
  - 원인: 해당 원문 패키지에 `*_pre.xml` 기반 XBRL 링크베이스 부재
- `--step8-strict-trackc` 실행 시
  - `Track C: XBRL 디렉토리를 찾지 못해 생략. (--step8-strict-trackc)`로 실패 처리 확인

### F. Step9 테스트 스켈레톤/회귀 기반 구축 (4순위 착수)

- `tests/` 디렉토리 생성 및 핵심 단위 테스트 추가
  - `tests/test_dart_api.py`
  - `tests/test_document_classifier.py`
  - `tests/test_main_helpers.py`
  - `tests/test_html_parser.py`
  - `tests/test_xbrl_parser.py`
  - `tests/test_financial_statements.py`
- Step8 요약 JSON 스키마 고정
  - `schema_version` 필드 추가 (`1.1`)
  - `build_step8_summary_payload()` 함수 분리로 스키마 생성 로직 단일화
- CI 오프라인 검증에 단위 테스트 단계 추가
  - `python -m unittest discover -s tests -v`
- Track C strict 샘플 셋 확정/게이트 연결
  - 샘플: `tests/fixtures/trackc_strict_sample` (`sample_pre.xml`, `sample_lab-ko.xml`)
  - CI 게이트: `python main.py --step3-parse-xbrl --xbrl-dir tests/fixtures/trackc_strict_sample`
- 온라인 strict 게이트 초안 추가
  - 스크립트: `tests/online_trackc_strict_gate.py`
  - CI `workflow_dispatch` 입력: `run_online_strict_trackc=true`
  - 동작: 실공시 후보를 순차 실행해 최소 1건 `--step8-strict-trackc` 성공 요구
- 단위 테스트 기준치 갱신
  - 현재 기준 `31 tests` 통과

### G. Track C 입력 보강 (fnlttXbrl fallback)

- `src/dart_api.py`에 `download_fnltt_xbrl()` 추가
- Step8에서 `document.xml`에 `*_pre.xml`이 없으면 `fnlttXbrl.xml` 자동 시도
- Step8 로그/요약에 `track_c.source` 추가
  - `xbrl_dir(option)`
  - `document.xml`
  - `fnlttXbrl.xml`

### H. 릴리스/배포 메타 정리

- 릴리스 노트 초안 추가: `docs/releases/v0.1.0.md`
- 태그 생성: `v0.1.0` (기준 커밋: `0673f8c`)
- GitHub 원격 저장소 연결: `git@github.com:noahpaik/dart_export.git`

### I. Step5 `segment_revenue` 타사 회귀검증 (오프라인 샘플)

- 수동 회귀 스크립트 추가: `tests/manual_segment_revenue_regression.py`
- 검증 샘플(기보관 원문): `삼성전자`, `SK하이닉스`, `현대자동차`
- 실행 결과:
  - 삼성전자: `accepted=1` (부문매출 표 1건 파싱)
  - SK하이닉스: `accepted=0`, `single_segment_notices=1` (단일사업부문 정상 스킵)
- 현대자동차: `accepted=1` (부문매출 표 1건 파싱)
- 회귀 실행 결과: `PASS`

### J. 문서분류 점수 기준 샘플사 튜닝

- `src/document_classifier.py`에 회사명 기반 프로파일(`manufacturing`/`finance`/`general`) 추가
- 샘플사 오버라이드 매핑 추가
  - 제조: `삼성전자`, `SK하이닉스`, `현대자동차`, `LG전자`, `POSCO홀딩스`
  - 금융: `KB금융`, `신한금융지주`, `하나금융지주`, `우리금융지주`, `메리츠금융지주`, `삼성생명`, `삼성화재`
- 프로파일별 분류 임계치/키워드 보강
  - 금융: `notes` 임계치 완화 + 금융 고유 키워드 가중치(`순이자수익`, `대손충당금` 등)
  - 제조: `매출실적`, `수주상황` 신호 가중치 보강
- Step8 연동
  - `main.py`에서 `DocumentClassifier(company_name=args.company_name)` 사용
- 검증
  - `tests/test_document_classifier.py` 프로파일 튜닝 테스트 2건 추가
  - 전체 단위 테스트 `31 tests` 통과

### K. Step6 taxonomy alias 확장

- `config/taxonomy.yaml` alias 확장
  - `sga_detail`: 인건비/수수료/운반비/총계 계열 동의어 보강
  - `segment_revenue`: 부문명 표기 변형(`기 타`, `DX부문`, `합 계` 등) 정규화 보강
- `sga_detail.standard_accounts`에 `총계` 추가
  - 합계/소계/계 라벨이 `기타판관비`로 뭉개지지 않도록 분리
- 정규화 회귀 테스트 추가: `tests/test_account_normalizer.py` (4건)
  - alias 매핑 및 기본 fallback 동작 검증
- 검증
  - `python3 main.py --check-config`
  - `python3 -m unittest discover -s tests -v` (`31 tests` 통과)

### L. Step6 숫자 노이즈/총계 행 처리 룰 강화

- `main.py`에 Step8 행 필터 헬퍼 추가
  - `normalize_step8_row_label()`
  - `is_step8_total_row_label()`
  - `is_step8_noise_row_label()`
  - `should_skip_step8_row()`
- 적용 범위
  - Track B 집계 시 `sga_detail`/`segment_revenue` 모두에서 노이즈 행/총계 행 제외
  - `segment_revenue`는 통화 코드(`USD`, `EUR`, `JPY`) 및 가격변동형 행 제외
- 검증
  - `tests/test_main_helpers.py`에 행 필터 테스트 3건 추가
  - `tests/manual_segment_revenue_regression.py` 회귀 `PASS` 유지
  - 전체 단위 테스트 `31 tests` 통과

### M. Step8 운영 지표 자동 집계

- Step8 요약 JSON(`schema_version=1.1`)에 `metrics` 필드 추가
  - `runtime_ms`
  - `normalizer` 사용량/캐시 지표(`cache_hit_rate` 포함)
  - `warning_types` 집계
- `tests/online_step8_integration_gate.py`에서 `metrics` 필드 검증 추가
- `tests/test_account_normalizer.py`에 usage 집계 테스트 추가
- `tests/test_main_helpers.py`에 warning 유형 집계 테스트 추가

### N. Step8 분기/반기 온라인 회귀 확장

- `tests/online_step8_integration_gate.py` 확장
  - `--report-codes` (콤마 구분)
  - `--min-success-per-report-code` (코드별 최소 성공 건수)
  - 비연간(11012/11013/11014)에서는 fallback `segment_mode` 허용 범위를 완화해 false fail 축소
- CI `workflow_dispatch` 입력 추가
  - `run_online_step8_multi_report_regression=true`
- 실측 결과(2024, 고정 3사, max_retries=2):
  - `11012`: PASS 3 / FAIL 0
  - `11013`: PASS 3 / FAIL 0
  - `11014`: PASS 3 / FAIL 0
  - 전체: PASS 9 / FAIL 0

### O. Step8 연도 매트릭스 온라인 회귀 확장

- `tests/online_step8_integration_gate.py` 확장
  - 요약 `years` 필드가 입력 연도(`--years`)와 일치하는지 검증
- CI `workflow_dispatch` 입력 추가
  - `run_online_step8_year_matrix_regression=true`
- 실측 결과(연간 `11011`, 고정 3사, years=`2022,2023,2024`, max_retries=2):
  - PASS 3 / FAIL 0

### P. Step8 경고 메트릭 CI 아티팩트 집계 자동화

- `tests/collect_step8_warning_metrics.py` 추가
  - `*_summary.json` 재귀 탐색
  - `metrics.warning_types`/runtime/track mode/report_code/year_set 집계
  - JSON + Markdown 리포트 동시 출력
- 단위 테스트 추가: `tests/test_collect_step8_warning_metrics.py`
  - 집계 합산 정확성/Markdown 섹션 렌더링 검증
- CI 반영:
  - `STEP8_ARTIFACT_ROOT=/tmp/step8_online_artifacts`
  - 온라인 Step8 회귀별 `--output-dir` 분리(`base`, `multi_report`, `year_matrix`)
  - 집계 단계 실행 후 아티팩트 `step8-warning-metrics` 업로드
- 검증:
  - `python3 -m unittest discover -s tests -v` 통과

## 4. 앞으로 해야 할 것 (우선순위)

### 1순위: Track C 운영 정책 후속 정리 (Step8)

- 완료됨: `*_pre.xml` 없음 기본 `info` 처리
- 완료됨: `--step8-strict-trackc` 옵션 도입
- 완료됨: `trackc_mode` 상태코드 로그 출력
- 완료됨: 기본 정책을 README/운영가이드/CI 규칙에 반영
- 완료됨: CI strict 게이트용 고정 XBRL 샘플(`tests/fixtures/trackc_strict_sample`) 확정
- 완료됨: 실공시 기반 온라인 strict 선택 게이트 추가(`run_online_strict_trackc`)
- 완료됨: 온라인 strict 게이트 실측 PASS 2건 확보(삼성전자/2024, SK하이닉스/2024)
- 완료됨: 온라인 게이트 통과 실공시 조합(회사/연도) 2건 고정 + CI 반영

### 2순위: Step5 파싱 품질 고도화

- 완료됨: `segment_revenue` 추출 보강(삼성전자/현대차 회귀에서 유효 row 확인)
- 완료됨: 타사 샘플 3개(`삼성전자`, `SK하이닉스`, `현대자동차`) 회귀검증
- 병합셀/다중헤더/주석형 텍스트 테이블 제외 규칙 보강
- 완료됨: 문서분류 점수 기준 샘플사 튜닝(대형 제조/금융)

### 3순위: Step6 정규화 보강

- 완료됨: `sga_detail`, `segment_revenue` taxonomy alias 확장
- 완료됨: 숫자 노이즈 행/총계 행 처리 룰 강화
- 완료됨: `llm_client` 선택적 연동 옵션 설계/구현(비용/캐시 정책 포함)

### 4순위: Step9 테스트/검증 체계 구축

- 완료됨: 단위 테스트 1차 세트(`dart_api`, `document_classifier`, `main` 헬퍼/Step8 요약 스키마)
- 완료됨: `html_parser` 규칙/엣지케이스 테스트 5건 추가
  - 파일: `tests/test_html_parser.py`
  - 범위: 병합셀/다중헤더 파싱, 멀티헤더 연도 추정, 주석형 텍스트 테이블 제외, 단일사업부문 안내 감지
- 완료됨: 단위 테스트 기준치 갱신(`31 tests` 통과)
- 완료됨: 온라인 Step8 통합 회귀 게이트 추가
  - 스크립트: `tests/online_step8_integration_gate.py`
  - CI `workflow_dispatch` 입력: `run_online_step8_regression=true`
  - 검증 항목: `track_a(bs/cf)`, `track_c(mode=parsed)`, `track_b_fallback(mode/segment_rows)` 요약 JSON 검사
- 완료됨: 온라인 Step8 통합 회귀 고정 조합 3개 확정
  - 조합: `삼성전자(2024)`, `SK하이닉스(2024)`, `LG전자(2024)`
  - 실측 결과: `PASS 3 / FAIL 0` 확인
  - 비고: `현대자동차(2024)`는 strict Track C에서 `no_xbrl_dir`로 제외
- 통합 테스트
  - 실제 공시 샘플 2~3개로 E2E 회귀검증
- 운영 지표
  - 실행시간, 캐시 히트율, warning 발생 유형 집계

## 5. 바로 실행할 다음 TODO

1. 완료됨: 운영 지표(`metrics.warning_types`)를 CI 아티팩트 대시보드로 집계
2. 완료됨: 연도 매트릭스를 `2022,2023,2024`까지 확장한 장기 회귀 검증
