# DART 파이프라인 구현 현황 및 다음 작업

업데이트 일시: 2026-02-23

## 1. 구현 원칙

- 기본 경로는 `Track A + Track C` 우선
- `Track B(문서 분류 + HTML/XML 테이블 파싱)`는 fallback
- 모든 단계는 재실행 가능(idempotent)하게 유지

## 2. 현재 진행 현황 요약

| Step | 상태 | 비고 |
|---|---|---|
| Step 0 | 완료 | 스캐폴딩/설정/CLI 기본 진입점 완료 |
| Step 1 | 완료 | DART 연동 + corp_code DB + 최신공시/원문 다운로드 + 네트워크 복원력 보강 |
| Step 2 | 완료 | `fnlttSinglAcntAll` 수집 + 시계열 병합 구현 |
| Step 3 | 완료 | XBRL `pre/lab` 파싱 + 역할/계정/멤버 추출 구현 |
| Step 4 | 완료 | `cal.xml` 검증 + `def.xml` 구조 파싱 구현 |
| Step 5 | 진행중 | 문서분류/HTML(XML 포함) 파싱 구현 완료, 품질 튜닝 남음 |
| Step 6 | 진행중 | 정규화/캐시 연동 완료, 룰 보강/옵션화 남음 |
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
- Step8 요약 JSON 스키마 고정
  - `schema_version` 필드 추가 (`1.0`)
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

### G. Track C 입력 보강 (fnlttXbrl fallback)

- `src/dart_api.py`에 `download_fnltt_xbrl()` 추가
- Step8에서 `document.xml`에 `*_pre.xml`이 없으면 `fnlttXbrl.xml` 자동 시도
- Step8 로그/요약에 `track_c.source` 추가
  - `xbrl_dir(option)`
  - `document.xml`
  - `fnlttXbrl.xml`

## 4. 앞으로 해야 할 것 (우선순위)

### 1순위: Track C 운영 정책 후속 정리 (Step8)

- 완료됨: `*_pre.xml` 없음 기본 `info` 처리
- 완료됨: `--step8-strict-trackc` 옵션 도입
- 완료됨: `trackc_mode` 상태코드 로그 출력
- 완료됨: 기본 정책을 README/운영가이드/CI 규칙에 반영
- 완료됨: CI strict 게이트용 고정 XBRL 샘플(`tests/fixtures/trackc_strict_sample`) 확정
- 완료됨: 실공시 기반 온라인 strict 선택 게이트 추가(`run_online_strict_trackc`)
- 남은 작업: 온라인 게이트에서 안정적으로 통과하는 고정 실공시 샘플 조합(회사/연도) 확정

### 2순위: Step5 파싱 품질 고도화

- 완료됨: `segment_revenue` 추출 보강(삼성전자/현대차 회귀에서 유효 row 확인)
- 남은 작업: 타 회사/업종 샘플에서 과추출/누락 여부 추가 검증
- 병합셀/다중헤더/주석형 텍스트 테이블 제외 규칙 보강
- 문서분류 점수 기준을 샘플사별(대형 제조/금융 등) 튜닝

### 3순위: Step6 정규화 보강

- `sga_detail`, `segment_revenue` taxonomy alias 확장
- 숫자 노이즈 행/총계 행 처리 룰 강화
- `llm_client` 선택적 연동 옵션 설계(비용/캐시 정책 포함)

### 4순위: Step9 테스트/검증 체계 구축

- 완료됨: 단위 테스트 1차 세트(`dart_api`, `document_classifier`, `main` 헬퍼/Step8 요약 스키마)
- 남은 작업: `html_parser` 규칙/엣지케이스 테스트 추가
- 통합 테스트
  - 실제 공시 샘플 2~3개로 E2E 회귀검증
- 운영 지표
  - 실행시간, 캐시 히트율, warning 발생 유형 집계

## 5. 바로 실행할 다음 TODO

1. 온라인 strict 게이트에서 통과하는 실공시 조합(회사/연도) 1~2개 확정
2. `segment_revenue` 룰을 타사 샘플 2~3개로 회귀검증
3. `html_parser` 단위 테스트(병합셀/다중헤더/주석형 텍스트 제외) 3~5개 추가
4. 실제 공시 샘플 기반 Step8 통합 회귀 테스트(온라인 선택 실행) 고도화
