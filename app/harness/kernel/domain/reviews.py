from __future__ import annotations

from typing import Any


def prepare_tool_resume(original: dict[str, Any], effective: dict[str, Any]) -> dict[str, Any]:
    interruptions = original.get("interrupts", [])
    if not interruptions:
        raise ValueError("No durable interrupts associated with this review")
    identifiers = {item["id"] for item in interruptions}
    explicit = effective.get("decisions_by_interrupt", effective.get("responses", {}))
    if explicit and set(explicit) != identifiers:
        raise ValueError("Resume decisions must match all interrupt identifiers exactly")
    if "decisions" in effective and len(interruptions) != 1:
        raise ValueError("Multiple interrupts require decisions_by_interrupt")
    result = {}
    for item in interruptions:
        value = item["value"]
        if isinstance(value, dict) and "framework_approval" in value:
            result[item["id"]] = True
            continue
        decision = explicit.get(item["id"], effective if "decisions" in effective else None)
        if not isinstance(value, dict) or "action_requests" not in value:
            if item["id"] not in explicit:
                raise ValueError("Custom interrupts require an explicit responses mapping")
            result[item["id"]] = decision
            continue
        requests = value["action_requests"]
        if decision is None:
            if effective != original:
                raise ValueError("Unsupported tool review payload")
            decision = {"decisions": [{"type": "approve"} for _ in requests]}
        choices = decision.get("decisions", [])
        if len(choices) != len(requests):
            raise ValueError("One ordered decision is required per tool action")
        rules = {rule["action_name"]: rule for rule in value.get("review_configs", [])}
        for request, choice in zip(requests, choices, strict=True):
            allowed = rules.get(request["name"], {}).get(
                "allowed_decisions", ["approve", "edit", "reject"]
            )
            if choice.get("type") not in allowed:
                raise ValueError("Tool decision violates the interrupt review policy")
            if choice["type"] == "edit":
                edited = choice.get("edited_action", {})
                if edited.get("name") != request["name"] or not isinstance(
                    edited.get("args"), dict
                ):
                    raise ValueError("Tool editing may change arguments, not tool identity")
        result[item["id"]] = decision
    return {"decisions_by_interrupt": result}


def resume_values(payload: dict[str, Any]) -> Any:
    if "decisions_by_interrupt" in payload:
        return payload["decisions_by_interrupt"]
    return prepare_tool_resume(payload, payload)["decisions_by_interrupt"]
