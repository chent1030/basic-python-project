from __future__ import annotations

import html
import math
from collections import Counter
from typing import Any


def cosine(first: list[float], second: list[float]) -> float:
    if not first or len(first) != len(second):
        return 0.0
    first_scale = max(abs(value) for value in first)
    second_scale = max(abs(value) for value in second)
    if not first_scale or not second_scale:
        return 0.0
    first = [value / first_scale for value in first]
    second = [value / second_scale for value in second]
    norm = math.sqrt(sum(value * value for value in first) * sum(value * value for value in second))
    return (
        sum(left * right for left, right in zip(first, second, strict=True)) / norm if norm else 0.0
    )


def render_report(report: dict[str, Any], *, confirmed: bool, inspection_id: str) -> str:
    def escape(value: Any) -> str:
        return html.escape(str(value), quote=True)

    sections = "".join(
        f"<section><h2>{escape(section['title'])}</h2>"
        f"<p>{escape(section['content'])}</p>"
        f"<small>{escape(section['claim_type'])} · "
        f"{escape(', '.join(section['source_refs']))}</small></section>"
        for section in report["sections"]
    )
    reviews = "".join(f"<li>{escape(item)}</li>" for item in report["review_items"])
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(report['title'])}</title>"
        "<style>body{font:16px/1.7 sans-serif;max-width:900px;margin:40px auto;"
        "padding:24px;color:#17252a}section{border-top:1px solid #ddd;margin-top:24px}"
        "p{white-space:pre-wrap}small{color:#53646b}@media print{body{margin:0}}</style>"
        f"</head><body><header><small>CPS · {escape(inspection_id)} · "
        f"{'人工确认版本' if confirmed else '待审核草稿'}</small>"
        f"<h1>{escape(report['title'])}</h1><p>{escape(report['summary'])}</p>"
        f"<p>风险：{escape(report['risk'])}</p></header>{sections}"
        f"<section><h2>复核事项</h2><ul>{reviews}</ul></section></body></html>"
    )


def calculate_history(records: list[dict[str, Any]], since: float, until: float) -> dict[str, Any]:
    selected = sorted(
        (record for record in records if since <= record["archived_at"] <= until),
        key=lambda record: record["archived_at"],
    )
    categories: Counter = Counter()
    areas: Counter = Counter()
    supervisors: Counter = Counter()
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    repeated = 0
    measures: dict[str, dict[str, int]] = {}
    total = 0
    for record in selected:
        line = record["line_info"]
        case_keys = {}
        for issue in record["results"].get("issues", []):
            categories[issue["category"]] += 1
            areas[line["area"]] += 1
            supervisors[line["supervisor_id"]] += 1
            recurrence_key = (line["line_id"], issue["category"], issue.get("location") or "")
            total += 1
            is_repeat = recurrence_key in seen
            repeated += int(is_repeat)
            action = issue["suggested_action"]
            measure = measures.setdefault(
                action,
                {
                    "occurrences": 0,
                    "repeat_occurrences": 0,
                    "verified_pass": 0,
                    "verified_failure": 0,
                    "unknown": 0,
                    "reappeared_after_pass": 0,
                },
            )
            measure["occurrences"] += 1
            measure["repeat_occurrences"] += int(is_repeat)
            checks = [
                check
                for check in record["results"].get("rectification", {}).get("checks", [])
                if check["issue_id"] == issue.get("issue_id")
            ]
            passed = bool(checks) and all(check["verdict"] == "satisfied" for check in checks)
            failed = any(check["verdict"] == "unsatisfied" for check in checks)
            measure["verified_pass" if passed else "verified_failure" if failed else "unknown"] += 1
            previous = seen.get(recurrence_key)
            if previous and previous["passed"]:
                measures[previous["action"]]["reappeared_after_pass"] += 1
            case_keys[recurrence_key] = {"action": action, "passed": passed}
        seen.update(case_keys)
    return {
        "source_ref": "statistics",
        "period": {"since": since, "until": until},
        "record_count": len(selected),
        "issue_count": total,
        "category_counts": dict(categories.most_common()),
        "area_counts": dict(areas.most_common()),
        "supervisor_counts": dict(supervisors.most_common()),
        "repeat_occurrences": repeated,
        "recurrence_rate": repeated / total if total else None,
        "measures": measures,
        "source_refs": [f"archive:{record['id']}" for record in selected],
        "limitations": [
            "复发定义：窗口内不同巡检中产线、分类、位置相同的后续问题；不是因果结论。",
            "未再次记录不代表整改有效；缺少复查覆盖与观察窗口时不能认定措施消除了问题。",
            "措施按已确认问题的建议归类；复查通过/失败为关联观察，不证明建议确已完整执行或具有因果效果。",
        ],
    }
