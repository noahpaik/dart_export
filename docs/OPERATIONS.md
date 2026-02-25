# 운영 가이드

## 1) 운영 전 점검

1. 설정 검증
```bash
python3 main.py --check-config
```

2. 네트워크/키 점검
```bash
python3 main.py --check-network --verbose
```

3. 핵심 플래그 확인
```bash
python3 main.py -h | grep -E "check-network|step8-strict-trackc|step8-skip-trackc|step8-enable-llm-normalize|step8-normalizer-cache-policy"
```

4. 단위 테스트
```bash
python3 -m unittest discover -s tests -v
```

5. Track C strict 샘플 게이트
```bash
python3 main.py --step3-parse-xbrl --xbrl-dir tests/fixtures/trackc_strict_sample
```

## 2) 표준 실행

```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024
```

산출물:
- 엑셀 파일: `data/*_model.xlsx`
- 요약 파일: `data/*_model_summary.json` (기본, `schema_version=1.1`)
- 원문 캐시: `data/raw/<회사명 또는 corp_code>/`
- 정규화 캐시: `data/cache.db`
- 운영 지표: `metrics.runtime_ms`, `metrics.normalizer.cache_hit_rate`, `metrics.warning_types`

선택 LLM 정규화 실행:
```bash
python3 main.py --step8-run-pipeline --company-name 삼성전자 --years 2022,2023,2024 \
  --step8-enable-llm-normalize \
  --step8-llm-max-calls 10 \
  --step8-normalizer-cache-policy read_write
```

Step6 캐시 정책:
- `read_write`(기본): 캐시 읽기/쓰기
- `read_only`: 캐시 읽기만 수행
- `bypass`: 캐시 사용 안 함

## 3) Track C 정책

- 기본 정책:
  - `*_pre.xml`이 없으면 Track C는 `info`로 건너뛰고 Step8은 계속 진행
  - Step8 로그에 `trackc_mode=skipped(no_xbrl_dir)` 출력
  - Step8은 `document.xml`에서 `pre.xml`이 없을 때 `fnlttXbrl.xml` 보강 다운로드를 자동 시도
- 엄격 정책:
  - `--step8-strict-trackc` 사용 시 Track C 미파싱을 즉시 실패로 처리
  - CI나 품질게이트가 필요한 배치에 권장

주의:
- `--step8-skip-trackc`와 `--step8-strict-trackc`는 동시에 사용할 수 없음
- Step8 로그의 `TrackC source=`로 입력 경로를 확인 가능

## 4) 장애 대응 런북

### A. DNS/네트워크 장애

증상:
- `DNS 해석 실패`
- `네트워크 연결 실패`

조치:
1. `python3 main.py --check-network --verbose` 실행
2. DNS/HTTPS/API key 중 어느 구간이 실패하는지 확인
3. 인프라 복구 전에는 Step8 재시도하지 않고 원인 구간 우선 복구

### B. Track C 미존재

증상:
- `trackc_mode=skipped(no_xbrl_dir)`
- 원문 패키지 내 `*_pre.xml` 부재

조치:
1. 기본 모드에서는 정상 케이스로 처리
2. 강제 검증이 필요하면 `--step8-strict-trackc`로 실행

### C. Track B 파싱 품질 저하

증상:
- `TrackB fallback: ... segment_rows=0` 등 기대치 미달

조치:
1. Step8 `TrackB fallback`의 `mode`를 먼저 확인
   - `skipped(single_segment_notice)`: 정상 스킵
   - `skipped(no_segment_data)`: 후보는 있었지만 유효 부문매출 표 미탐지
   - `skipped(no_docs)`: fallback 대상 문서 자체가 없음
2. Step8 `infos`에 단일사업부문 안내가 있는지 확인
   - `Track B segment_revenue: 단일사업부문 공시로 부문별 매출 데이터가 없어 생략.`
3. 단일사업부문 안내가 있으면 정상 케이스로 처리
4. 안내가 없으면 누락 가능성으로 판단하고 원문/룰을 점검
5. `data/raw/...` 원문 파일 구조 확인
6. `src/document_classifier.py`, `src/html_parser.py` 룰 조정
7. 변경 후 동일 공시로 회귀 실행

## 5) 배포/운영 권장 체크리스트

1. PR 단계: `--check-config`, `py_compile`, `unittest`, CLI 플래그 존재 확인 통과
2. 정기 점검: `--check-network --verbose` 성공 확인
3. 릴리즈 점검: 표준 Step8 + 엄격 Step8(고정 샘플 또는 온라인 strict 게이트) 결과 보관
4. 온라인 통합 회귀(선택): 고정 조합으로 Step8 요약 검증
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix base
```
5. 온라인 분기/반기 회귀(선택): `11012/11013/11014`에서 최소 성공 건수 확인
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix multi_report
```
6. 온라인 연도 매트릭스 회귀(선택): `2022,2023,2024` 다개년 입력 검증
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix year_matrix
```
7. 온라인 분기/반기 연도 매트릭스 회귀(선택): `11012/11013/11014` + `2022,2023,2024` 장기 입력 검증
```bash
python3 tests/online_step8_integration_gate.py --matrix-config config/online_step8_matrix.yaml --matrix multi_report_year_matrix
```
8. Step8 경고 메트릭 집계(선택): 온라인 회귀 산출물 기준 집계 리포트 생성
```bash
python3 tests/collect_step8_warning_metrics.py \
  --summary-root /tmp/step8_online_artifacts \
  --output-json /tmp/step8_online_artifacts/metrics/step8_warning_metrics.json \
  --output-md /tmp/step8_online_artifacts/metrics/step8_warning_metrics.md
```
9. Step8 경고 메트릭 추이 리포트(선택): 최근 N회 비교 리포트 생성
```bash
python3 tests/collect_step8_warning_trends.py \
  --current-metrics-json /tmp/step8_online_artifacts/metrics/step8_warning_metrics.json \
  --history-dir /tmp/step8_online_artifacts/history \
  --output-json /tmp/step8_online_artifacts/metrics/step8_warning_trends.json \
  --output-md /tmp/step8_online_artifacts/metrics/step8_warning_trends.md \
  --recent-runs 5
```
10. Step8 추이 품질게이트(선택): 경고/성능 delta 임계치 초과 시 실패 처리
```bash
python3 tests/collect_step8_warning_trends.py \
  --current-metrics-json /tmp/step8_online_artifacts/metrics/step8_warning_metrics.json \
  --history-dir /tmp/step8_online_artifacts/history \
  --output-json /tmp/step8_online_artifacts/metrics/step8_warning_trends.json \
  --output-md /tmp/step8_online_artifacts/metrics/step8_warning_trends.md \
  --recent-runs 5 \
  --quality-gate-config config/step8_warning_quality_gate.yaml \
  --fail-on-quality-gate
```
11. CI 아티팩트 확인: 온라인 Step8 회귀가 실행된 경우 `step8-warning-metrics` 아티팩트에서
   메트릭 리포트(`metrics/step8_warning_metrics.{json,md}`)와 추이 리포트(`metrics/step8_warning_trends.{json,md}`)를 함께 점검
12. 임계치 튜닝: `config/step8_warning_quality_gate.yaml`에서 전역/보고서코드별/경고유형별 임계치를 조정

## 6) 정기 배치(schedule) 실패 대응

정기 배치 기준:
- GitHub Actions `CI` 워크플로의 `schedule` 이벤트(UTC cron) 실행
- 온라인 게이트/아티팩트 경로는 수동(`workflow_dispatch`)과 동일

실패 확인 절차:
1. GitHub Repository > Actions > `CI`에서 이벤트가 `schedule`인 최신 실패 run 확인
2. 실패 job이 `online-checks`인지 먼저 확인하고 실패 step 이름을 기록
3. `step8-warning-metrics` 아티팩트 다운로드 후 아래 파일 우선 확인
   - `metrics/step8_warning_metrics.md`
   - `metrics/step8_warning_trends.md`
4. 실패 유형 분류
   - 네트워크/DART API 실패: `Network Check`, `DART Latest Report Probe` 로그 확인
   - 품질게이트 실패: `Build Step8 Warning Trend Report`의 delta/warning 로그 확인
   - 회귀 실패: 해당 `Step8 ... Regression` step의 회사/보고서코드별 fail 로그 확인
5. 1차 복구
   - 일시적 네트워크 장애: 동일 commit으로 `workflow_dispatch` 재실행
   - 지속적 회귀/품질게이트 실패: 실패 조합을 로컬 재현 후 룰/임계치 조정 이슈 생성
6. 조치 결과를 run 링크와 함께 작업 기록(이슈/PR)에 남김

알림 권장:
- 저장소 `Watch` 설정에서 Actions 실패 알림을 활성화해 정기 배치 실패를 즉시 확인
