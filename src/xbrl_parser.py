"""XBRL parser module (Track C)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class XBRLParserError(RuntimeError):
    """Raised when XBRL parsing fails."""


@dataclass
class XBRLNote:
    role_code: str
    role_name: str
    accounts: list[dict[str, str]]
    members: list[dict[str, str]]
    fs_type: str | None = None


class XBRLNoteParser:
    """
    Parse XBRL pre/lab files to extract note structures.

    - pre.xml: role/account structure
    - lab-ko.xml / lab-en.xml: account labels
    """

    NS = {
        "link": "http://www.xbrl.org/2003/linkbase",
        "xlink": "http://www.w3.org/1999/xlink",
    }
    XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
    XLINK_LABEL = "{http://www.w3.org/1999/xlink}label"
    XLINK_FROM = "{http://www.w3.org/1999/xlink}from"
    XLINK_TO = "{http://www.w3.org/1999/xlink}to"
    XLINK_ROLE = "{http://www.w3.org/1999/xlink}role"

    DEFAULT_ROLE_MAP = {
        "D210000": "재무상태표",
        "D431410": "포괄손익계산서",
        "D520000": "현금흐름표",
        "D610000": "자본변동표",
        "D822100": "유형자산",
        "D823180": "무형자산",
        "D825100": "투자부동산",
        "D826380": "재고자산",
        "D827570": "충당부채",
        "D831150": "수익분해",
        "D832610": "리스",
        "D834120": "주식기준보상",
        "D834300": "비용성격별분류",
        "D834310": "판관비상세",
        "D834320": "판관비상세2",
        "D834330": "판관비상세3",
        "D834480": "종업원급여",
        "D835110": "법인세",
        "D838000": "주당이익",
        "D851100": "현금흐름조정",
        "D861200": "자본상세",
        "D861300": "이익잉여금",
        "D871100": "영업부문",
        "D818000": "특수관계자",
        "D822300": "금융자산상세",
        "D822310": "금융부채상세",
        "D822380": "금융상품위험",
        "D822390": "금융자산범주",
        "D822400": "차입금상세",
        "D822420": "공정가치측정",
        "D822430": "공정가치서열",
        "D822470": "사용제한금융자산",
        "D822490": "기타지급채무",
    }

    SGA_ID_KEYWORDS = (
        "SellingGeneralAdministrativeExpenses",
        "SalariesWages",
        "ProvisionForSeveranceIndemnities",
        "EmployeeBenefits",
        "BadDebtExpenses",
        "Commissions",
        "AdvertisingExpenses",
        "FreightExpenses",
        "TaxesDues",
        "TravelExpenses",
        "TrainingExpenses",
        "RentalExpenses",
        "InsurancePremiums",
        "OrdinaryDevelopmentExpense",
        "EntertainmentExpenses",
        "MiscellaneousExpenses",
    )

    def __init__(
        self,
        xbrl_dir: str,
        include_separate: bool = False,
        include_unknown_roles: bool = False,
        role_map: dict[str, str] | None = None,
    ):
        self.dir = Path(xbrl_dir)
        self.include_separate = include_separate
        self.include_unknown_roles = include_unknown_roles
        self.role_map = dict(self.DEFAULT_ROLE_MAP)
        if role_map:
            self.role_map.update(role_map)

        self.labels_ko: dict[str, str] = {}
        self.labels_en: dict[str, str] = {}
        self._notes_cache: list[XBRLNote] | None = None

        if not self.dir.exists() or not self.dir.is_dir():
            raise XBRLParserError(f"유효한 XBRL 디렉토리가 아닙니다: {self.dir}")

    def parse(self, force_refresh: bool = False) -> list[XBRLNote]:
        if self._notes_cache is not None and not force_refresh:
            return self._notes_cache

        self._load_labels()
        notes = self._parse_presentation()
        self._notes_cache = notes
        return notes

    def _load_labels(self) -> None:
        self.labels_ko = self._parse_label_file(self._find_label_file("ko"))
        self.labels_en = self._parse_label_file(self._find_label_file("en"))

    def _find_label_file(self, lang: str) -> Path | None:
        candidates = sorted(self.dir.glob(f"*_lab-{lang}.xml"))
        if candidates:
            return candidates[0]
        return None

    def _parse_label_file(self, path: Path | None) -> dict[str, str]:
        if path is None:
            return {}

        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise XBRLParserError(f"라벨 XML 파싱 실패 ({path.name}): {exc}") from exc

        root = tree.getroot()
        labels: dict[str, str] = {}

        for label_link in root.findall(".//link:labelLink", self.NS):
            loc_map: dict[str, str] = {}
            for loc in label_link.findall("link:loc", self.NS):
                loc_label = (loc.get(self.XLINK_LABEL) or "").strip()
                href = (loc.get(self.XLINK_HREF) or "").strip()
                account_id = self._extract_account_id(href)
                if loc_label and account_id:
                    loc_map[loc_label] = account_id

            resource_map: dict[str, str] = {}
            for label in label_link.findall("link:label", self.NS):
                resource_label = (label.get(self.XLINK_LABEL) or "").strip()
                role = (label.get(self.XLINK_ROLE) or "").strip()
                text = (label.text or "").strip()
                if not resource_label or not text:
                    continue
                if role and role != "http://www.xbrl.org/2003/role/label":
                    continue
                resource_map[resource_label] = text

            for arc in label_link.findall("link:labelArc", self.NS):
                from_label = (arc.get(self.XLINK_FROM) or "").strip()
                to_label = (arc.get(self.XLINK_TO) or "").strip()
                account_id = loc_map.get(from_label)
                text = resource_map.get(to_label)
                if account_id and text and account_id not in labels:
                    labels[account_id] = text

        return labels

    def _parse_presentation(self) -> list[XBRLNote]:
        pre_file = self._find_pre_file()
        try:
            tree = ET.parse(pre_file)
        except ET.ParseError as exc:
            raise XBRLParserError(f"pre.xml 파싱 실패 ({pre_file.name}): {exc}") from exc

        root = tree.getroot()
        notes: list[XBRLNote] = []

        for presentation_link in root.findall(".//link:presentationLink", self.NS):
            role_uri = (presentation_link.get(self.XLINK_ROLE) or "").strip()
            role_code = self._extract_role_code(role_uri)
            if role_code is None:
                continue

            role_name, fs_type, known = self._resolve_role(role_code)
            if fs_type == "separate" and not self.include_separate:
                continue
            if not known and not self.include_unknown_roles:
                continue

            loc_entries: list[tuple[str, str]] = []
            seen = set()
            for loc in presentation_link.findall("link:loc", self.NS):
                href = (loc.get(self.XLINK_HREF) or "").strip()
                account_id = self._extract_account_id(href)
                if not account_id:
                    continue
                if account_id in seen:
                    continue
                seen.add(account_id)
                loc_entries.append((account_id, href))

            accounts: list[dict[str, str]] = []
            members: list[dict[str, str]] = []
            for account_id, href in loc_entries:
                source = self._detect_source(account_id, href)
                if self._is_structural_account(account_id):
                    continue
                payload = {
                    "account_id": account_id,
                    "label_ko": self.labels_ko.get(account_id, ""),
                    "label_en": self.labels_en.get(account_id, ""),
                    "source": source,
                }
                if account_id.endswith("Member"):
                    members.append(payload)
                else:
                    accounts.append(payload)

            notes.append(
                XBRLNote(
                    role_code=role_code,
                    role_name=role_name,
                    fs_type=fs_type,
                    accounts=accounts,
                    members=members,
                )
            )

        notes.sort(key=lambda item: item.role_code)
        return notes

    def _find_pre_file(self) -> Path:
        candidates = sorted(self.dir.glob("*_pre.xml"))
        if not candidates:
            raise XBRLParserError(f"pre.xml 파일을 찾을 수 없습니다: {self.dir}")
        return candidates[0]

    @staticmethod
    def _extract_account_id(href: str) -> str:
        if not href:
            return ""
        if "#" in href:
            return href.split("#", 1)[1].strip()
        return ""

    @staticmethod
    def _extract_role_code(role_uri: str) -> str | None:
        if not role_uri:
            return None
        match = re.search(r"([DU]\d{6})", role_uri)
        if not match:
            return None
        return match.group(1)

    def _resolve_role(self, role_code: str) -> tuple[str, str | None, bool]:
        if role_code in self.role_map:
            return self.role_map[role_code], "consolidated", True

        if role_code.endswith("5"):
            base_code = f"{role_code[:-1]}0"
            if base_code in self.role_map:
                return f"{self.role_map[base_code]}(별도)", "separate", True
            return f"Unknown({role_code})", "separate", False

        return f"Unknown({role_code})", "consolidated", False

    @staticmethod
    def _is_structural_account(account_id: str) -> bool:
        structural_keywords = ("Abstract", "Table", "LineItems", "Axis", "Domain")
        return any(keyword in account_id for keyword in structural_keywords)

    @staticmethod
    def _detect_source(account_id: str, href: str) -> str:
        aid = account_id.lower()
        href_l = href.lower()
        if aid.startswith("entity_") or "entity" in href_l:
            return "company"
        if aid.startswith("dart_"):
            return "dart"
        if aid.startswith("ifrs") or aid.startswith("ias_"):
            return "ifrs"
        return "unknown"

    def get_sga_accounts(self) -> dict[str, str]:
        """
        판관비 관련 dart_ 계정을 추출한다.
        """
        notes = self.parse()
        result: dict[str, str] = {}

        for note in notes:
            if not any(keyword in note.role_name for keyword in ("판관비", "비용성격")):
                continue
            for account in note.accounts:
                account_id = account["account_id"]
                if account["source"] != "dart":
                    continue
                if any(keyword in account_id for keyword in self.SGA_ID_KEYWORDS):
                    result[account_id] = account.get("label_ko", "")

        if result:
            return dict(sorted(result.items()))

        for account_id, label in sorted(self.labels_ko.items()):
            if not account_id.startswith("dart_"):
                continue
            if any(keyword in account_id for keyword in self.SGA_ID_KEYWORDS):
                result[account_id] = label
        return result

    def get_segment_members(self) -> list[dict[str, str]]:
        """
        영업부문 role의 회사 고유 Member 목록을 반환한다.
        """
        notes = self.parse()
        segment_candidates = [
            note
            for note in notes
            if note.role_code in {"D871100", "D871105"} or "영업부문" in note.role_name
        ]
        if not segment_candidates:
            return []

        members = []
        for member in segment_candidates[0].members:
            if member.get("source") == "company":
                members.append(member)
        return members

    @staticmethod
    def detect_xbrl_dir_from_files(files: list[Path]) -> Path | None:
        """
        Step1 download 결과 파일 목록에서 XBRL 디렉토리를 추정한다.
        """
        if not files:
            return None
        candidate_dirs = {path.parent for path in files if path.suffix.lower() in {".xml", ".xbrl"}}
        for directory in sorted(candidate_dirs):
            if list(directory.glob("*_pre.xml")):
                return directory
        return None
