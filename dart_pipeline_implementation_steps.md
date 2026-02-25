# DART 파이프라인 구현 현황 및 다음 작업

업데이트 일시: 2026-02-25 (Step 5/6 DoD 검증 반영)

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
| Step 5 | 완료 | DoD 검증(2026-02-25) 통과: 단위/수동/온라인 회귀 및 오탐지 방지 확인 |
| Step 6 | 완료 | DoD 검증(2026-02-25) 통과: 정규화/캐시/LLM/메트릭 스키마/온라인 회귀 확인 |
| Step 7 | 완료 | `excel_writer.py` 구현 + Step8 파이프라인 통합 완료 |
| Step 8 | 진행중 | E2E 구동 + 운영지표(`metrics`) + CI 아티팩트 집계 자동화 완료, 운영 튜닝 남음 |
| Step 9 | 진행중 | 핵심 단위 테스트/온라인 회귀/CI 연동 완료, 장기 안정화 회귀 고도화 남음 |

## 2-1. Step 5/6 완료 판정 기준 (DoD)

### Step 5 완료기준 (파싱 품질)

1. HTML/문서 파싱 단위테스트 통과
   - `python3 -m unittest -v tests/test_html_parser.py tests/test_document_classifier.py`
2. 수동 회귀 통과
   - `python3 tests/manual_segment_revenue_regression.py`
3. 온라인 고정 회귀(3사, 3개년, 4개 보고서코드) 통과
   - `python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix all_reports_year_matrix`
4. 주석형 텍스트 테이블 오탐지 없음
   - 단위테스트(`test_segment_revenue_filter_rejects_text_comment_table`) + 회귀 결과 기준
5. 위 1~4 증빙 로그 확보 후 Step 5 상태를 `완료`로 상향

### Step 6 완료기준 (정규화/캐시/LLM 운영)

1. 정규화/LLM 단위테스트 통과
   - `python3 -m unittest -v tests/test_account_normalizer.py tests/test_llm_client.py tests/test_main_helpers.py`
2. 캐시 정책 동작 보장
   - `read_write`: 재실행 시 cache hit 발생
   - `read_only`: cache write 없음
   - `bypass`: cache read/write 없음
3. LLM 예산/가드레일 동작 보장
   - `max_calls` 초과 시 차단
   - 게이트웨이 실패 시 파이프라인 크래시 없이 경고/정보 처리
4. Step8 summary 스키마 보장
   - `metrics.normalizer`, `metrics.warning_types` 필드 항상 포함
5. 온라인 고정 회귀(3사, 3개년, 4개 보고서코드)에서 정규화 관련 치명적 회귀 없음
6. 위 1~5 증빙 로그 확보 후 Step 6 상태를 `완료`로 상향

## 2-2. Step 5/6 DoD 검증 실행 로그 (2026-02-25)

### Step 5 검증 로그

- 실행: `python3 -m unittest -v tests/test_html_parser.py tests/test_document_classifier.py`
  - 결과: `Ran 10 tests ... OK`
  - 포함 확인: `test_segment_revenue_filter_rejects_text_comment_table ... ok`
- 실행: `python3 tests/manual_segment_revenue_regression.py`
  - 결과:
    - `company=삼성전자 accepted=1 single_segment_notices=0`
    - `company=SK하이닉스 accepted=0 single_segment_notices=1`
    - `company=현대자동차 accepted=1 single_segment_notices=0`
    - 최종: `PASS`
- 실행: `python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix all_reports_year_matrix`
  - 결과: `pass=12 fail=0`
  - 보고서코드별:
    - `11011: pass=3 fail=0`
    - `11012: pass=3 fail=0`
    - `11013: pass=3 fail=0`
    - `11014: pass=3 fail=0`
- 판정: Step 5 `완료`

### Step 6 검증 로그

- 실행: `python3 -m unittest -v tests/test_account_normalizer.py tests/test_llm_client.py tests/test_main_helpers.py`
  - 결과: `Ran 24 tests ... OK`
  - 캐시 정책 관련 확인:
    - `test_cache_policy_read_only_does_not_write_cache ... ok`
    - `test_cache_policy_bypass_ignores_existing_cache ... ok`
    - `test_cache_policy_read_only_reads_existing_cache ... ok`
    - `test_usage_tracks_cache_and_llm_metrics ... ok` (read_write hit/miss/write 계측)
  - LLM 예산/가드레일 확인:
    - `test_budget_limit_blocks_after_max_calls ... ok`
    - `test_gateway_failure_returns_empty_json_and_counts_failure ... ok`
  - Step8 summary 스키마 확인:
    - `test_build_step8_summary_payload_schema ... ok`
- 실행: Step 5와 동일한 온라인 고정 회귀 커맨드
  - 결과: `pass=12 fail=0` (정규화 관련 치명적 회귀 없음)
  - 스키마 필드 확인: 온라인 게이트에서 `metrics.warning_types`, `metrics.normalizer` 필드 검증 통과
- 판정: Step 6 `완료`

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
  - 현재 기준 `50 tests` 통과

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
  - 전체 단위 테스트 `50 tests` 통과

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
  - `python3 -m unittest discover -s tests -v` (`50 tests` 통과)

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
  - 전체 단위 테스트 `50 tests` 통과

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
  - PASS 기업: `삼성전자`, `SK하이닉스`, `LG전자`

### P. Step8 경고 메트릭 CI 아티팩트 집계 자동화

- `tests/collect_step8_warning_metrics.py` 추가
  - `*_summary.json` 재귀 탐색
  - `metrics.warning_types`/runtime/track mode/report_code/year_set 집계
  - JSON + Markdown 리포트 동시 출력
- 단위 테스트 추가: `tests/test_collect_step8_warning_metrics.py`
  - 집계 합산 정확성/Markdown 섹션 렌더링 검증
- CI 반영:
  - `STEP8_ARTIFACT_ROOT=/tmp/step8_online_artifacts`
  - 온라인 Step8 회귀별 `--output-dir` 분리(`base`, `multi_report`, `multi_report_year_matrix`, `year_matrix`)
  - 집계 단계 실행 후 아티팩트 `step8-warning-metrics` 업로드
- 검증:
  - `python3 -m unittest discover -s tests -v` 통과
  - `tests/collect_step8_warning_metrics.py --require-runs` 실측 집계 확인
    - `run_count=3`, `year_sets["2022,2023,2024"]=3`, `parse_failures=0`

### Q. 원격 반영 상태

- 최신 커밋: `39fc808` (`feat: expand step8 regression and metrics automation`)
- 반영 브랜치: `main`
- 원격 반영: `origin/main` push 완료
- 참고: SSH 인증키는 `~/.ssh/config`에서 `id_ed25519` 고정으로 정리 완료

### R. Step8 분기/반기 연도 매트릭스 온라인 회귀 확장

- CI `workflow_dispatch` 입력 추가
  - `run_online_step8_multi_report_year_matrix_regression=true`
- 검증 조합:
  - 보고서코드: `11012`, `11013`, `11014`
  - 입력 연도: `2022,2023,2024`
  - 고정 기업: `삼성전자`, `SK하이닉스`, `LG전자`
- 실행 스크립트:
  - `python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix multi_report_year_matrix`
- 실측 결과:
  - `11012`: PASS 3 / FAIL 0
  - `11013`: PASS 3 / FAIL 0
  - `11014`: PASS 3 / FAIL 0
  - 전체: PASS 9 / FAIL 0

### S. Step8 경고 메트릭 추이 리포트 자동화

- `tests/collect_step8_warning_trends.py` 추가
  - 현재 실행의 `step8_warning_metrics.json`을 기준점으로 사용
  - GitHub Actions 아티팩트(`step8-warning-metrics`)에서 최근 N회 히스토리를 조회/다운로드
  - 추이 산출물 생성:
    - `metrics/step8_warning_trends.json`
    - `metrics/step8_warning_trends.md`
- 단위 테스트 추가: `tests/test_collect_step8_warning_trends.py`
  - latest/previous delta 계산 검증
  - history parse failure 처리 검증
  - markdown 렌더링 검증
- CI 반영:
  - `Build Step8 Warning Trend Report` 단계 추가
  - 조건: 온라인 Step8 회귀 입력 중 하나라도 실행된 경우
  - 입력: `--recent-runs 5`, `--fetch-from-github`, `--github-repo`, `--github-token`
  - 품질게이트: `warning_count delta <= 3`, `runtime_avg_ms delta <= 5000` 초과 시 실패
- 실측(로컬 샘플 기준):
  - `run_count=9`, `report_codes(11012/11013/11014)=각 3`, `year_sets(2022,2023,2024)=9`
  - 추이 리포트 파일 생성 확인
  - 품질게이트 상태: `pass` 확인

### T. 온라인 회귀 매트릭스 설정 파일화

- 설정 파일 추가: `config/online_step8_matrix.yaml`
  - 회사/연도/보고서코드/재시도/최소성공건수 프로파일(`base`, `multi_report`, `year_matrix`, `multi_report_year_matrix`) 정의
  - 회사별 검증 규칙(`allowed_segment_modes`, `min_segment_rows_when_parsed`, `require_single_segment_notice`) 정의
- `tests/online_step8_integration_gate.py` 확장
  - `--matrix-config`, `--matrix`, `--list-matrices` 지원
  - 기존 개별 옵션(`--companies`, `--years`, `--report-codes` 등)은 매트릭스 override로 유지
- CI 연동
  - `.github/workflows/ci.yml`의 Step8 온라인 회귀 단계가 공통 설정 파일을 읽도록 변경
  - 하드코딩 인자 제거, `--matrix-config config/online_step8_matrix.yaml --matrix <profile>` 형태로 통일
- 운영/규칙 문서 반영
  - `docs/CI_RULES.md`, `docs/OPERATIONS.md`, `README.md`

### U. 추이 품질게이트 임계치 운영 튜닝

- 설정 파일 추가: `config/step8_warning_quality_gate.yaml`
  - 전역 임계치: `max_warning_delta`, `max_runtime_avg_ms_delta`
  - 보고서코드별 임계치: `report_code_thresholds`
    - 연간(`11011`)은 엄격, 분기/반기(`11012/11013/11014`)는 완화
  - 경고유형 임계치/무시 목록: `max_warning_type_delta`, `ignore_warning_types`
- `tests/collect_step8_warning_trends.py` 확장
  - `--quality-gate-config` 지원 (기본: `config/step8_warning_quality_gate.yaml`)
  - 보고서코드별 경고건수/평균 실행시간 델타 계산 및 게이트 평가
  - 경고유형별 delta 임계치/ignore 규칙 평가
  - 추이 리포트에 게이트 설정 로드 여부, 보고서코드별/경고유형별 delta 섹션 추가
- 테스트 보강
  - `tests/test_collect_step8_warning_trends.py`에 보고서코드별 임계치/경고유형 ignore 검증 케이스 추가
- CI/문서 반영
  - `.github/workflows/ci.yml`에서 추이 게이트 실행 시 설정 파일 참조
  - `docs/CI_RULES.md`, `docs/OPERATIONS.md`, `README.md` 실행 예시 업데이트
- 실측 기반 1차 재보정(2026-02-25)
  - 비교군:
    - baseline: `year_matrix_3y` + `multi_report_year_matrix_local` (12 runs)
    - current: `recalib_year_matrix` + `recalib_multi_report_year_matrix` (12 runs)
  - 관측 delta:
    - `warning_count`: `0`
    - `runtime_avg_ms`: `-2517.59`
    - 보고서코드별 runtime delta:
      - `11011`: `-642.66`
      - `11012`: `-4352.33`
      - `11013`: `-3151.66`
      - `11014`: `-1923.67`
  - 조정:
    - `11012/11013/11014 max_warning_delta`: `4 -> 3`
    - `11012/11013/11014 max_runtime_avg_ms_delta`: `6500 -> 5500`
  - 검증:
    - `tests/collect_step8_warning_trends.py --quality-gate-config config/step8_warning_quality_gate.yaml --fail-on-quality-gate` 통과

### V. Step8 운영 튜닝 1차 (품질게이트 표본 수 가드레일)

- `tests/collect_step8_warning_trends.py` 확장
  - 전역 최소 표본 수: `min_run_count`
  - 보고서코드별 최소 표본 수: `min_run_count_by_report_code`
  - 표본 수 미달 시 품질게이트를 `fail` 대신 `skipped` 처리해 초기/희소 표본 오탐 방지
- 설정 반영: `config/step8_warning_quality_gate.yaml`
  - `min_run_count: 3`
  - `min_run_count_by_report_code: 2`
- CLI override 추가
  - `--quality-gate-min-run-count`
  - `--quality-gate-min-run-count-by-report-code`
- 테스트 보강
  - `tests/test_collect_step8_warning_trends.py`에 최소 표본 수 스킵/코드별 임계치 적용 조건 케이스 추가

### W. Step8 운영 튜닝 2차 (5회+ 운영데이터 기준 재보정)

- 재보정 데이터셋(로컬 운영 아티팩트) 구성
  - `year_matrix_3y`(3 runs), `multi_report_year_matrix_local`(9 runs),
    `recalib_year_matrix`(3 runs), `recalib_multi_report_year_matrix`(9 runs),
    `recalib_baseline_all`(12 runs), `recalib_current_all`(12 runs)
  - 총 6개 포인트(5+ 충족) 기준으로 동일 프로파일 쌍 delta 점검
- 관측 delta(대표)
  - `warning_count`: 전 프로파일 `0`
  - `runtime_avg_ms`: 개선 방향(음수) 유지
    - year_matrix: `-642.66`
    - multi_report_year_matrix: `-3142.55`
    - all: `-2517.59`
- 설정 재보정: `config/step8_warning_quality_gate.yaml`
  - `min_run_count: 3 -> 5`
  - `min_run_count_by_report_code: 2 -> 3`
  - 목적: 표본 부족/부분 표본 비교에서 발생하는 품질게이트 오탐 추가 축소

### X. Step8 운영 튜닝 3차 (온라인 회귀 1회 반영)

- 실행(온라인 회귀 1회, all profile):
  - `python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix all_reports_year_matrix --output-dir /tmp/step8_online_artifacts/recalib_run3_all`
  - 결과: `pass=12 fail=0`
- 신규 포인트 집계:
  - `python3 tests/collect_step8_warning_metrics.py --summary-root /tmp/step8_online_artifacts/recalib_run3_all --output-json /tmp/step8_online_artifacts/recalib_out/point_recalib_run3_all_metrics.json --output-md /tmp/step8_online_artifacts/recalib_out/point_recalib_run3_all_metrics.md --require-runs`
- run2→run3 비교(동일 all profile, 12 runs):
  - `warning_count delta`: `0`
  - `runtime_avg_ms delta`: `+809.67`
  - 보고서코드별 runtime delta:
    - `11011`: `+428.0`
    - `11012`: `+77.33`
    - `11013`: `+1607.0`
    - `11014`: `+1126.33`
- 설정 미세조정(런타임 임계치만 강화):
  - 전역 `max_runtime_avg_ms_delta`: `5000 -> 4500`
  - `11011 max_runtime_avg_ms_delta`: `4500 -> 4000`
  - `11012/11013/11014 max_runtime_avg_ms_delta`: `5500 -> 5000`
- 검증:
  - `tests/collect_step8_warning_trends.py --current-metrics-json /tmp/step8_online_artifacts/recalib_out/point_recalib_run3_all_metrics.json --history-dir /tmp/step8_online_artifacts/recalib_history_run2_only --output-json /tmp/step8_online_artifacts/recalib_out/run3_vs_run2_trends_after_tune.json --output-md /tmp/step8_online_artifacts/recalib_out/run3_vs_run2_trends_after_tune.md --recent-runs 5 --quality-gate-config config/step8_warning_quality_gate.yaml --fail-on-quality-gate` 통과

## 4. 앞으로 해야 할 것 (우선순위, 2026-02-25 기준)

### 완료: Step 5/6 완료 판정 실행 및 상태 갱신

- `2-1. Step 5/6 완료 판정 기준(DoD)` 검증 커맨드 실제 실행 완료
- Step 5/6 검증 로그 문서 첨부 완료 (`2-2` 섹션)
- 상태 갱신 완료: `Step 5=완료`, `Step 6=완료`

### 완료: 온라인 회귀 정기 배치(scheduled) 추가

- `.github/workflows/ci.yml`에 `schedule`(cron) 트리거 추가
- 수동(`workflow_dispatch`)과 정기 배치가 동일한 게이트/아티팩트 경로(`/tmp/step8_online_artifacts/*`)를 사용하도록 정리
- 정기 실행 실패 시 확인 가능한 알림/로그 확인 절차 문서화
  - `docs/OPERATIONS.md` 6) 정기 배치 실패 대응
  - `docs/CI_RULES.md` 온라인 CI 조건/기본 실행 범위 반영

### 완료: 온라인 회귀 매트릭스 설정 파일화

- 회사/연도/보고서코드/검증 규칙을 `config/online_step8_matrix.yaml`로 분리
- `tests/online_step8_integration_gate.py`와 CI가 동일 설정을 읽도록 통합
- 신규 기업/산업군 추가 시 설정 파일 확장으로 대응 가능하도록 구조화

### 완료: 추이 품질게이트 임계치 운영 튜닝

- 최근 N회 데이터 추이 비교용 게이트를 설정 파일 기반으로 전환
- 보고서코드(`11011/11012/11013/11014`)별 임계치 세분화 반영
- 경고유형별 임계치/ignore 정책 반영으로 오탐 민감도 조정

## 5. 다음 권장 TODO

1. 최근 운영 데이터 누적 후 `config/step8_warning_quality_gate.yaml` 임계치 재보정
