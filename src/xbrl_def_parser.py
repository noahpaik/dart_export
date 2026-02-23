"""def.xml parser module."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from pathlib import Path


class XBRLDefParserError(RuntimeError):
    """Raised when def.xml parsing fails."""


class XBRLDefParser:
    """
    Parse `*_def.xml` to extract table dimensions (Axis/Member structure).
    """

    NS = {
        "link": "http://www.xbrl.org/2003/linkbase",
        "xlink": "http://www.w3.org/1999/xlink",
    }
    XLINK_ROLE = "{http://www.w3.org/1999/xlink}role"
    XLINK_LABEL = "{http://www.w3.org/1999/xlink}label"
    XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
    XLINK_FROM = "{http://www.w3.org/1999/xlink}from"
    XLINK_TO = "{http://www.w3.org/1999/xlink}to"
    XLINK_ARCROLE = "{http://www.w3.org/1999/xlink}arcrole"

    def __init__(self, xbrl_dir: str, def_file: str | None = None):
        self.dir = Path(xbrl_dir)
        if not self.dir.exists() or not self.dir.is_dir():
            raise XBRLDefParserError(f"유효한 XBRL 디렉토리가 아닙니다: {self.dir}")

        if def_file:
            self.def_file = Path(def_file)
            if not self.def_file.exists() or not self.def_file.is_file():
                raise XBRLDefParserError(f"유효한 def.xml 파일이 아닙니다: {self.def_file}")
        else:
            self.def_file = self._find_file("*_def.xml", "def.xml")
        self.labels_ko = self._parse_label_file(self._find_optional_label("ko"))
        self.labels_en = self._parse_label_file(self._find_optional_label("en"))

    def _find_file(self, pattern: str, display_name: str) -> Path:
        candidates = sorted(self.dir.glob(pattern))
        if not candidates:
            raise XBRLDefParserError(f"{display_name} 파일을 찾을 수 없습니다: {self.dir}")
        return candidates[0]

    def _find_optional_label(self, lang: str) -> Path | None:
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
            raise XBRLDefParserError(f"라벨 XML 파싱 실패 ({path.name}): {exc}") from exc

        root = tree.getroot()
        labels: dict[str, str] = {}
        for link in root.findall(".//link:labelLink", self.NS):
            loc_map: dict[str, str] = {}
            for loc in link.findall("link:loc", self.NS):
                loc_label = (loc.get(self.XLINK_LABEL) or "").strip()
                href = (loc.get(self.XLINK_HREF) or "").strip()
                account_id = self._extract_account_id(href)
                if loc_label and account_id:
                    loc_map[loc_label] = account_id

            res_map: dict[str, str] = {}
            for label in link.findall("link:label", self.NS):
                label_id = (label.get(self.XLINK_LABEL) or "").strip()
                role = (label.get(self.XLINK_ROLE) or "").strip()
                text = (label.text or "").strip()
                if not label_id or not text:
                    continue
                if role and role != "http://www.xbrl.org/2003/role/label":
                    continue
                res_map[label_id] = text

            for arc in link.findall("link:labelArc", self.NS):
                from_label = (arc.get(self.XLINK_FROM) or "").strip()
                to_label = (arc.get(self.XLINK_TO) or "").strip()
                account_id = loc_map.get(from_label)
                label_text = res_map.get(to_label)
                if account_id and label_text and account_id not in labels:
                    labels[account_id] = label_text

        return labels

    @staticmethod
    def _extract_account_id(href: str) -> str:
        if "#" not in href:
            return ""
        return href.split("#", 1)[1].strip()

    @staticmethod
    def _extract_role_code(role_uri: str) -> str | None:
        match = re.search(r"([DU]\d{6})", role_uri or "")
        if not match:
            return None
        return match.group(1)

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

    def list_roles(self) -> list[str]:
        try:
            tree = ET.parse(self.def_file)
        except ET.ParseError as exc:
            raise XBRLDefParserError(f"def.xml 파싱 실패 ({self.def_file.name}): {exc}") from exc

        roles = set()
        root = tree.getroot()
        for link in root.findall(".//link:definitionLink", self.NS):
            role_uri = (link.get(self.XLINK_ROLE) or "").strip()
            role_code = self._extract_role_code(role_uri)
            if role_code:
                roles.add(role_code)
        return sorted(roles)

    def parse_table_structure(self, role_code: str) -> dict:
        role_code_n = (role_code or "").strip()
        if not role_code_n:
            raise XBRLDefParserError("role_code가 비어 있습니다.")

        try:
            tree = ET.parse(self.def_file)
        except ET.ParseError as exc:
            raise XBRLDefParserError(f"def.xml 파싱 실패 ({self.def_file.name}): {exc}") from exc

        root = tree.getroot()
        target_link = None

        for dlink in root.findall(".//link:definitionLink", self.NS):
            role_uri = (dlink.get(self.XLINK_ROLE) or "").strip()
            current_code = self._extract_role_code(role_uri)
            if current_code == role_code_n:
                target_link = dlink
                break

        if target_link is None:
            return {"role_code": role_code_n, "axes": [], "line_items": []}

        loc_map: dict[str, tuple[str, str]] = {}
        for loc in target_link.findall("link:loc", self.NS):
            loc_label = (loc.get(self.XLINK_LABEL) or "").strip()
            href = (loc.get(self.XLINK_HREF) or "").strip()
            account_id = self._extract_account_id(href)
            if loc_label and account_id:
                loc_map[loc_label] = (account_id, href)

        axes: set[str] = set()
        axis_domain: dict[str, str] = {}
        domain_member_edges: dict[str, set[str]] = defaultdict(set)
        line_items: set[str] = set()

        for arc in target_link.findall("link:definitionArc", self.NS):
            arcrole = (arc.get(self.XLINK_ARCROLE) or "").strip()
            relation = arcrole.split("/")[-1] if arcrole else ""

            from_ref = (arc.get(self.XLINK_FROM) or "").strip()
            to_ref = (arc.get(self.XLINK_TO) or "").strip()
            from_data = loc_map.get(from_ref)
            to_data = loc_map.get(to_ref)
            if not from_data or not to_data:
                continue

            from_id = from_data[0]
            to_id = to_data[0]

            if relation == "hypercube-dimension":
                axes.add(to_id)
            elif relation == "dimension-domain":
                axis_domain[from_id] = to_id
            elif relation == "domain-member":
                domain_member_edges[from_id].add(to_id)

            if from_id.endswith("LineItems"):
                line_items.add(from_id)
            if to_id.endswith("LineItems"):
                line_items.add(to_id)

        axes_payload = []
        for axis_id in sorted(axes):
            domain_id = axis_domain.get(axis_id, "")
            members = self._collect_members(domain_id, domain_member_edges, loc_map)
            axes_payload.append(
                {
                    "axis_id": axis_id,
                    "axis_label_ko": self.labels_ko.get(axis_id, ""),
                    "axis_label_en": self.labels_en.get(axis_id, ""),
                    "axis_source": self._detect_source(axis_id, self._href_for_id(axis_id, loc_map)),
                    "domain_id": domain_id,
                    "domain_label_ko": self.labels_ko.get(domain_id, ""),
                    "domain_label_en": self.labels_en.get(domain_id, ""),
                    "members": members,
                }
            )

        line_items_payload = []
        for account_id in sorted(line_items):
            line_items_payload.append(
                {
                    "account_id": account_id,
                    "label_ko": self.labels_ko.get(account_id, ""),
                    "label_en": self.labels_en.get(account_id, ""),
                    "source": self._detect_source(account_id, self._href_for_id(account_id, loc_map)),
                }
            )

        return {
            "role_code": role_code_n,
            "axes": axes_payload,
            "line_items": line_items_payload,
        }

    @staticmethod
    def _href_for_id(account_id: str, loc_map: dict[str, tuple[str, str]]) -> str:
        for _, (aid, href) in loc_map.items():
            if aid == account_id:
                return href
        return ""

    def _collect_members(
        self,
        domain_id: str,
        edges: dict[str, set[str]],
        loc_map: dict[str, tuple[str, str]],
    ) -> list[dict[str, str]]:
        if not domain_id:
            return []

        queue = deque([domain_id])
        visited = {domain_id}
        members = []

        while queue:
            current = queue.popleft()
            for nxt in sorted(edges.get(current, set())):
                if nxt in visited:
                    continue
                visited.add(nxt)
                queue.append(nxt)

                if not nxt.endswith("Member"):
                    continue
                members.append(
                    {
                        "id": nxt,
                        "label_ko": self.labels_ko.get(nxt, ""),
                        "label_en": self.labels_en.get(nxt, ""),
                        "source": self._detect_source(nxt, self._href_for_id(nxt, loc_map)),
                    }
                )

        return members
