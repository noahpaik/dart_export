"""cal.xml validation module."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CalValidationError(RuntimeError):
    """Raised when cal.xml parsing or validation fails."""


@dataclass(frozen=True)
class CalculationRelation:
    role_code: str
    parent: str
    child: str
    weight: float
    order: float | None = None


class CalValidation:
    """
    Parse `*_cal.xml` and validate parent-child arithmetic.

    Example:
      OperatingIncome = GrossProfit - SGA
      -> parent=OperatingIncome, children=[(GrossProfit,+1), (SGA,-1)]
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

    def __init__(self, cal_file: str):
        self.cal_file = Path(cal_file)
        if not self.cal_file.exists() or not self.cal_file.is_file():
            raise CalValidationError(f"유효한 cal.xml 파일이 아닙니다: {self.cal_file}")
        self.relations = self._parse_cal(self.cal_file)

    def _parse_cal(self, cal_file: Path) -> dict[str, list[CalculationRelation]]:
        try:
            tree = ET.parse(cal_file)
        except ET.ParseError as exc:
            raise CalValidationError(f"cal.xml 파싱 실패 ({cal_file.name}): {exc}") from exc

        root = tree.getroot()
        role_relations: dict[str, list[CalculationRelation]] = defaultdict(list)

        for calc_link in root.findall(".//link:calculationLink", self.NS):
            role_uri = (calc_link.get(self.XLINK_ROLE) or "").strip()
            role_code = self._extract_role_code(role_uri)
            if role_code is None:
                continue

            loc_map: dict[str, str] = {}
            for loc in calc_link.findall("link:loc", self.NS):
                loc_label = (loc.get(self.XLINK_LABEL) or "").strip()
                href = (loc.get(self.XLINK_HREF) or "").strip()
                account_id = self._extract_account_id(href)
                if loc_label and account_id:
                    loc_map[loc_label] = account_id

            for arc in calc_link.findall("link:calculationArc", self.NS):
                from_label = (arc.get(self.XLINK_FROM) or "").strip()
                to_label = (arc.get(self.XLINK_TO) or "").strip()
                parent = loc_map.get(from_label, "")
                child = loc_map.get(to_label, "")
                if not parent or not child:
                    continue

                weight = self._to_float(arc.get("weight"), default=1.0)
                order = self._to_float(arc.get("order"), default=None)
                role_relations[role_code].append(
                    CalculationRelation(
                        role_code=role_code,
                        parent=parent,
                        child=child,
                        weight=weight if weight is not None else 1.0,
                        order=order,
                    )
                )

        return {
            role_code: sorted(
                relations,
                key=lambda rel: (
                    rel.parent,
                    rel.order if rel.order is not None else 10_000.0,
                    rel.child,
                ),
            )
            for role_code, relations in role_relations.items()
            if relations
        }

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
    def _to_float(value: Any, default: float | None = None) -> float | None:
        if value is None:
            return default
        text = str(value).strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default

    @staticmethod
    def _normalize_value(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if text in {"", "None", "nan", "NaN", "-"}:
            return None

        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]

        cleaned = (
            text.replace(",", "")
            .replace(" ", "")
            .replace("\u00A0", "")
            .replace("원", "")
            .replace("백만원", "")
        )
        try:
            number = float(cleaned)
        except ValueError:
            return None
        return -number if negative else number

    def available_roles(self) -> list[str]:
        return sorted(self.relations.keys())

    def get_relations(self, role_code: str) -> list[CalculationRelation]:
        return list(self.relations.get(role_code, []))

    def validate(
        self,
        role_code: str,
        values: dict[str, Any],
        tolerance: float = 1.0,
        require_all_children: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Validate one role's calculation graph.

        Args:
          role_code: e.g. D210000
          values: {account_id: numeric_value}
          tolerance: absolute allowed difference
          require_all_children: if True, skip validation for parent with missing child values
        """
        role_code_n = (role_code or "").strip()
        if role_code_n not in self.relations:
            return []

        normalized_values: dict[str, float] = {}
        for account_id, raw_value in values.items():
            converted = self._normalize_value(raw_value)
            if converted is None:
                continue
            normalized_values[str(account_id)] = converted

        parent_children: dict[str, list[CalculationRelation]] = defaultdict(list)
        for relation in self.relations[role_code_n]:
            parent_children[relation.parent].append(relation)

        errors: list[dict[str, Any]] = []
        for parent, children in parent_children.items():
            actual = normalized_values.get(parent)
            if actual is None:
                continue

            expected = 0.0
            breakdown: list[dict[str, Any]] = []
            missing_children: list[str] = []

            for relation in children:
                child_value = normalized_values.get(relation.child)
                if child_value is None:
                    missing_children.append(relation.child)
                    breakdown.append(
                        {
                            "child": relation.child,
                            "weight": relation.weight,
                            "value": None,
                            "contribution": None,
                        }
                    )
                    continue
                contribution = child_value * relation.weight
                expected += contribution
                breakdown.append(
                    {
                        "child": relation.child,
                        "weight": relation.weight,
                        "value": child_value,
                        "contribution": contribution,
                    }
                )

            if require_all_children and missing_children:
                continue
            if not breakdown:
                continue

            diff = actual - expected
            if abs(diff) > float(tolerance):
                errors.append(
                    {
                        "role_code": role_code_n,
                        "parent": parent,
                        "actual": actual,
                        "expected": expected,
                        "diff": diff,
                        "abs_diff": abs(diff),
                        "missing_children": missing_children,
                        "breakdown": breakdown,
                    }
                )
        return errors
