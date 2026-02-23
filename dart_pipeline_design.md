# DART 재무제표 & 주석 데이터 자동 추출 — 재무모델링 파이프라인

## 1. 개요

DART OpenAPI에서 **3대 재무제표(B/S, I/S, C/F)** 를 구조화된 API로 추출하고, 공시 원문에서 **주석 데이터(부문별 매출, 판관비 상세, 감가상각 내역 등)** 를 파싱하여, 재무모델링용 엑셀 템플릿에 자동 세팅하는 파이프라인.

데이터 소스를 두 트랙으로 분리하는 것이 핵심이다.

| 트랙 | 데이터 | 소스 | 파싱 난이도 |
|------|--------|------|------------|
| **Track A** | B/S, I/S, C/F (표준 재무제표) | DART 구조화 API (`fnlttSinglAcntAll`) | 낮음 — JSON/구조화 |
| **Track B** | 주석 상세 (부문별, 판관비 등) | 공시 원문 HTML (`document.xml`) | 높음 — HTML 파싱 + LLM 정규화 |

Track A는 DART가 표준화된 계정 체계로 제공하므로 바로 사용 가능하고, Track B는 회사마다 계정명이 달라 LLM 기반 정규화가 필요하다.

---

## 2. 전체 아키텍처

```
[DART OpenAPI]
     │
     ├──────────────────────────────────────┐
     ▼                                      ▼
┌──────────────┐                    ┌──────────────┐
│  Step 1       │                    │  Step 1       │
│  공시 탐색     │  rcept_no 확보     │  (공유)        │
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       ▼                                   ▼
 ═══ Track A ═══                     ═══ Track B ═══
 (3대 재무제표)                       (주석 데이터)
       │                                   │
       ▼                                   ▼
┌──────────────┐                    ┌──────────────┐
│  Step 2A      │                    │  Step 2B      │
│  구조화 API   │                    │  원문 다운로드  │
│  fnlttSingl   │                    │  document.xml  │
│  AcntAll      │                    │  → HTML 추출   │
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       ▼                                   ▼
┌──────────────┐                    ┌──────────────┐
│  Step 3A      │                    │  Step 3B      │
│  DataFrame    │                    │  테이블 파싱   │
│  변환 + 정리  │                    │  BS + read_html│
└──────┬───────┘                    └──────┬───────┘
       │                                   │
       │                                   ▼
       │                            ┌──────────────┐
       │                            │  Step 4B      │
       │                            │  LLM 계정명   │
       │                            │  정규화 + 캐싱 │
       │                            └──────┬───────┘
       │                                   │
       ▼                                   ▼
┌─────────────────────────────────────────────┐
│  Step 5: 엑셀 세팅                            │
│  openpyxl → 재무모델 템플릿에 값 삽입            │
│  B/S, I/S, C/F 시트 + 주석 상세 시트            │
└─────────────────────────────────────────────┘
```

---

## 3. 디렉토리 구조

```
dart_pipeline/
├── config/
│   ├── settings.yaml              # API 키, 모델 설정, 경로
│   └── taxonomy.yaml              # 표준 계정명 체계 정의
├── src/
│   ├── dart_api.py                # DART OpenAPI 호출 모듈
│   ├── corp_code_db.py            # [신규] corp_code SQLite DB 관리
│   ├── financial_statements.py    # [Track A] B/S, I/S, C/F 구조화 추출
│   ├── document_classifier.py     # [Track B] 공시 zip 내 주석 파일 식별
│   ├── html_parser.py             # [Track B] HTML 주석 테이블 파싱
│   ├── account_normalizer.py      # [Track B] LLM 기반 계정명 정규화
│   ├── excel_writer.py            # 엑셀 템플릿 세팅 (전체)
│   └── cache_db.py                # SQLite 캐싱 관리
├── templates/
│   └── financial_model.xlsx       # 재무모델 엑셀 템플릿
├── data/
│   ├── raw/                       # 다운로드된 원본 zip/html
│   ├── parsed/                    # 파싱된 DataFrame (parquet)
│   ├── corp_code.db               # [신규] 전체 기업 고유번호 DB
│   └── cache.db                   # 계정명 매핑 캐시
├── main.py                        # 파이프라인 실행 진입점
└── requirements.txt
```

---

## 4. 단계별 구현

### Step 1: 공시 탐색 — `dart_api.py`

DART OpenAPI로 특정 회사의 사업보고서/반기보고서 목록을 조회하고, 가장 최근 공시의 `rcept_no`를 확보한다.

**사용 API**

| API | 엔드포인트 | 용도 |
|-----|-----------|------|
| 공시목록 | `/api/list.json` | rcept_no 조회 |
| 고유번호 | `/api/corpCode.xml` | 회사명 → corp_code 변환 |

**구현 포인트**

```python
import requests

class DartAPI:
    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_reports(self, corp_code: str, year: str, report_type: str = "11011") -> list[dict]:
        """
        사업보고서 목록 조회
        report_type:
          11011 = 사업보고서, 11012 = 반기보고서
          11013 = 1분기보고서, 11014 = 3분기보고서
        """
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": f"{year}0101",
            "end_de": f"{year}1231",
            "pblntf_ty": "A",       # 정기공시
            "page_count": 100,
        }
        resp = requests.get(f"{self.BASE_URL}/list.json", params=params)
        data = resp.json()

        # report_type에 해당하는 공시만 필터링
        reports = []
        for item in data.get("list", []):
            if report_type in item.get("report_nm", ""):
                reports.append(item)
        return reports

    def get_corp_code(self, company_name: str) -> str | None:
        """회사명으로 corp_code 조회"""
        db = CorpCodeDB()
        if db.is_empty():
            print("📥 corp_code DB 초기화 중 (최초 1회)...")
            db.build_from_dart(self.api_key)
        return db.search(company_name)
```

**corp_code 관리 — `corp_code_db.py`**

`corpCode.xml` API는 전체 상장/비상장 기업 약 100,000건을 zip으로 반환한다.
매번 API를 호출하면 느리므로, 최초 1회 SQLite에 적재 후 로컬 검색한다.

```python
import requests
import zipfile
import io
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

class CorpCodeDB:
    """
    DART 고유번호(corp_code) 로컬 DB.
    
    corpCode.xml: ~100,000개 기업의 corp_code, 회사명, 종목코드 포함.
    zip (약 2MB) → XML 파싱 → SQLite 적재. 최초 1회만 수행.
    """

    def __init__(self, db_path: str = "data/corp_code.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._ensure_table()

    def _ensure_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS corp (
                corp_code TEXT PRIMARY KEY,    -- DART 고유번호 (8자리)
                corp_name TEXT NOT NULL,       -- 회사명
                stock_code TEXT,               -- 종목코드 (6자리, 비상장은 빈값)
                modify_date TEXT,              -- 최종변경일
                is_listed INTEGER DEFAULT 0    -- 상장 여부 (편의용)
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_corp_name ON corp(corp_name)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stock_code ON corp(stock_code)"
        )
        self.conn.commit()

    def is_empty(self) -> bool:
        row = self.conn.execute("SELECT COUNT(*) FROM corp").fetchone()
        return row[0] == 0

    def build_from_dart(self, api_key: str):
        """
        DART API에서 corpCode.xml zip을 다운로드하여 DB에 적재.
        
        API: GET https://opendart.fss.or.kr/api/corpCode.xml
        반환: zip 파일 (내부에 CORPCODE.xml)

        XML 구조:
          <result>
            <list>
              <corp_code>00126380</corp_code>
              <corp_name>삼성전자</corp_name>
              <stock_code>005930</stock_code>
              <modify_date>20240301</modify_date>
            </list>
            ...약 100,000건
          </result>
        """
        print("  ⬇️  corpCode.xml 다운로드 중...")
        resp = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": api_key},
        )

        if resp.status_code != 200:
            raise Exception(f"corpCode.xml 다운로드 실패: HTTP {resp.status_code}")

        # zip 해제 → XML 파싱
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_filename = zf.namelist()[0]  # 보통 "CORPCODE.xml"
            xml_bytes = zf.read(xml_filename)

        print("  🔍 XML 파싱 중...")
        root = ET.fromstring(xml_bytes)

        # DB 적재 (batch insert)
        records = []
        for item in root.iter("list"):
            corp_code = item.findtext("corp_code", "").strip()
            corp_name = item.findtext("corp_name", "").strip()
            stock_code = item.findtext("stock_code", "").strip()
            modify_date = item.findtext("modify_date", "").strip()

            if not corp_code or not corp_name:
                continue

            # stock_code가 6자리 숫자면 상장, 아니면 비상장(" " 또는 빈값)
            is_listed = 1 if stock_code and stock_code.strip() and len(stock_code.strip()) == 6 else 0
            records.append((corp_code, corp_name, stock_code.strip(), modify_date, is_listed))

        self.conn.executemany(
            "INSERT OR REPLACE INTO corp VALUES (?, ?, ?, ?, ?)",
            records,
        )
        self.conn.commit()
        print(f"  ✅ {len(records)}개 기업 적재 완료 (상장: {sum(1 for r in records if r[4])}개)")

    def search(self, name: str) -> str | None:
        """
        회사명으로 corp_code 검색.
        정확 일치 → 부분 일치(상장 우선) 순으로 시도.
        """
        # 1차: 정확히 일치
        row = self.conn.execute(
            "SELECT corp_code FROM corp WHERE corp_name = ?", (name,)
        ).fetchone()
        if row:
            return row[0]

        # 2차: 부분 일치 (상장 기업 우선, 이름 짧은 순 = 더 정확한 매치)
        rows = self.conn.execute(
            "SELECT corp_code, corp_name, is_listed FROM corp "
            "WHERE corp_name LIKE ? ORDER BY is_listed DESC, LENGTH(corp_name) ASC LIMIT 10",
            (f"%{name}%",),
        ).fetchall()

        if not rows:
            return None
        return rows[0][0]

    def search_by_stock_code(self, stock_code: str) -> str | None:
        """종목코드(6자리)로 corp_code 검색. 예: '005930' → '00126380'"""
        row = self.conn.execute(
            "SELECT corp_code FROM corp WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        return row[0] if row else None

    def get_listed_corps(self) -> list[dict]:
        """상장 기업만 조회 (~2,600개)"""
        rows = self.conn.execute(
            "SELECT corp_code, corp_name, stock_code FROM corp WHERE is_listed = 1"
        ).fetchall()
        return [
            {"corp_code": r[0], "corp_name": r[1], "stock_code": r[2]}
            for r in rows
        ]

    def refresh(self, api_key: str):
        """DB 전체 갱신 (월 1회 권장)"""
        self.conn.execute("DELETE FROM corp")
        self.build_from_dart(api_key)
```

**사용 예시**

```python
db = CorpCodeDB()
db.build_from_dart("YOUR_API_KEY")  # 최초 1회, 약 5초

db.search("삼성전자")                  # → "00126380"
db.search("SK하이닉스")                # → "00164779"
db.search("삼성")                      # → "00126380" (상장+최단 이름 우선)
db.search_by_stock_code("005930")      # → "00126380"

# 전체 상장기업 목록
listed = db.get_listed_corps()         # ~2,600개
```

> **운영 팁**
> - corpCode.xml은 DART에서 일 1회 갱신 한도가 있으므로, 월 1회 `refresh()` 호출이면 충분
> - DB 파일 크기: 약 5~8MB (100,000건)
> - `search()`의 부분 일치는 "삼성" 검색 시 "삼성전자", "삼성SDI", "삼성물산" 등이 후보로 나오는데, 상장+짧은이름 우선 정렬로 대부분 올바른 결과를 반환

---

### Step 2A: 3대 재무제표 추출 (Track A) — `financial_statements.py`

DART OpenAPI의 구조화된 재무제표 API를 사용한다. 이 API는 **표준 계정 체계(K-IFRS taxonomy)** 로 정규화된 데이터를 JSON으로 제공하므로, HTML 파싱이나 LLM 정규화가 필요 없다.

**사용 API**

| API | 엔드포인트 | 용도 |
|-----|-----------|------|
| 단일회사 전체 재무제표 | `/api/fnlttSinglAcntAll.json` | B/S, I/S, C/F 전 항목 |
| 단일회사 주요 계정 | `/api/fnlttSinglAcnt.json` | 주요 항목만 (간략) |
| 다중회사 주요 계정 | `/api/fnlttMultiAcnt.json` | 비교 분석용 |

**핵심: `fnlttSinglAcntAll` API 스펙**

```
GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json

파라미터:
  crtfc_key   : API 인증키
  corp_code   : 고유번호 (8자리)
  bsns_year   : 사업연도 (예: "2024")
  reprt_code  : 보고서 코드
                 11011 = 사업보고서 (연간)
                 11012 = 반기보고서
                 11013 = 1분기보고서
                 11014 = 3분기보고서
  fs_div      : CFS=연결, OFS=별도
```

**응답 구조 (핵심 필드)**

```json
{
  "status": "000",
  "list": [
    {
      "rcept_no": "20240315000123",
      "sj_div": "BS",              // BS=재무상태표, IS=손익계산서,
                                    // CIS=포괄손익, CF=현금흐름, SCE=자본변동
      "sj_nm": "재무상태표",
      "account_id": "ifrs-full_CurrentAssets",
      "account_nm": "유동자산",
      "account_detail": "-",
      "thstrm_nm": "제 56 기",      // 당기
      "thstrm_amount": "218983382000000",
      "frmtrm_nm": "제 55 기",      // 전기
      "frmtrm_amount": "196972079000000",
      "bfefrmtrm_nm": "제 54 기",   // 전전기
      "bfefrmtrm_amount": "187817786000000",
      "ord": "1"                    // 표시 순서
    },
    ...
  ]
}
```

**구현 포인트**

```python
import requests
import pandas as pd
from dataclasses import dataclass
from enum import Enum

class StatementType(Enum):
    BS = "BS"    # 재무상태표 (Balance Sheet)
    IS = "IS"    # 손익계산서 (Income Statement)
    CIS = "CIS"  # 포괄손익계산서 (Comprehensive Income Statement)
    CF = "CF"    # 현금흐름표 (Cash Flow Statement)
    SCE = "SCE"  # 자본변동표 (Statement of Changes in Equity)

@dataclass
class FinancialStatement:
    statement_type: StatementType
    corp_code: str
    year: str
    fs_div: str              # CFS(연결) or OFS(별도)
    df: pd.DataFrame         # 정리된 DataFrame
    raw_data: list[dict]     # 원본 API 응답

class FinancialStatementFetcher:
    BASE_URL = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_all_statements(
        self,
        corp_code: str,
        year: str,
        report_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict[StatementType, FinancialStatement]:
        """
        3대 재무제표 + 포괄손익 + 자본변동표를 한 번에 가져온다.
        API 호출은 1회로 충분 — 응답에 모든 재무제표가 포함됨.
        """
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": report_code,
            "fs_div": fs_div,
        }

        resp = requests.get(f"{self.BASE_URL}/fnlttSinglAcntAll.json", params=params)
        data = resp.json()

        if data.get("status") != "000":
            raise Exception(f"DART API 에러: {data.get('message')}")

        raw_list = data["list"]

        # sj_div(재무제표 구분)별로 분리
        statements = {}
        for st_type in StatementType:
            filtered = [row for row in raw_list if row["sj_div"] == st_type.value]
            if filtered:
                df = self._to_dataframe(filtered, st_type)
                statements[st_type] = FinancialStatement(
                    statement_type=st_type,
                    corp_code=corp_code,
                    year=year,
                    fs_div=fs_div,
                    df=df,
                    raw_data=filtered,
                )

        return statements

    def _to_dataframe(self, rows: list[dict], st_type: StatementType) -> pd.DataFrame:
        """API 응답을 깔끔한 DataFrame으로 변환"""
        records = []
        for row in rows:
            record = {
                "계정ID": row["account_id"],
                "계정명": row["account_nm"],
                "계정상세": row.get("account_detail", ""),
                "당기": self._parse_amount(row.get("thstrm_amount")),
                "전기": self._parse_amount(row.get("frmtrm_amount")),
                "전전기": self._parse_amount(row.get("bfefrmtrm_amount")),
                "표시순서": int(row.get("ord", 0)),
            }
            records.append(record)

        df = pd.DataFrame(records)
        df = df.sort_values("표시순서").reset_index(drop=True)

        # I/S, C/F는 누적 금액과 분기 금액이 분리될 수 있음
        # thstrm_add_amount(당기 누적) 필드가 있으면 추가
        if any("thstrm_add_amount" in row for row in rows):
            df["당기누적"] = [
                self._parse_amount(row.get("thstrm_add_amount"))
                for row in rows
            ]

        return df

    def _parse_amount(self, value) -> float | None:
        """금액 문자열 → 숫자 변환"""
        if value is None or value == "":
            return None
        try:
            cleaned = str(value).replace(",", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    def fetch_multi_year(
        self,
        corp_code: str,
        years: list[str],
        report_code: str = "11011",
        fs_div: str = "CFS",
    ) -> dict[str, dict[StatementType, FinancialStatement]]:
        """
        여러 연도를 한 번에 수집.
        시계열 재무모델에 필요한 3~5년치 데이터.
        """
        results = {}
        for year in years:
            try:
                statements = self.fetch_all_statements(corp_code, year, report_code, fs_div)
                results[year] = statements
                print(f"  ✅ {year}년 재무제표 수집 완료")
            except Exception as e:
                print(f"  ❌ {year}년 실패: {e}")
        return results
```

**3대 재무제표 → 시계열 DataFrame 조합**

API는 연도별로 당기/전기/전전기를 제공하므로, 여러 해를 조합하면 중복 데이터가 생긴다. 이를 정리하는 유틸리티:

```python
def build_time_series(
    multi_year_data: dict[str, dict[StatementType, FinancialStatement]],
    target_type: StatementType,
) -> pd.DataFrame:
    """
    여러 연도의 데이터를 하나의 시계열 DataFrame으로 합친다.

    예: 2022~2024 사업보고서를 수집하면
    → 2020~2024년 5개년 시계열이 완성됨 (전전기 활용)
    """
    all_frames = {}

    for year, statements in sorted(multi_year_data.items()):
        if target_type not in statements:
            continue
        df = statements[target_type].df

        # 당기 데이터를 해당 연도로 매핑
        year_df = df[["계정ID", "계정명", "당기"]].copy()
        year_df = year_df.rename(columns={"당기": year})
        all_frames[year] = year_df

    if not all_frames:
        return pd.DataFrame()

    # 계정ID 기준으로 merge
    base = list(all_frames.values())[0][["계정ID", "계정명"]]
    for year, year_df in all_frames.items():
        base = base.merge(
            year_df[["계정ID", year]],
            on="계정ID",
            how="outer",
        )

    return base
```

**DART API 계정 체계 — 재무모델링용 세부 계정ID (K-IFRS Taxonomy)**

`fnlttSinglAcntAll` API는 회사가 공시한 **모든 세부 계정**을 반환한다. 삼성전자 기준으로 약 210개 항목이 내려오며, 유동자산 → 현금, 매출채권, 재고자산 등 하위 계정까지 포함된다. 아래는 재무모델링에 필수적인 세부 계정 목록이다.

**B/S — 재무상태표 (sj_div: "BS")**

```
[자산]
├── 유동자산 ─────────────────── ifrs-full_CurrentAssets
│   ├── 현금및현금성자산 ──────── ifrs-full_CashAndCashEquivalents
│   ├── 단기금융상품 ────────── ifrs-full_CurrentFinancialAssets
│   │                           또는 dart_ShortTermDepositsNotClassifiedAsCashEquivalents
│   ├── 단기투자증권 ────────── ifrs-full_CurrentFinancialAssetsAtFairValueThroughProfitOrLoss
│   ├── 매출채권 ──────────── ifrs-full_TradeAndOtherCurrentReceivables
│   │                           또는 ifrs-full_TradeReceivables
│   ├── 미수금 ───────────── ifrs-full_OtherCurrentReceivables
│   ├── 선급금 ───────────── ifrs-full_CurrentPrepaidExpenses
│   │                           또는 ifrs-full_CurrentAdvances
│   ├── 재고자산 ──────────── ifrs-full_Inventories
│   └── 기타유동자산 ─────────  ifrs-full_OtherCurrentAssets
│
├── 비유동자산 ──────────────── ifrs-full_NoncurrentAssets
│   ├── 장기금융상품 ─────────  ifrs-full_NoncurrentFinancialAssets
│   ├── 장기투자증권 ─────────  ifrs-full_NoncurrentFinancialAssetsAtFairValue...
│   ├── 관계기업투자 ─────────  ifrs-full_InvestmentsInAssociates
│   │                           또는 ifrs-full_InvestmentsAccountedForUsingEquityMethod
│   ├── 유형자산 ──────────── ifrs-full_PropertyPlantAndEquipment
│   │   ├── 토지 ─────────── ifrs-full_Land
│   │   ├── 건물 ─────────── ifrs-full_Buildings
│   │   ├── 구축물 ────────── ifrs-full_InvestmentProperty (또는 dart_ 확장)
│   │   ├── 기계장치 ─────── ifrs-full_Machinery
│   │   ├── 건설중인자산 ──── ifrs-full_ConstructionInProgress
│   │   └── 기타유형자산 ──── ifrs-full_OtherPropertyPlantAndEquipment
│   ├── 무형자산 ──────────── ifrs-full_IntangibleAssetsOtherThanGoodwill
│   ├── 영업권 ───────────── ifrs-full_Goodwill
│   ├── 사용권자산 ────────── ifrs-full_RightOfUseAssets
│   ├── 이연법인세자산 ────── ifrs-full_DeferredTaxAssets
│   └── 기타비유동자산 ────── ifrs-full_OtherNoncurrentAssets
│
└── 자산총계 ────────────────── ifrs-full_Assets

[부채]
├── 유동부채 ─────────────────── ifrs-full_CurrentLiabilities
│   ├── 매입채무 ──────────── ifrs-full_TradeAndOtherCurrentPayables
│   │                           또는 ifrs-full_TradePayables
│   ├── 단기차입금 ────────── ifrs-full_ShorttermBorrowings
│   │                           또는 dart_ShortTermBorrowings
│   ├── 유동성장기부채 ────── ifrs-full_CurrentPortionOfLongtermBorrowings
│   ├── 미지급금 ──────────── ifrs-full_OtherCurrentPayables
│   ├── 선수금 ───────────── ifrs-full_CurrentAdvancesReceived
│   ├── 미지급비용 ────────── ifrs-full_AccruedExpenses (또는 dart_ 확장)
│   ├── 유동리스부채 ─────── ifrs-full_CurrentLeaseLiabilities
│   ├── 미지급법인세 ─────── ifrs-full_CurrentTaxLiabilities
│   ├── 충당부채 ──────────── ifrs-full_CurrentProvisions
│   └── 기타유동부채 ─────── ifrs-full_OtherCurrentLiabilities
│
├── 비유동부채 ──────────────── ifrs-full_NoncurrentLiabilities
│   ├── 사채 ────────────── ifrs-full_BondsIssued (또는 dart_BondsIssued)
│   ├── 장기차입금 ────────── ifrs-full_LongtermBorrowings
│   ├── 비유동리스부채 ────── ifrs-full_NoncurrentLeaseLiabilities
│   ├── 퇴직급여부채(순확정급여부채) ── ifrs-full_NetDefinedBenefitLiability
│   ├── 이연법인세부채 ────── ifrs-full_DeferredTaxLiabilities
│   ├── 장기충당부채 ─────── ifrs-full_NoncurrentProvisions
│   └── 기타비유동부채 ────── ifrs-full_OtherNoncurrentLiabilities
│
└── 부채총계 ────────────────── ifrs-full_Liabilities

[자본]
├── 지배기업소유주지분 ─────── ifrs-full_EquityAttributableToOwnersOfParent
│   ├── 자본금 ───────────── ifrs-full_IssuedCapital
│   ├── 주식발행초과금 ────── ifrs-full_SharePremium
│   ├── 이익잉여금 ────────── ifrs-full_RetainedEarnings
│   ├── 기타포괄손익누계액 ── ifrs-full_AccumulatedOtherComprehensiveIncome
│   ├── 기타자본 ──────────── ifrs-full_OtherEquityInterest (또는 dart_ 확장)
│   └── 자기주식 ──────────── ifrs-full_TreasuryShares
├── 비지배지분 ──────────────── ifrs-full_NoncontrollingInterests
└── 자본총계 ────────────────── ifrs-full_Equity
```

**I/S — 손익계산서 (sj_div: "IS")**

```
매출액(수익) ──────────────────── ifrs-full_Revenue
매출원가 ─────────────────────── ifrs-full_CostOfSales
매출총이익 ───────────────────── ifrs-full_GrossProfit
판매비와관리비 ───────────────── dart_TotalSellingGeneralAdministrativeExpenses
│   ├── 급여 ────────────── (주석에서 추출 — Track B)
│   ├── 감가상각비 ─────── (주석에서 추출 — Track B)
│   ├── 무형자산상각비 ──── (주석에서 추출 — Track B)
│   ├── 지급수수료 ─────── (주석에서 추출 — Track B)
│   ├── 광고선전비 ─────── (주석에서 추출 — Track B)
│   └── ... 기타 ─────────── (주석에서 추출 — Track B)
영업이익 ─────────────────────── dart_OperatingIncomeLoss
금융수익 ─────────────────────── ifrs-full_FinanceIncome
│   ├── 이자수익 ─────────── ifrs-full_InterestRevenueCalculatedUsingEffectiveInterestMethod
│   └── 기타금융수익 ────── (회사별 확장)
금융비용 ─────────────────────── ifrs-full_FinanceCosts
│   ├── 이자비용 ─────────── ifrs-full_InterestExpense
│   └── 기타금융비용 ────── (회사별 확장)
기타영업외수익 ───────────────── ifrs-full_OtherIncome
기타영업외비용 ───────────────── ifrs-full_OtherExpense
관계기업투자손익 ─────────────── ifrs-full_ShareOfProfitLossOfAssociates...
법인세비용차감전순이익 ─────── ifrs-full_ProfitLossBeforeTax
법인세비용 ───────────────────── ifrs-full_IncomeTaxExpense
당기순이익 ───────────────────── ifrs-full_ProfitLoss
│   ├── 지배기업소유주 ──── ifrs-full_ProfitLossAttributableToOwnersOfParent
│   └── 비지배지분 ────── ifrs-full_ProfitLossAttributableToNoncontrollingInterests
기본주당이익 ─────────────────── ifrs-full_BasicEarningsLossPerShare
희석주당이익 ─────────────────── ifrs-full_DilutedEarningsLossPerShare
```

**C/F — 현금흐름표 (sj_div: "CF")**

```
영업활동현금흐름 ──────────────── ifrs-full_CashFlowsFromUsedInOperatingActivities
│   ├── 당기순이익 ────────── ifrs-full_ProfitLoss
│   ├── 조정항목 ────────── (회사별 세부 구성)
│   │   ├── 감가상각비 ──── ifrs-full_DepreciationAndAmortisationExpense
│   │   ├── 무형자산상각비  ifrs-full_AmortisationExpense
│   │   ├── 퇴직급여 ────── (dart_ 확장)
│   │   ├── 법인세비용 ──── ifrs-full_IncomeTaxExpense
│   │   └── 기타조정 ────── (회사별)
│   └── 운전자본변동 ─────── (회사별 세부 구성)
│       ├── 매출채권 증감 ── (회사별)
│       ├── 재고자산 증감 ── (회사별)
│       ├── 매입채무 증감 ── (회사별)
│       └── 기타 증감 ────── (회사별)
│
투자활동현금흐름 ──────────────── ifrs-full_CashFlowsFromUsedInInvestingActivities
│   ├── 유형자산 취득 ──── ifrs-full_PurchaseOfPropertyPlantAndEquipment (CAPEX)
│   ├── 유형자산 처분 ──── ifrs-full_ProceedsFromSalesOfPropertyPlantAndEquipment
│   ├── 무형자산 취득 ──── ifrs-full_PurchaseOfIntangibleAssets
│   ├── 단기금융상품 순증감  (회사별)
│   ├── 장기금융상품 순증감  (회사별)
│   └── 관계기업투자 ────── ifrs-full_PurchaseOfInvestments... (회사별)
│
재무활동현금흐름 ──────────────── ifrs-full_CashFlowsFromUsedInFinancingActivities
│   ├── 단기차입금 순증감 ── (회사별)
│   ├── 장기차입금 차입 ──── ifrs-full_ProceedsFromLongtermBorrowings
│   ├── 장기차입금 상환 ──── ifrs-full_RepaymentsOfLongtermBorrowings
│   ├── 사채 발행 ─────── ifrs-full_ProceedsFromIssuingBonds (회사별)
│   ├── 사채 상환 ─────── ifrs-full_RepaymentsOfBonds (회사별)
│   ├── 자기주식 취득 ──── ifrs-full_PaymentsToAcquireOwnEquityInstruments
│   ├── 배당금 지급 ────── ifrs-full_DividendsPaid
│   └── 리스부채 상환 ──── (IFRS 16, 회사별)
│
환율변동효과 ─────────────────── ifrs-full_EffectOfExchangeRateChangesOnCashAndCashEquivalents
현금및현금성자산 순증감 ───────── ifrs-full_IncreaseDecreaseInCashAndCashEquivalents
기초현금 ─────────────────────── ifrs-full_CashAndCashEquivalents (기초)
기말현금 ─────────────────────── ifrs-full_CashAndCashEquivalents (기말)
```

> **중요 참고사항**
> - `fnlttSinglAcntAll` API는 위 세부 계정을 **모두** 반환한다. 삼성전자 기준 BS 약 50개, IS 약 30개, CF 약 50개, CIS+SCE 포함 총 ~210개 항목.
> - 다만 `account_id`는 회사마다 **완전히 동일하지는 않다**. K-IFRS 표준 taxonomy(ifrs-full_)는 동일하지만, 회사별 확장 계정(dart_ 또는 회사 자체 계정)이 추가될 수 있다.
> - 예를 들어, 삼성전자는 `dart_ShortTermDepositsNotClassifiedAsCashEquivalents`(단기금융상품)를 쓰지만, 다른 회사는 `ifrs-full_CurrentFinancialAssets`를 쓸 수 있다.
> - **I/S의 판관비 상세**와 **C/F의 조정항목/운전자본 세부**는 API에서 합계만 제공하거나 회사마다 세부 분류가 다른 경우가 많아, 이 부분은 **Track B(주석 HTML 파싱)**로 보완해야 한다.

**Track A vs Track B 세부 커버리지 비교**

| 항목 | Track A (API) | Track B (주석) | 비고 |
|------|:---:|:---:|------|
| B/S 유동자산 세부 (현금, AR, 재고 등) | ✅ | — | API에서 대부분 세부 제공 |
| B/S 유형자산 세부 (토지, 건물, 기계 등) | ⚠️ 일부 | ✅ | 유형자산 주석에서 상세 내역 |
| B/S 비유동자산 세부 | ✅ | — | API에서 제공 |
| B/S 부채 세부 (AP, 차입금 등) | ✅ | — | API에서 제공 |
| I/S 매출~영업이익 | ✅ | — | API 표준 |
| I/S 판관비 상세 내역 | ❌ 합계만 | ✅ 필수 | 핵심 Track B 영역 |
| I/S 금융수익/비용 상세 | ⚠️ 일부 | ✅ | 이자수익/비용은 API, 나머지는 주석 |
| C/F 영업/투자/재무 대분류 | ✅ | — | API 표준 |
| C/F 조정항목 상세 | ⚠️ 회사별 | ✅ | 감가상각비 등 주석 보완 |
| C/F CAPEX 세부 | ✅ | ✅ | 유형자산취득은 API, 세부는 주석 |
| 부문별 매출 | ❌ | ✅ 필수 | 오직 주석에서만 |
| 수익 분해 (제품/용역별) | ❌ | ✅ 필수 | 오직 주석에서만 |

---

### Step 2B: 원문 다운로드 (Track B) — `dart_api.py`

`rcept_no`로 공시 원문 zip을 다운로드하고, 주석 관련 HTML 파일을 추출한다.

**사용 API**

| API | 엔드포인트 | 반환 |
|-----|-----------|------|
| 원본문서 | `/api/document.xml` | zip (HTML 파일들) |

**구현 포인트**

```python
import zipfile
import io
from pathlib import Path

class DartAPI:
    # ... (이어서)

    def download_document(self, rcept_no: str, save_dir: str) -> list[Path]:
        """공시 원문 HTML zip 다운로드 및 추출"""
        params = {
            "crtfc_key": self.api_key,
            "rcept_no": rcept_no,
        }
        resp = requests.get(
            f"{self.BASE_URL}/document.xml",
            params=params,
            stream=True,
        )

        save_path = Path(save_dir) / rcept_no
        save_path.mkdir(parents=True, exist_ok=True)

        # zip 해제
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            zf.extractall(save_path)

        # HTML 파일 목록 반환
        return list(save_path.glob("*.html")) + list(save_path.glob("*.htm"))
```

**주석 파일 식별 — `find_notes_files()`**

DART 공시 zip의 파일 구조는 표준화되어 있지 않다. 회사/공시마다 다르지만, 실제로 관찰되는 패턴은 크게 3가지이다.

```
패턴 A: 단일 HTML (소형 기업)
  └── 0001.html          ← 사업보고서 전체가 하나의 파일

패턴 B: 섹션별 분리 (대형 기업, 가장 흔함)
  ├── 0.html             ← 표지/목차
  ├── 1.html             ← 사업의 내용
  ├── 2.html             ← 재무제표 본문
  ├── 3.html             ← 재무제표 주석 ★
  ├── 4.html             ← 감사보고서
  └── ...

패턴 C: 서브디렉토리 포함
  ├── main.html
  ├── images/
  └── attached/
       ├── 연결재무제표.html
       ├── 재무제표주석.html  ★
       └── ...
```

핵심 문제: **주석 파일이 어느 것인지 파일명만으로는 알 수 없는 경우가 많다.** `3.html` 같은 숫자 파일명이 대부분이기 때문이다. 따라서 **파일 내용 기반 분류**가 필수이다.

```python
import re
from pathlib import Path
from bs4 import BeautifulSoup
from dataclasses import dataclass

@dataclass
class DartDocument:
    """공시 zip에서 식별된 문서"""
    path: Path
    doc_type: str          # "notes", "financial_statements", "audit", "toc", "other"
    fs_type: str | None    # "consolidated", "separate", None
    confidence: float      # 식별 신뢰도 (0~1)

class DocumentClassifier:
    """
    DART 공시 zip 내 HTML 파일을 분류하는 클래스.
    
    분류 전략:
    1. HTML 내 <title> 태그 확인
    2. 본문 첫 5,000자에서 키워드 매칭
    3. 주석 특유의 구조적 패턴 감지 (번호 매기기, 테이블 밀도)
    """

    # 주석 파일 식별 키워드 (가중치 포함)
    NOTES_KEYWORDS = {
        # 직접적 표현 (높은 가중치)
        "재무제표에 대한 주석": 1.0,
        "주석": 0.6,
        "재무제표주석": 1.0,
        "Notes to": 0.8,
        # 주석에서만 등장하는 내용 (중간 가중치)
        "중요한 회계정책": 0.7,
        "유의적인 회계정책": 0.7,
        "판매비와관리비": 0.5,    # 주석 상세
        "영업부문": 0.5,
        "수익의 분해": 0.5,
        "금융상품의 범주": 0.4,
        "법인세비용의 구성": 0.4,
    }

    # 주석이 아닌 문서의 키워드 (제외용)
    EXCLUDE_KEYWORDS = {
        "감사보고서": "audit",
        "내부회계관리": "audit",
        "사업의 내용": "business",
        "이사의 경영진단": "business",
        "주주총회": "toc",
        "목 차": "toc",
    }

    # 재무제표 본문 vs 주석 구분 키워드
    FS_BODY_KEYWORDS = [
        "재 무 상 태 표",  # DART 특유의 띄어쓰기
        "재무상태표",
        "손 익 계 산 서",
        "손익계산서",
        "현 금 흐 름 표",
        "현금흐름표",
    ]

    def classify_documents(self, html_files: list[Path]) -> list[DartDocument]:
        """모든 HTML 파일을 분류"""
        documents = []
        for f in html_files:
            doc = self._classify_single(f)
            documents.append(doc)
        return documents

    def find_notes_files(self, html_files: list[Path]) -> list[DartDocument]:
        """주석 파일만 필터링하여 반환 (신뢰도순 정렬)"""
        all_docs = self.classify_documents(html_files)
        notes = [d for d in all_docs if d.doc_type == "notes"]
        notes.sort(key=lambda d: d.confidence, reverse=True)

        if not notes:
            # fallback: 재무제표 파일 중 테이블이 가장 많은 파일
            print("  ⚠️ 주석 파일을 키워드로 식별 못함 → 테이블 밀도 기반 fallback")
            notes = self._fallback_by_table_density(html_files)

        return notes

    def _classify_single(self, html_path: Path) -> DartDocument:
        """단일 HTML 파일 분류"""
        try:
            content = html_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = html_path.read_text(encoding="euc-kr", errors="ignore")

        # 1. <title> 태그 확인
        title = ""
        title_match = re.search(r"<title>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = title_match.group(1).strip()

        # 2. 본문 앞부분 추출 (파싱 비용 절약)
        head_text = self._extract_text(content[:10000])

        # 3. 제외 키워드 체크 (감사보고서, 목차 등)
        for keyword, doc_type in self.EXCLUDE_KEYWORDS.items():
            if keyword in title or keyword in head_text[:500]:
                return DartDocument(
                    path=html_path, doc_type=doc_type,
                    fs_type=None, confidence=0.8,
                )

        # 4. 주석 키워드 점수 계산
        notes_score = 0.0
        search_text = title + " " + head_text
        for keyword, weight in self.NOTES_KEYWORDS.items():
            if keyword in search_text:
                notes_score += weight

        # 5. 재무제표 본문 vs 주석 구분
        # 본문은 "재무상태표" 등이 제목으로 나오고 테이블이 적음
        # 주석은 "1. 일반사항", "2. 중요한 회계정책" 등 번호 패턴 + 테이블이 많음
        is_fs_body = any(kw in head_text[:2000] for kw in self.FS_BODY_KEYWORDS)
        has_note_numbering = bool(re.search(
            r"(?:^|\n)\s*(?:\d+|[가-힣])\.\s*(?:일반사항|회계정책|중요한|유의적|현금)",
            head_text[:5000],
        ))

        if is_fs_body and notes_score < 0.5:
            return DartDocument(
                path=html_path, doc_type="financial_statements",
                fs_type=self._detect_fs_type(head_text), confidence=0.7,
            )

        # 6. 최종 판정
        if notes_score >= 0.5 or has_note_numbering:
            return DartDocument(
                path=html_path, doc_type="notes",
                fs_type=self._detect_fs_type(head_text),
                confidence=min(notes_score, 1.0),
            )

        return DartDocument(
            path=html_path, doc_type="other",
            fs_type=None, confidence=0.3,
        )

    def _detect_fs_type(self, text: str) -> str | None:
        """연결 vs 별도 재무제표 구분"""
        head = text[:3000]
        if "연결" in head:
            return "consolidated"
        elif "별도" in head or "개별" in head:
            return "separate"
        return None

    def _extract_text(self, html: str) -> str:
        """HTML에서 텍스트만 추출 (가볍게)"""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _fallback_by_table_density(self, html_files: list[Path]) -> list[DartDocument]:
        """
        키워드 매칭 실패 시: 테이블 수가 가장 많은 파일 = 주석일 확률 높음.
        
        근거: 주석은 판관비, 유형자산, 부문별 정보 등 수십 개 테이블을 포함하지만,
        재무제표 본문은 B/S, I/S, C/F 등 3~5개 테이블만 있음.
        """
        scored = []
        for f in html_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = f.read_text(encoding="euc-kr", errors="ignore")

            table_count = content.lower().count("<table")
            scored.append((f, table_count))

        scored.sort(key=lambda x: x[1], reverse=True)

        # 테이블 수 상위 파일을 주석으로 추정
        if scored and scored[0][1] > 10:
            return [DartDocument(
                path=scored[0][0], doc_type="notes",
                fs_type=None, confidence=0.4,
            )]

        return []
```

**사용 예시 (main.py에서)**

```python
classifier = DocumentClassifier()
notes_docs = classifier.find_notes_files(html_files)

for doc in notes_docs:
    print(f"  📄 {doc.path.name} (type={doc.fs_type}, confidence={doc.confidence:.1f})")
```

> **실무에서 만나는 엣지 케이스**
> - **패턴 A(단일 파일)**: 사업보고서 전체가 1개 HTML. 이 경우 주석은 문서 중간에 있으므로, BeautifulSoup으로 "주석" 섹션 시작점을 찾아 이후 테이블만 파싱해야 함
> - **인코딩 문제**: 구형 공시는 EUC-KR, 최근 공시는 UTF-8. 두 인코딩을 모두 시도하는 fallback 필수
> - **연결 + 별도 주석이 같은 파일**: `_detect_fs_type()`으로 구분하되, 기본값은 연결재무제표 우선

---

### Step 3B: 테이블 파싱 (Track B) — `html_parser.py`

HTML 주석 문서에서 테이블을 추출하고, 각 테이블이 어떤 주석 항목인지 라벨링한다.

**핵심 로직**

```python
import pandas as pd
from bs4 import BeautifulSoup
from dataclasses import dataclass

@dataclass
class NoteTable:
    note_title: str          # 주석 제목 (예: "판매비와관리비", "부문별 정보")
    df: pd.DataFrame         # 파싱된 테이블
    raw_html: str            # 원본 HTML (디버깅용)

class HTMLParser:

    # 추출 대상 주석 항목 키워드
    TARGET_NOTES = {
        "segment_revenue": ["영업부문", "부문별", "사업부문", "보고부문"],
        "sga_detail":      ["판매비와관리비", "판관비"],
        "depreciation":    ["유형자산", "감가상각"],
        "revenue_detail":  ["수익의 분해", "매출 구성", "제품별 매출"],
        "employee":        ["종업원", "임직원", "인건비"],
        "rnd":             ["연구개발", "경상연구"],
        "capex":           ["투자활동", "유형자산의 취득"],
    }

    def parse_notes(self, html_path: str) -> list[NoteTable]:
        """HTML에서 주석 테이블 추출"""
        with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        results = []
        tables = soup.find_all("table")

        for table in tables:
            # 테이블 바로 앞의 텍스트에서 주석 제목 추출
            title = self._find_table_title(table)
            note_type = self._classify_note(title)

            if note_type:
                try:
                    # pandas로 테이블 파싱
                    df = pd.read_html(str(table), header=0)[0]
                    df = self._clean_dataframe(df)

                    results.append(NoteTable(
                        note_title=title,
                        df=df,
                        raw_html=str(table),
                    ))
                except Exception as e:
                    print(f"테이블 파싱 실패: {title} - {e}")

        return results

    def _find_table_title(self, table_tag) -> str:
        """테이블 앞의 제목/문맥 텍스트 추출"""
        # 이전 형제 태그에서 텍스트 찾기
        for sibling in table_tag.previous_siblings:
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(strip=True)
                if len(text) > 2 and len(text) < 100:
                    return text
        return ""

    def _classify_note(self, title: str) -> str | None:
        """주석 제목으로 카테고리 분류"""
        for note_type, keywords in self.TARGET_NOTES.items():
            if any(kw in title for kw in keywords):
                return note_type
        return None

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """DataFrame 정리: 빈 행/열 제거, 숫자 변환"""
        # 완전히 빈 행/열 제거
        df = df.dropna(how="all").dropna(axis=1, how="all")

        # 숫자 컬럼 변환 (쉼표, 괄호 처리)
        for col in df.columns[1:]:  # 첫 번째 열은 계정명
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.replace("(", "-", regex=False)
                .str.replace(")", "", regex=False)
                .str.strip()
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df
```

**파싱 난이도별 대응**

| 난이도 | 상황 | 대응 |
|--------|------|------|
| 쉬움 | 깔끔한 `<table>` 태그 | `pd.read_html()` 직접 사용 |
| 보통 | 병합셀, 다중 헤더 | BeautifulSoup으로 전처리 후 파싱 |
| 어려움 | 서술형 주석 안에 숫자 산재 | LLM에게 원문 텍스트를 주고 테이블 구성 요청 |

---

### Step 4B: 계정명 정규화 (Track B) — `account_normalizer.py`

파이프라인의 핵심. 회사마다 다른 계정명을 표준 체계로 매핑한다.

**표준 계정 체계 (taxonomy.yaml)**

```yaml
# config/taxonomy.yaml
sga_detail:
  standard_accounts:
    - 급여
    - 퇴직급여
    - 복리후생비
    - 감가상각비
    - 무형자산상각비
    - 지급수수료
    - 광고선전비
    - 운반비
    - 세금과공과
    - 대손상각비
    - 기타판관비       # catch-all
  aliases:             # 자주 나오는 변형을 미리 등록
    급여및상여: 급여
    임직원급여: 급여
    종업원급여: 급여
    퇴직급여충당부채전입액: 퇴직급여
    수선유지비: 기타판관비

segment_revenue:
  standard_accounts:
    - 부문명            # 동적으로 결정됨
  note: "부문별 매출은 회사마다 부문명이 다르므로 LLM이 부문명 자체를 추출"

revenue_detail:
  standard_accounts:
    - 제품매출
    - 상품매출
    - 용역매출
    - 기타매출
```

**정규화 로직**

```python
import json
import yaml
import hashlib
from cache_db import CacheDB

class AccountNormalizer:

    def __init__(self, taxonomy_path: str, llm_client, cache_db: CacheDB):
        with open(taxonomy_path) as f:
            self.taxonomy = yaml.safe_load(f)
        self.llm = llm_client
        self.cache = cache_db

    def normalize(self, note_type: str, account_names: list[str], corp_code: str) -> dict:
        """
        계정명 리스트를 표준 계정명으로 매핑

        Returns: {원본계정명: 표준계정명} dict
        """
        # 1) 캐시 확인
        cache_key = self._make_cache_key(corp_code, note_type, account_names)
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # 2) 사전 매핑 (aliases)
        taxonomy_entry = self.taxonomy.get(note_type, {})
        aliases = taxonomy_entry.get("aliases", {})
        standard = taxonomy_entry.get("standard_accounts", [])

        mapping = {}
        unmapped = []

        for name in account_names:
            clean_name = name.strip()
            if clean_name in aliases:
                mapping[clean_name] = aliases[clean_name]
            elif clean_name in standard:
                mapping[clean_name] = clean_name
            else:
                unmapped.append(clean_name)

        # 3) 미매핑 항목은 LLM으로 처리
        if unmapped:
            llm_mapping = self._llm_normalize(note_type, unmapped, standard)
            mapping.update(llm_mapping)

        # 4) 캐시 저장
        self.cache.set(cache_key, mapping)

        return mapping

    def _llm_normalize(self, note_type: str, names: list[str], standard: list[str]) -> dict:
        """LLM을 사용한 계정명 매핑"""
        prompt = f"""한국 기업 재무제표의 '{note_type}' 주석에서 추출한 계정명을 표준 계정명에 매핑해줘.

## 규칙
- 의미가 같거나 포함 관계면 매핑
- 여러 표준 항목에 걸치면 가장 가까운 하나만 선택
- 어디에도 맞지 않으면 "기타"로 매핑
- 합계/소계 행이면 "SKIP"으로 매핑

## 입력
추출된 계정명: {json.dumps(names, ensure_ascii=False)}
표준 계정명: {json.dumps(standard, ensure_ascii=False)}

## 출력 형식
JSON만 반환. 설명 없이.
{{"원본계정명": "표준계정명", ...}}"""

        response = self.llm.chat(prompt)
        return json.loads(response)

    def _make_cache_key(self, corp_code, note_type, names):
        raw = f"{corp_code}:{note_type}:{sorted(names)}"
        return hashlib.md5(raw.encode()).hexdigest()
```

**캐싱 전략 — `cache_db.py`**

```python
import sqlite3
import json

class CacheDB:
    def __init__(self, db_path: str = "data/cache.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS account_mapping (
                cache_key TEXT PRIMARY KEY,
                mapping TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def get(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT mapping FROM account_mapping WHERE cache_key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, mapping: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO account_mapping (cache_key, mapping) VALUES (?, ?)",
            (key, json.dumps(mapping, ensure_ascii=False)),
        )
        self.conn.commit()
```

> 캐싱의 효과: 삼성전자가 매 분기 같은 계정명을 사용한다면, 최초 1회만 LLM 호출하고 이후는 즉시 매핑된다. 회사 수가 늘어나도 LLM 비용이 선형 증가하지 않는다.

---

### Step 5: 엑셀 세팅 — `excel_writer.py`

Track A(3대 재무제표)와 Track B(주석 상세)를 모두 엑셀 템플릿에 삽입한다.

**구현 포인트**

```python
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
import pandas as pd

class ExcelWriter:

    def __init__(self, template_path: str):
        self.wb = load_workbook(template_path)

    # ──────────────────────────────────────────────
    # Track A: 3대 재무제표
    # ──────────────────────────────────────────────

    def write_balance_sheet(self, df: pd.DataFrame, sheet_name: str = "BS"):
        """
        재무상태표 기입

        df 컬럼: [계정ID, 계정명, 2020, 2021, 2022, 2023, 2024]
        """
        self._write_statement(df, sheet_name)

    def write_income_statement(self, df: pd.DataFrame, sheet_name: str = "IS"):
        """손익계산서 기입"""
        self._write_statement(df, sheet_name)

    def write_cash_flow(self, df: pd.DataFrame, sheet_name: str = "CF"):
        """현금흐름표 기입"""
        self._write_statement(df, sheet_name)

    def _write_statement(self, df: pd.DataFrame, sheet_name: str):
        """
        3대 재무제표 공통 기입 로직

        전략: 템플릿의 계정ID와 API의 계정ID를 매칭
        → 표준 taxonomy이므로 회사가 달라도 동일한 ID로 매칭 가능
        → 단, 회사별 확장 계정(dart_ 등)은 aliases로 보완
        """
        ws = self.wb[sheet_name]

        # 회사별 확장 계정 → 표준 계정ID 매핑 (같은 의미의 다른 ID)
        ACCOUNT_ALIASES = {
            # 단기금융상품: 회사마다 다른 ID 사용
            "dart_ShortTermDepositsNotClassifiedAsCashEquivalents": "ifrs-full_CurrentFinancialAssets",
            # 단기차입금
            "dart_ShortTermBorrowings": "ifrs-full_ShorttermBorrowings",
            # 사채
            "dart_BondsIssued": "ifrs-full_BondsIssued",
            # 판관비 합계
            "dart_TotalSellingGeneralAdministrativeExpenses": "dart_TotalSGA",
            # 필요시 추가...
        }

        # 템플릿에서 계정ID → 행번호 매핑 읽기
        id_row_map = {}
        for row in ws.iter_rows(min_col=1, max_col=1):
            for cell in row:
                if cell.value and str(cell.value).startswith(("ifrs-", "dart_")):
                    id_row_map[str(cell.value).strip()] = cell.row

        col_map = self._read_col_map(ws, header_row=3)

        year_columns = [c for c in df.columns if c not in ("계정ID", "계정명")]
        unmatched = []

        for _, row_data in df.iterrows():
            account_id = row_data["계정ID"]
            target_row = None

            # 1차: 직접 매칭
            if account_id in id_row_map:
                target_row = id_row_map[account_id]
            # 2차: aliases를 통한 매칭
            elif account_id in ACCOUNT_ALIASES:
                alias = ACCOUNT_ALIASES[account_id]
                if alias in id_row_map:
                    target_row = id_row_map[alias]
            # 3차: 역방향 aliases (템플릿이 dart_, API가 ifrs- 인 경우)
            else:
                for alias_from, alias_to in ACCOUNT_ALIASES.items():
                    if alias_to == account_id and alias_from in id_row_map:
                        target_row = id_row_map[alias_from]
                        break

            if target_row is None:
                unmatched.append((account_id, row_data["계정명"]))
                continue

            for year in year_columns:
                if year in col_map and pd.notna(row_data[year]):
                    ws.cell(row=target_row, column=col_map[year], value=row_data[year])

        if unmatched:
            print(f"  ⚠️ {sheet_name}: 미매칭 {len(unmatched)}개 계정")
            for aid, anm in unmatched[:5]:
                print(f"     - {anm} ({aid})")

    # ──────────────────────────────────────────────
    # Track B: 주석 상세
    # ──────────────────────────────────────────────

    def write_sga_detail(self, data: dict, sheet_name: str = "판관비"):
        """
        판관비 상세를 엑셀에 기입

        data 형태:
        {
            "급여": {"2023": 150000, "2022": 140000},
            "감가상각비": {"2023": 30000, "2022": 28000},
            ...
        }
        """
        ws = self.wb[sheet_name]

        # 템플릿에 미리 정의된 행 매핑 (표준 계정명 → 행번호)
        row_map = self._read_row_map(ws, account_col="B")

        # 템플릿에 미리 정의된 열 매핑 (연도 → 열번호)
        col_map = self._read_col_map(ws, header_row=3)

        for account_name, yearly_values in data.items():
            if account_name not in row_map:
                print(f"⚠️ 템플릿에 없는 계정: {account_name}")
                continue
            row = row_map[account_name]

            for year, value in yearly_values.items():
                if year not in col_map:
                    continue
                col = col_map[year]
                ws.cell(row=row, column=col, value=value)

    def write_segment_revenue(self, data: dict, sheet_name: str = "부문별매출"):
        """부문별 매출은 부문명이 동적이므로 행을 동적으로 생성"""
        ws = self.wb[sheet_name]
        start_row = 5  # 데이터 시작 행

        for i, (segment_name, yearly_values) in enumerate(data.items()):
            row = start_row + i
            ws.cell(row=row, column=2, value=segment_name)

            for year, value in yearly_values.items():
                col = self._year_to_col(ws, year, header_row=3)
                if col:
                    ws.cell(row=row, column=col, value=value)

    def save(self, output_path: str):
        self.wb.save(output_path)

    def _read_row_map(self, ws, account_col="B") -> dict:
        """시트에서 계정명 → 행번호 매핑 읽기"""
        row_map = {}
        for row in ws.iter_rows(min_col=2, max_col=2):
            for cell in row:
                if cell.value and isinstance(cell.value, str):
                    row_map[cell.value.strip()] = cell.row
        return row_map

    def _read_col_map(self, ws, header_row=3) -> dict:
        """시트에서 연도 → 열번호 매핑 읽기"""
        col_map = {}
        for cell in ws[header_row]:
            if cell.value:
                col_map[str(cell.value).strip()] = cell.column
        return col_map
```

---

### 파이프라인 실행 — `main.py`

```python
from src.dart_api import DartAPI
from src.corp_code_db import CorpCodeDB
from src.financial_statements import FinancialStatementFetcher, StatementType, build_time_series
from src.document_classifier import DocumentClassifier
from src.html_parser import HTMLParser
from src.account_normalizer import AccountNormalizer
from src.excel_writer import ExcelWriter
from src.cache_db import CacheDB

def run_pipeline(
    company_name: str,
    corp_code: str | None = None,  # None이면 company_name으로 자동 검색
    years: list[str] = None,       # 예: ["2022", "2023", "2024"]
    api_key: str = "",
    llm_client = None,             # None이면 OpenClawLLM 자동 생성
    fs_div: str = "CFS",           # CFS=연결, OFS=별도
):
    # ============================================
    # 초기화
    # ============================================
    dart = DartAPI(api_key)
    fetcher = FinancialStatementFetcher(api_key)
    classifier = DocumentClassifier()
    parser = HTMLParser()
    cache = CacheDB()

    # LLM 클라이언트: OpenClaw 게이트웨이 또는 직접 API
    if llm_client is None:
        llm_client = OpenClawLLM()  # localhost:4141 기본
    normalizer = AccountNormalizer("config/taxonomy.yaml", llm_client, cache)
    writer = ExcelWriter("templates/financial_model.xlsx")

    # corp_code 자동 조회
    if corp_code is None:
        corp_code = dart.get_corp_code(company_name)
        if corp_code is None:
            raise ValueError(f"'{company_name}' 에 해당하는 corp_code를 찾을 수 없음")
        print(f"🏢 {company_name} → corp_code: {corp_code}")

    if years is None:
        years = ["2022", "2023", "2024"]

    # ============================================
    # Track A: 3대 재무제표 (구조화 API)
    # ============================================
    print("═" * 50)
    print("Track A: 3대 재무제표 수집")
    print("═" * 50)

    multi_year = fetcher.fetch_multi_year(corp_code, years, fs_div=fs_div)

    # B/S 시계열 조합 → 엑셀 기입
    bs_ts = build_time_series(multi_year, StatementType.BS)
    if not bs_ts.empty:
        writer.write_balance_sheet(bs_ts)
        print(f"  📊 B/S: {len(bs_ts)}개 계정, {len(years)}개년")

    # I/S 시계열 조합 → 엑셀 기입
    is_ts = build_time_series(multi_year, StatementType.IS)
    if not is_ts.empty:
        writer.write_income_statement(is_ts)
        print(f"  📊 I/S: {len(is_ts)}개 계정, {len(years)}개년")

    # C/F 시계열 조합 → 엑셀 기입
    cf_ts = build_time_series(multi_year, StatementType.CF)
    if not cf_ts.empty:
        writer.write_cash_flow(cf_ts)
        print(f"  📊 C/F: {len(cf_ts)}개 계정, {len(years)}개년")

    # ============================================
    # Track B: 주석 데이터 (HTML 파싱)
    # ============================================
    print("\n" + "═" * 50)
    print("Track B: 주석 데이터 추출")
    print("═" * 50)

    # 가장 최근 연도의 사업보고서에서 주석 추출
    latest_year = max(years)
    reports = dart.get_reports(corp_code, latest_year)
    rcept_no = reports[0]["rcept_no"]
    print(f"  📋 공시번호: {rcept_no}")

    # 원문 다운로드
    html_files = dart.download_document(rcept_no, f"data/raw/{company_name}")

    # 주석 파일 식별 (DocumentClassifier 사용)
    notes_docs = classifier.find_notes_files(html_files)
    print(f"  📄 주석 파일 {len(notes_docs)}개 발견")
    for doc in notes_docs:
        print(f"     - {doc.path.name} (fs={doc.fs_type}, conf={doc.confidence:.1f})")

    # 연결재무제표 주석 우선 (없으면 전체)
    consolidated_notes = [d for d in notes_docs if d.fs_type == "consolidated"]
    target_notes = consolidated_notes if consolidated_notes else notes_docs

    # 테이블 파싱
    all_tables = []
    for doc in target_notes:
        tables = parser.parse_notes(str(doc.path))
        all_tables.extend(tables)
    print(f"  📊 주석 테이블 {len(all_tables)}개 추출")

    # 정규화 + 엑셀 세팅
    for table in all_tables:
        account_names = table.df.iloc[:, 0].tolist()
        mapping = normalizer.normalize(
            note_type=table.note_title,
            account_names=account_names,
            corp_code=corp_code,
        )
        table.df["표준계정"] = table.df.iloc[:, 0].map(mapping)

        # note_type에 따라 적절한 writer 메서드 호출
        if "sga" in table.note_title:
            writer.write_sga_detail(table.df)
        elif "segment" in table.note_title:
            writer.write_segment_revenue(table.df)
        # ... 기타 주석 항목

    # ============================================
    # 저장
    # ============================================
    output_path = f"data/{company_name}_{latest_year}_model.xlsx"
    writer.save(output_path)
    print(f"\n✅ 완료: {output_path}")


if __name__ == "__main__":
    # 방법 1: OpenClaw 게이트웨이 경유 (추천)
    run_pipeline(
        company_name="삼성전자",
        years=["2022", "2023", "2024"],
        api_key="YOUR_DART_API_KEY",
        # llm_client 생략 → OpenClawLLM 자동 생성
    )

    # 방법 2: 직접 API 호출 (OpenClaw 없을 때)
    # from src.llm_client import DirectLLM
    # run_pipeline(
    #     company_name="SK하이닉스",
    #     years=["2022", "2023", "2024"],
    #     api_key="YOUR_DART_API_KEY",
    #     llm_client=DirectLLM(provider="gemini", model="gemini-2.5-flash-lite"),
    # )
```

---

## 5. LLM 연동: OpenClaw API 라우팅

> 로컬 LLM(Qwen2.5 14B) 대신 OpenClaw에 연동된 클라우드 모델을 사용한다.
> Track C(XBRL) 도입으로 LLM 필요 케이스가 대폭 줄었으므로, 비용 부담이 낮다.

### 5-1. 모델별 역할 배분

| 작업 | 난이도 | 추천 모델 | 이유 |
|------|:---:|------|------|
| 계정명 매핑 (aliases miss) | 낮음 | **GPT-5.2*** | 무료, 단순 매핑에 충분 |
| entity_ 확장 계정 해석 | 중간 | **GPT-5.2** (Codex) | 400K 컨텍스트, 추론 강력 |
| TextBlock 서술형 파싱 | 높음 | **GPT-5.2** (Codex) | 긴 문서 + 숫자 추출 정확도 |
| 복합 테이블 구조 해석 | 높음 | **GPT-5.2** (Codex) | 복잡 추론, Opus급 성능 |
| 벌크 처리 (수십 개 회사) | — | **GPT-5.2*** | RPD 1,000, 속도 최고 |

### 5-2. OpenClaw Gateway 활용 LLM 클라이언트

OpenClaw의 게이트웨이는 `http://localhost:PORT`에서 OpenAI 호환 API를 제공한다.
별도 SDK 없이 표준 `openai` 패키지로 접근 가능.

```python
import json
import os
from openai import OpenAI

class OpenClawLLM:
    """
    OpenClaw 게이트웨이를 통한 LLM 클라이언트.
    
    OpenClaw이 Google Antigravity, GitHub Copilot, Groq 등의
    인증/라우팅을 처리하므로, 파이프라인에서는 모델 ID만 지정하면 된다.
    """

    # 작업별 기본 모델 라우팅
    MODEL_ROUTES = {
        "account_mapping":    "openai-codex/gpt-5.2",             # 무료, 단순 매핑
        "entity_interpret":   "openai-codex/gpt-5.2",             # 400K 컨텍스트, 회계 추론
        "textblock_parse":    "openai-codex/gpt-5.2",             # 긴 텍스트 파싱 + 추론
        "complex_table":      "openai-codex/gpt-5.2",             # 복합 테이블 구조 해석
        "bulk_normalize":     "openai-codex/gpt-5.2",             # 속도 우선
    }

    def __init__(
        self,
        gateway_url: str = "http://localhost:4141",  # OpenClaw 기본 포트
        default_model: str = "google/gemini-2.5-flash-lite",
    ):
        self.client = OpenAI(
            base_url=f"{gateway_url}/v1",
            api_key="openclaw",  # 게이트웨이가 인증 처리
        )
        self.default_model = default_model

    def chat(
        self,
        prompt: str,
        task_type: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """
        LLM 호출. task_type으로 자동 라우팅하거나 model 직접 지정.

        Args:
            prompt: 프롬프트
            task_type: "account_mapping" | "entity_interpret" | "textblock_parse" | ...
            model: 직접 지정 시 task_type 무시
            temperature: 계정 매핑은 0.0~0.1 권장
            max_tokens: 응답 최대 토큰
        """
        selected_model = (
            model
            or self.MODEL_ROUTES.get(task_type, self.default_model)
        )

        response = self.client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content

    def chat_json(
        self,
        prompt: str,
        task_type: str | None = None,
        **kwargs,
    ) -> dict:
        """JSON 응답을 파싱해서 반환"""
        raw = self.chat(prompt, task_type=task_type, **kwargs)

        # ```json ... ``` 블록 제거
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]  # 첫 줄 제거
            clean = clean.rsplit("```", 1)[0]  # 마지막 ``` 제거

        return json.loads(clean)
```

### 5-3. 직접 API 호출 방식 (OpenClaw 없이도 가능)

OpenClaw 게이트웨이 없이 각 프로바이더 API를 직접 호출하는 경우:

```python
class DirectLLM:
    """
    OpenClaw 없이 API 직접 호출.
    프로바이더별 API 키를 환경변수로 관리.
    """

    PROVIDERS = {
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "env_key": "GOOGLE_API_KEY",
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "env_key": "GROQ_API_KEY",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "env_key": "DEEPSEEK_API_KEY",
        },
    }

    def __init__(self, provider: str = "gemini", model: str = "gemini-2.5-flash-lite"):
        config = self.PROVIDERS[provider]
        self.client = OpenAI(
            base_url=config["base_url"],
            api_key=os.environ[config["env_key"]],
        )
        self.model = model

    def chat(self, prompt: str, **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return response.choices[0].message.content
```

### 5-4. 비용 최적화 전략

Track C(XBRL) 도입 후 LLM 호출이 필요한 경우:

| 단계 | LLM 필요? | 이유 |
|------|:---------:|------|
| B/S, I/S, C/F 값 추출 | ❌ | Track A(API) |
| 주석 구조 파악 | ❌ | Track C — pre.xml role 매핑 |
| 계정명 → 한국어 라벨 | ❌ | Track C — lab-ko.xml |
| 판관비 상세 매핑 | ❌ | Track C — dart_ 표준 계정 |
| 현금흐름 조정항목 | ❌ | Track C — dart_/ifrs 표준 |
| **entity_ 확장 계정** | ✅ | 회사 고유, lab-ko.xml 라벨로 1차 시도 후 실패 시 |
| **TextBlock 비정형 파싱** | ✅ | 서술형 주석 안 숫자 추출 |
| **cal.xml에 없는 검증** | ✅ | 비표준 계산관계 확인 |

**비용 시뮬레이션 (회사 1개 기준):**

```
Track C로 처리:      ~40개 주석 항목 → LLM 0원
entity_ 해석:        ~5-10회 호출 × Gemini Flash Lite(무료) → 0원
TextBlock 파싱:      ~2-3회 호출 × Gemini 2.5 Pro(무료) → 0원
복합 테이블:         ~0-1회 호출 × Opus 4.6(Antigravity 무료) → 0원

→ 총 비용: 0원 (Google 무료 + Antigravity OAuth)
→ Groq Llama 사용 시에도 무료 (RPD 1,000)
```

| 전략 | 설명 | 절감 효과 |
|------|------|----------|
| **Track C 우선** | XBRL에서 구조+라벨 먼저 추출 | LLM 호출 80%+ 감소 |
| **aliases 사전** | 자주 나오는 변형을 yaml에 미리 등록 | 나머지 50~70% 감소 |
| **SQLite 캐싱** | 회사-주석유형별 매핑 결과 저장 | 같은 회사 반복 조회 시 0원 |
| **무료 모델 우선** | Gemini Flash Lite → Groq → Opus 순 | 유료 API 0원 |
| **배치 처리** | 여러 계정명을 한 번에 매핑 요청 | API 호출 횟수 최소화 |

---

## 6. 예상 난관과 대응

> **⚠️ SNT다이내믹스 XBRL 분석으로 발견된 핵심 수정사항 (2025.02)**
>
> 실제 XBRL 파일(`entity00134477_2025-12-31`)을 분석한 결과, 기존 Track B(HTML 파싱) 설계에 근본적 보완이 필요하다. **XBRL 자체가 주석 데이터를 구조화해서 갖고 있으며**, HTML 파싱보다 훨씬 정확한 Track C(XBRL 직접 파싱)를 추가해야 한다.

### 6-0. 신규: Track C — XBRL 주석 직접 파싱

**발견 사실: DART XBRL에는 주석 데이터가 구조화되어 있다**

XBRL zip 안의 5개 파일이 하는 역할:

| 파일 | 역할 | 활용 |
|------|------|------|
| `_pre.xml` | 프레젠테이션 — 어떤 계정이 어떤 주석에 속하는지 **role별 구조** | 주석 항목 분류의 정답지 |
| `_lab-ko.xml` | 한국어 라벨 — account_id → 한국어 계정명 매핑 | LLM 없이 계정명 확보 |
| `_lab-en.xml` | 영어 라벨 | 글로벌 매핑용 |
| `_cal.xml` | 계산 관계 — 합계/구성 관계 | 계정간 관계 파악 |
| `_def.xml` | 정의 — 테이블 축/구성원 정의 | 다차원 데이터 구조 |

**핵심 발견: role 코드가 IFRS 주석 번호를 반영한다**

SNT다이내믹스의 pre.xml에서 발견된 75개 role 중, 재무모델링에 핵심적인 role들:

```
[판관비 상세]
  D431410 = 연결 포괄손익계산서 (I/S 전체 + 판관비 합계)
  D431415 = 별도 포괄손익계산서

[주석 상세 — 여기가 핵심]
  ias_16_role-D822100  = 유형자산 변동 (취득/처분/감가상각)     ← 연결
  ias_16_role-D822105  = 유형자산 변동                      ← 별도
  ias_38_role-D823180  = 무형자산 변동
  ias_2_role-D826380   = 재고자산 내역 (상품/제품/재공/원재료)
  ias_37_role-D827570  = 충당부채
  ifrs_15_role-D831150 = 수익 분해 (제품별/지역별)            ★
  ifrs_16_role-D832610 = 리스 (사용권자산/리스부채)
  ias_19_role-D834480  = 종업원급여 (급여/퇴직급여/복리후생)
  ias_12_role-D835110  = 법인세
  ias_33_role-D838000  = 주당이익
  ias_7_role-D851100   = 현금흐름 조정항목 상세                ★
  ifrs_8_role-D871100  = 영업부문 정보                      ★
  ias_24_role-D818000  = 특수관계자 거래

[entity 확장 (U로 시작)]
  entity_role-U800300  = 회사 고유 주석 (기타)               ← 회사마다 다름
```

**핵심 발견: 판관비 상세 계정이 DART 확장(dart_)으로 표준화되어 있다**

HTML 파싱 없이 XBRL에서 직접 추출 가능한 판관비 계정들:

```
dart_SalariesWages                                          → 급여
dart_ProvisionForSeveranceIndemnities                        → 퇴직급여
dart_EmployeeBenefits 또는 dart_EmployeeBenefitsSellingGeneralAdministrativeExpenses → 복리후생비
dart_DepreciationExpenseSellingGeneralAdministrativeExpenses  → 감가상각비
dart_BadDebtExpensesSellingGeneralAdministrativeExpenses      → 대손상각비
dart_CommissionsSellingGeneralAdministrativeExpenses          → 지급수수료
dart_AdvertisingExpensesSellingGeneralAdministrativeExpenses  → 광고선전비
dart_FreightExpensesSellingGeneralAdministrativeExpenses      → 운반비
dart_TaxesDuesSellingGeneralAdministrativeExpenses            → 세금과공과
dart_TravelExpensesSellingGeneralAdministrativeExpenses       → 여비교통비
dart_TrainingExpensesSellingGeneralAdministrativeExpenses     → 교육훈련비
dart_RentalExpensesSellingGeneralAdministrativeExpenses       → 임차료
dart_InsurancePremiumsSellingGeneralAdministrativeExpenses    → 보험료
dart_OrdinaryDevelopmentExpenseSellingGeneralAdministrativeExpenses → 경상개발비
dart_EntertainmentExpensesSellingGeneralAdministrativeExpenses → 접대비
dart_MiscellaneousExpenses                                   → 기타판관비
dart_TotalSellingGeneralAdministrativeExpenses                → 판관비 합계
```

→ **이 계정들은 dart_ 접두어로 표준화되어 있어서, 회사가 달라도 동일한 ID를 사용한다.**
→ 기존 설계의 "회사마다 계정명이 달라서 LLM 정규화가 필요하다"는 전제가 **판관비에 한해서는** 틀렸다.

**Track C 구현 — `xbrl_parser.py`**

```python
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass

@dataclass
class XBRLNote:
    role_code: str           # 예: "D831150"
    role_name: str           # 예: "수익 분해"
    accounts: list[dict]     # [{account_id, label_ko, label_en, source}]
    members: list[dict]      # 다차원 축 (부문명, 자산 유형 등)

class XBRLNoteParser:
    """
    XBRL 파일에서 주석 데이터를 직접 파싱.
    
    HTML 파싱(Track B)보다 정확하고 LLM이 불필요한 경우가 많다.
    단, XBRL에는 '구조'만 있고 '값(금액)'은 인스턴스 문서에 있으므로,
    구조 + 라벨 추출에 사용하고, 금액은 fnlttSinglAcntAll API(Track A)
    또는 인스턴스 문서에서 가져온다.
    """

    # 재무모델링에 필요한 주석 role 매핑
    # role 코드 끝자리: 0 = 연결, 5 = 별도
    #
    # [SK하이닉스 vs SNT다이내믹스 비교 검증 완료]
    # 53개 공통 role 확인. 아래는 핵심 role만 수록.
    # 회사에 따라 추가 role이 있을 수 있음 (예: SK는 D834120 주식기준보상)
    NOTE_ROLES = {
        # ── 3대 재무제표 (Track A와 겹치지만, XBRL에서 추가 구조 확인용) ──
        "D210000": "재무상태표",         # dart
        "D431410": "포괄손익계산서",       # dart
        "D520000": "현금흐름표",          # dart
        "D610000": "자본변동표",          # dart

        # ── 비용 구조 (핵심) ──
        "D834300": "비용성격별분류",       # dart — 제조+판관비를 성격별 합산 ★
        "D834310": "판관비상세",          # dart — 판관비 세부 항목 ★
        "D834320": "판관비상세2",         # dart — 일부 회사에서 사용
        "D834330": "판관비상세3",         # dart — 일부 회사에서 사용

        # ── 자산/부채 상세 ──
        "D822100": "유형자산",           # ias_16
        "D823180": "무형자산",           # ias_38
        "D825100": "투자부동산",          # ias_40
        "D826380": "재고자산",           # ias_2
        "D827570": "충당부채",           # ias_37

        # ── 금융상품 ──
        "D822300": "금융자산상세",         # dart
        "D822310": "금융부채상세",         # dart
        "D822380": "금융상품위험",         # dart/ifrs_7
        "D822390": "금융자산범주",         # ifrs_7
        "D822400": "차입금상세",          # ifrs_7 — SK하이닉스에서 발견
        "D822420": "공정가치측정",         # ifrs_7
        "D822430": "공정가치서열",         # ifrs_7
        "D822470": "사용제한금융자산",      # ifrs_7 — SK하이닉스에서 발견
        "D822490": "기타지급채무",         # ifrs_7 — SK하이닉스에서 발견

        # ── 수익/부문 ──
        "D831150": "수익분해",           # ifrs_15 ★
        "D871100": "영업부문",           # ifrs_8  ★

        # ── 기타 주석 ──
        "D832610": "리스",             # ifrs_16
        "D834120": "주식기준보상",         # ifrs_2 — SK하이닉스에서 발견
        "D834480": "종업원급여",          # ias_19
        "D835110": "법인세",            # ias_12
        "D838000": "주당이익",           # ias_33
        "D818000": "특수관계자",          # ias_24
        "D851100": "현금흐름조정",         # ias_7  ★
        "D861200": "자본상세",           # ias_1
        "D861300": "이익잉여금",          # ias_1
    }

    def __init__(self, xbrl_dir: str):
        """XBRL zip 해제 후 디렉토리 경로"""
        self.dir = Path(xbrl_dir)
        self.labels_ko = {}   # account_id → 한국어 라벨
        self.labels_en = {}   # account_id → 영어 라벨

    def parse(self) -> list[XBRLNote]:
        """전체 파싱 실행"""
        # 1. 라벨 로드
        self._load_labels()

        # 2. pre.xml에서 role별 계정 구조 추출
        notes = self._parse_presentation()

        return notes

    def _load_labels(self):
        """lab-ko.xml, lab-en.xml에서 account_id → 라벨 매핑 구축"""
        ns_xlink = "http://www.w3.org/1999/xlink"

        for lang, attr in [("ko", self.labels_ko), ("en", self.labels_en)]:
            lab_file = list(self.dir.glob(f"*_lab-{lang}.xml"))
            if not lab_file:
                continue

            tree = ET.parse(lab_file[0])
            root = tree.getroot()

            # loc → label 매핑 구축
            current_account = None
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if tag == "loc":
                    href = elem.get(f"{{{ns_xlink}}}href", "")
                    if "#" in href:
                        current_account = href.split("#")[-1]

                if tag == "label" and current_account:
                    role = elem.get(f"{{{ns_xlink}}}role", "")
                    # 기본 라벨만 (terseLabel, verboseLabel 등 제외)
                    if role == "http://www.xbrl.org/2003/role/label":
                        text = (elem.text or "").strip()
                        if text and current_account not in attr:
                            attr[current_account] = text

        print(f"  📝 라벨 로드: KO={len(self.labels_ko)}개, EN={len(self.labels_en)}개")

    def _parse_presentation(self) -> list[XBRLNote]:
        """pre.xml에서 role별 계정 구조 추출"""
        ns = {
            "link": "http://www.xbrl.org/2003/linkbase",
            "xlink": "http://www.w3.org/1999/xlink",
        }

        pre_file = list(self.dir.glob("*_pre.xml"))
        if not pre_file:
            return []

        tree = ET.parse(pre_file[0])
        root = tree.getroot()

        notes = []
        for plink in root.findall(".//link:presentationLink", ns):
            role_uri = plink.get(f'{{{ns["xlink"]}}}role', "")
            role_code = role_uri.split("role-")[-1] if "role-" in role_uri else ""

            # 홀수 코드 = 연결, 짝수+5 = 별도. 연결만 기본 파싱
            base_code = role_code.rstrip("5") if role_code.endswith("5") else role_code
            if base_code not in self.NOTE_ROLES:
                continue
            if role_code.endswith("5"):  # 별도 재무제표는 스킵 (옵션)
                continue

            role_name = self.NOTE_ROLES[base_code]

            # loc 태그에서 계정 추출
            accounts = []
            members = []
            for loc in plink.findall("link:loc", ns):
                href = loc.get(f'{{{ns["xlink"]}}}href', "")
                if "#" not in href:
                    continue
                account_id = href.split("#")[-1]

                # 소스 구분
                if "entity" in href:
                    source = "company"
                elif "dart_" in href:
                    source = "dart"
                else:
                    source = "ifrs"

                # Member는 별도 분류 (다차원 축)
                if "Member" in account_id:
                    members.append({
                        "account_id": account_id,
                        "label_ko": self.labels_ko.get(account_id, ""),
                        "source": source,
                    })
                # Abstract, Table, LineItems는 구조용이므로 스킵
                elif any(kw in account_id for kw in ["Abstract", "Table", "LineItems", "Axis"]):
                    continue
                else:
                    accounts.append({
                        "account_id": account_id,
                        "label_ko": self.labels_ko.get(account_id, ""),
                        "label_en": self.labels_en.get(account_id, ""),
                        "source": source,
                    })

            notes.append(XBRLNote(
                role_code=role_code,
                role_name=role_name,
                accounts=accounts,
                members=members,
            ))

        return notes

    def get_sga_accounts(self) -> dict[str, str]:
        """
        판관비 상세 계정 추출 (dart_ 표준 계정).
        
        LLM 정규화 불필요 — dart_ 접두어 계정은 회사가 달라도 동일.
        Returns: {account_id: 한국어 라벨}
        """
        sga_prefix = "dart_"
        sga_suffix_keywords = [
            "SellingGeneralAdministrativeExpenses",
            "SalariesWages",
            "ProvisionForSeveranceIndemnities",
            "EmployeeBenefits",
            "MiscellaneousExpenses",
            "TotalSellingGeneralAdministrativeExpenses",
        ]

        result = {}
        for acc_id, label in self.labels_ko.items():
            if not acc_id.startswith(sga_prefix):
                continue
            if any(kw in acc_id for kw in sga_suffix_keywords) or "판관비" in label:
                result[acc_id] = label

        return result

    def get_segment_members(self) -> list[dict]:
        """영업부문 Member 추출 (회사별 고유 부문명)"""
        notes = self.parse()
        for note in notes:
            if note.role_name == "영업부문":
                return [m for m in note.members if m["source"] == "company"]
        return []
```

**Track A + C 조합으로 LLM 불필요한 경우가 대폭 증가**

```
변경 전 (Track A + Track B):
  Track A(API)  → B/S, I/S, C/F 값
  Track B(HTML) → 주석 상세 (LLM 정규화 필수)

변경 후 (Track A + Track C + Track B fallback):
  Track A(API)   → B/S, I/S, C/F 값
  Track C(XBRL)  → 주석 구조 + 계정ID + 한국어 라벨 (LLM 불필요)
  Track B(HTML)  → Track C에서 못 잡는 예외 케이스만 (fallback)
```

**Track C로 LLM 없이 처리 가능한 항목:**
- 판관비 상세 (dart_ 표준 계정)
- 유형자산/무형자산 변동
- 재고자산 내역
- 현금흐름 조정항목
- 종업원급여 내역
- 주당이익

**여전히 Track B(HTML + LLM)가 필요한 항목:**
- 회사 고유 확장 계정(entity_)의 해석
- TextBlock(서술형 주석)에 포함된 비정형 데이터
- XBRL에 태깅되지 않은 주석 내용

### 6-0-1. SK하이닉스 XBRL 비교 분석으로 발견된 추가 사항

> **⚠️ SK하이닉스(entity00164779) vs SNT다이내믹스(entity00134477) 비교 (2025.02)**

**1. role 세트가 회사마다 다르다 — 동적 파싱 필수**

두 회사의 pre.xml을 비교하면:
- 공통 role: 53개 (핵심 재무제표 + 주요 주석)
- SK에만 있는 role: 15개 (주식기준보상 D834120, 차입금상세 D822400, 사용제한자산 D822470 등)
- SNT에만 있는 role: 16개 (투자부동산 D825100, 기타포괄손익 D861000 등)

→ **NOTE_ROLES를 하드코딩하되, pre.xml에 존재하는 role만 동적으로 파싱해야 한다.**
→ `U800xxx` (entity 고유 role)은 회사마다 개수와 내용이 완전히 다름.

**2. 판관비 dart_ 계정은 표준이지만, 사용 세트가 다르다**

| 구분 | SK하이닉스 | SNT다이내믹스 |
|------|:---:|:---:|
| 공통 판관비 계정 | 8개 | 8개 |
| SK에만 있는 항목 | 무형자산상각비, 판매촉진비 | — |
| SNT에만 있는 항목 | — | A/S비용, 통신비, 보상비, 전산비, 운반비 등 16개 |
| **총 판관비 계정** | **10개** | **26개** |

→ dart_ 계정 ID는 표준이라 매핑 자체는 LLM 불필요. 단, **회사별로 쓰는 항목 세트가 다르므로 템플릿에 모든 가능한 항목을 미리 나열하고, 데이터 있는 것만 채우는 방식**이 필요.

**3. SK하이닉스에서 발견된 D834300 (비용의 성격별 분류) — 대형 제조업 핵심**

SNT에는 없고 SK에만 있는 role. 제조원가+판관비를 성격별로 합산한 데이터:

```
ifrs-full_ExpenseByNature (비용의 성격별 분류 합계)
  + ifrs-full_ChangesInInventoriesOfFinishedGoodsAndWorkInProgress (재공품 변동)
  + ifrs-full_RawMaterialsAndConsumablesUsed    (원재료 사용)
  + ifrs-full_EmployeeBenefitsExpense           (종업원급여)
  + ifrs-full_DepreciationAndAmortisationExpense (감가상각비)
  + dart_Commissions                            (지급수수료)
  + dart_UtilityExpenses                        (수도광열비)
  + dart_RepairExpenses                         (수선비)
  + dart_OutsourcingExpenses                    (외주용역비)
  + ifrs-full_OtherExpenseByNature              (기타비용)
```

→ 반도체처럼 제조원가가 핵심인 기업 분석에 필수. D834310(판관비 상세)와 별개로 확보해야 함.

**4. 현금흐름 조정항목 — 공통 코어 + 회사별 확장**

```
공통 코어 (13개) — 어느 회사든 반드시 있음:
  감가상각비, 무형자산상각비, 퇴직급여, 법인세비용,
  이자수익, 외환차손/차익, 유형자산처분손익,
  매출채권/재고/매입채무 변동, 퇴직금 지급 등

SK 전용 (15개): 손상차손/환입, 금융자산처분, 파생상품 등
SNT 전용 (28개): 투자부동산감가, 사용권자산감가, 충당부채 변동 등
```

→ **현금흐름 조정은 "공통 코어 + 회사별 가변 항목" 구조로 설계해야 한다.**

**5. cal.xml = 자동 검증의 정답지**

cal.xml의 계산관계는 `weight` 속성으로 부호를 정확히 지정:

```python
# I/S 계산 체인 예시 (SK하이닉스 D431410):
GrossProfit        = + Revenue - CostOfSales
OperatingIncome    = + GrossProfit - TotalSGA
ProfitBeforeTax    = + OperatingIncome + FinanceIncome - FinanceCosts
                     + OtherGains - OtherLosses + ShareOfAssociates
ProfitLoss         = + ProfitBeforeTax - IncomeTaxExpense
ComprehensiveIncome = + ProfitLoss + OCI
```

이를 활용한 자동 검증 모듈:

```python
class CalValidation:
    """
    cal.xml의 계산관계를 이용한 자동 검증.
    
    Track A에서 가져온 금액이 cal.xml의 수식을 만족하는지 확인.
    불일치 시 경고 출력 → 데이터 오류 또는 API 응답 누락 탐지.
    """

    def __init__(self, cal_file: str):
        self.relations = self._parse_cal(cal_file)

    def _parse_cal(self, cal_file: str) -> dict[str, list[tuple[str, str, float]]]:
        """
        cal.xml 파싱 → {role_code: [(parent, child, weight), ...]}
        """
        tree = ET.parse(cal_file)
        root = tree.getroot()
        ns = {
            "link": "http://www.xbrl.org/2003/linkbase",
            "xlink": "http://www.w3.org/1999/xlink",
        }

        result = {}
        for clink in root.findall(".//link:calculationLink", ns):
            role = clink.get(f'{{{ns["xlink"]}}}role', "")
            role_code = role.split("role-")[-1] if "role-" in role else ""

            # loc 매핑
            loc_map = {}
            for loc in clink.findall("link:loc", ns):
                label = loc.get(f'{{{ns["xlink"]}}}label', "")
                href = loc.get(f'{{{ns["xlink"]}}}href', "")
                if "#" in href:
                    loc_map[label] = href.split("#")[-1]

            # arc에서 계산관계 추출
            rels = []
            for arc in clink.findall("link:calculationArc", ns):
                from_l = arc.get(f'{{{ns["xlink"]}}}from', "")
                to_l = arc.get(f'{{{ns["xlink"]}}}to', "")
                weight = float(arc.get("weight", "1"))

                parent = loc_map.get(from_l, "")
                child = loc_map.get(to_l, "")
                if parent and child:
                    rels.append((parent, child, weight))

            if rels:
                result[role_code] = rels

        return result

    def validate(
        self,
        role_code: str,
        values: dict[str, float],  # {account_id: 금액}
        tolerance: float = 1.0,     # 허용 오차 (단위: 백만원)
    ) -> list[dict]:
        """
        계산관계 검증. 불일치 항목 반환.

        예: B/S(D210000)에서 Assets = CurrentAssets + NoncurrentAssets 검증
        """
        if role_code not in self.relations:
            return []

        errors = []

        # parent별로 children 그룹핑
        from collections import defaultdict
        parent_children = defaultdict(list)
        for parent, child, weight in self.relations[role_code]:
            parent_children[parent].append((child, weight))

        for parent, children in parent_children.items():
            if parent not in values:
                continue

            # 자식 합계 계산
            calc_sum = sum(
                values.get(child, 0) * weight
                for child, weight in children
            )
            actual = values[parent]

            diff = abs(actual - calc_sum)
            if diff > tolerance:
                errors.append({
                    "parent": parent,
                    "expected": calc_sum,
                    "actual": actual,
                    "diff": diff,
                    "children": [
                        (c, w, values.get(c, 0)) for c, w in children
                    ],
                })

        return errors
```

**사용 예시:**

```python
validator = CalValidation("data/xbrl/entity00164779_cal.xml")

# Track A에서 가져온 값
bs_values = {
    "ifrs-full_Assets": 100_000_000,
    "ifrs-full_CurrentAssets": 40_000_000,
    "ifrs-full_NoncurrentAssets": 60_000_000,
    # ...
}

errors = validator.validate("D210000", bs_values)
if errors:
    for e in errors:
        print(f"⚠️ {e['parent']}: 계산값={e['expected']:,.0f} vs 실제={e['actual']:,.0f}")
else:
    print("✅ B/S 계산관계 검증 통과")
```

**6. def.xml = 다차원 테이블 구조의 정답지**

def.xml은 주석 테이블의 축(Axis)과 구성원(Member) 관계를 정의:

```
[SK하이닉스 수익분해 D831150]

테이블 1: 제품별 매출
  축: SegmentsAxis
    ├── DRAM          (entity 확장)
    ├── NAND Flash    (entity 확장)
    └── 기타           (entity 확장)
  값: Revenue

테이블 2: 지역별 매출
  축: GeographicalAreasAxis
    ├── 국내           (CountryOfDomicileMember, IFRS 표준)
    └── 해외           (ForeignCountriesMember)
         ├── 미국      (dart_USMember, DART 표준)
         ├── 중국      (dart_CNMember, DART 표준)
         ├── 아시아기타  (entity 확장)
         └── 유럽      (entity 확장)
  값: Revenue

테이블 3: 이행시점별 매출
  축: PerformanceObligationsAxis
    ├── 한 시점에 이행  (IFRS 표준)
    └── 기간에 걸쳐 이행 (IFRS 표준)
  값: Revenue
```

→ **def.xml을 파싱하면 HTML 테이블 파싱 없이도 주석 테이블의 행/열 구조를 정확히 알 수 있다.**
→ 단, def.xml에는 '구조'만 있고 '값(금액)'은 없으므로, 인스턴스 문서(.xbrl)나 API에서 값을 가져와야 함.

```python
class XBRLDefParser:
    """def.xml에서 테이블의 다차원 구조를 추출"""

    def parse_table_structure(
        self, def_file: str, role_code: str,
    ) -> dict:
        """
        특정 role의 테이블 구조 추출.
        
        Returns:
            {
                "axes": [
                    {"axis_id": "SegmentsAxis", "members": [
                        {"id": "DramMember", "label": "DRAM", "source": "company"},
                        ...
                    ]},
                ],
                "line_items": ["Revenue", "OperatingIncomeLoss", ...],
            }
        """
        tree = ET.parse(def_file)
        root = tree.getroot()
        ns = {
            "link": "http://www.xbrl.org/2003/linkbase",
            "xlink": "http://www.w3.org/1999/xlink",
        }

        for dlink in root.findall(".//link:definitionLink", ns):
            role = dlink.get(f'{{{ns["xlink"]}}}role', "")
            if role_code not in role:
                continue

            loc_map = {}
            for loc in dlink.findall("link:loc", ns):
                label = loc.get(f'{{{ns["xlink"]}}}label', "")
                href = loc.get(f'{{{ns["xlink"]}}}href', "")
                if "#" in href:
                    loc_map[label] = href.split("#")[-1]

            # dimension → member 관계 추출
            axes = {}
            line_items = []

            for arc in dlink.findall("link:definitionArc", ns):
                arcrole = arc.get(f'{{{ns["xlink"]}}}arcrole', "")
                from_acc = loc_map.get(arc.get(f'{{{ns["xlink"]}}}from', ""), "")
                to_acc = loc_map.get(arc.get(f'{{{ns["xlink"]}}}to', ""), "")

                rel = arcrole.split("/")[-1]

                if rel == "domain-member" and "Member" in to_acc:
                    # 어느 축의 member인지 찾기
                    if from_acc.endswith("Member") or from_acc.endswith("Domain"):
                        # parent member에 추가
                        for axis_id, axis_data in axes.items():
                            if from_acc in [m["id"] for m in axis_data] or from_acc == axis_data[0].get("domain"):
                                axes[axis_id].append({"id": to_acc})

                if rel == "hypercube-dimension" and "Axis" in to_acc:
                    axes[to_acc] = []

                if rel == "dimension-domain":
                    if to_acc in axes:
                        pass  # domain 설정
                    for axis_id in axes:
                        if from_acc == axis_id:
                            axes[axis_id] = [{"domain": to_acc}]

            return {"axes": axes, "line_items": line_items}

        return {}
```

### 6-0-2. 회사간 비교에서 도출된 설계 원칙

| 원칙 | 근거 |
|------|------|
| **role은 동적 탐색** | SK 68개 vs SNT 69개, 15~16개 차이 |
| **dart_ 계정은 신뢰하되, 세트는 가변** | 판관비: SK 10개 vs SNT 26개 |
| **entity_ 계정은 항상 LLM 또는 label 참조** | SK 1,144개 vs SNT 716개, 완전히 다름 |
| **cal.xml은 검증에, def.xml은 테이블 구조에** | 값이 아닌 구조/관계 정보 |
| **D834300 비용성격별분류는 제조업 필수** | SK에만 있음, 원가구조 분석 핵심 |
| **현금흐름은 코어13 + 가변** | 공통 조정항목 위주로 템플릿 설계 |

### 6-1. Track A: 분기 보고서의 누적/분기 금액 혼재

반기·분기 보고서의 I/S, C/F는 누적 금액(`thstrm_amount`)과 해당 분기 금액(`thstrm_add_amount`)이 분리된다. 연간 모델이 아닌 분기 모델을 만들 때 주의가 필요하다.

```
대응: report_code별로 누적 vs 분기 금액을 구분하는 로직
→ 사업보고서(11011)는 연간이라 문제 없음
→ 분기 보고서는 전분기 누적을 빼서 해당 분기 금액 산출
```

### 6-2. Track A: 회사별 추가 계정

표준 taxonomy 외에 회사가 자체 추가한 계정(`dart_xxx`)이 있을 수 있다. 예를 들어 `dart_OperatingIncomeLoss`(영업이익)는 K-IFRS 표준이 아닌 한국 DART 확장 계정이다.

```
대응: 템플릿에 주요 dart_ 확장 계정도 미리 포함
→ ifrs-full_ + dart_ 계정을 모두 커버
```

### 6-3. Track B: 테이블 파싱 실패

일부 회사는 주석을 표 대신 서술형으로 작성한다.

```
대응: LLM에게 원문 텍스트를 주고 구조화된 데이터 추출 요청
→ "다음 텍스트에서 판관비 항목별 금액을 JSON으로 추출해줘"
```

### 6-4. Track B: 병합셀/다중 헤더

DART HTML 테이블은 `colspan`, `rowspan`이 복잡하게 사용된다.

```
대응: pd.read_html() 실패 시 BeautifulSoup으로 직접 파싱하는 fallback 로직
→ 셀 단위로 순회하며 병합 범위를 추적
```

### 6-5. 연결/별도 재무제표 구분

같은 공시 안에 연결재무제표와 별도재무제표가 모두 포함된다.

```
대응: HTML 본문에서 "연결" 키워드 기준으로 섹션 분리
→ 기본값은 연결재무제표 우선
```

### 6-6. Track B: 단위 불일치

어떤 회사는 백만원, 어떤 회사는 원 단위로 기재한다.

```
대응: 테이블 상단/하단의 "(단위: 백만원)" 같은 텍스트를 파싱하여 단위 승수 적용
```

---

## 7. 확장 로드맵

**Phase 1 — MVP (현재)**
- 단일 회사, 3대 재무제표(Track A) + 판관비/부문별매출 2개 주석(Track B)
- 수동으로 corp_code 입력
- 3개년 시계열

**Phase 2 — 멀티컴퍼니**
- 여러 회사를 batch로 처리
- 캐시 히트율 모니터링 대시보드

**Phase 3 — 시계열 자동화**
- 최근 3~5년치 자동 수집
- 전기/당기 비교 검증 로직 (숫자 불일치 경고)

**Phase 4 — 풀 재무모델 연동**
- DCF, 멀티플 밸류에이션 시트 자동 업데이트
- 주석 데이터 → 가정(assumption) 시트 자동 반영

---

## 8. 필요 패키지

```txt
# requirements.txt
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
pandas>=2.0.0
openpyxl>=3.1.0
pyyaml>=6.0
openai>=1.0.0        # 또는 anthropic SDK
```
