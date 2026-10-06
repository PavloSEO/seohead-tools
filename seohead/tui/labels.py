"""Human labels for terminal presentation; persisted API identifiers stay unchanged."""

LABELS = {
    "not_run": "Not started",
    "not_agreed": "Outside agreed scope",
    "pending_exclusion": "Exclusion needs review",
    "not_applicable": "Not applicable",
    "remaining": "Ready to start",
    "review": "Needs review",
    "deliverable": "Deliverable needs review",
    "succeeded": "Succeeded",
    "run": "Evidence recorded",
    "running": "In progress",
    "completed": "Complete",
    "blocked": "Blocked",
    "failed": "Failed",
    "unavailable": "Unavailable",
    "stale": "Evidence needs refresh",
    "excluded": "Excluded from scope",
    "proposed_goal": "Proposed goal",
    "screaming_frog": "Screaming Frog",
    "sf_exports": "Supplied Screaming Frog exports",
    "sf_live": "Live Screaming Frog crawl",
    "native": "Native crawler",
    "schema": "Structured data",
    "primary": "Own site",
    "competitor": "Competitor",
    "proposed_competitor": "Suggested competitor",
    "task_ids": "Linked tasks",
    "goal_id": "Linked goal",
    "blocked_by": "Blocked by",
    "attempt_status": "Latest attempt",
    "execution_kind": "Execution method",
    "definition_hash": "Definition fingerprint",
    "config_fingerprint": "Configuration fingerprint",
}


def label(value: object) -> str:
    if value is None or value == "":
        return "Not recorded"
    text = str(value)
    return LABELS.get(text, text.replace("_", " ").capitalize())


def title(value: str) -> str:
    """Skill catalogue names may be slugs; preserve full prose titles verbatim."""
    return value.replace("-", " ").replace("_", " ").capitalize() if " " not in value else value


def value_lines(value: object, *, indent: str = "") -> list[str]:
    """Present already bounded retained evidence without a second silent truncation."""
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}{label(key)}:")
                lines.extend(value_lines(item, indent=indent + "  "))
            else:
                if key in {
                    "state",
                    "status",
                    "attempt_status",
                    "kind",
                    "applicability",
                    "execution_kind",
                }:
                    item = label(item)
                lines.append(
                    f"{indent}{label(key)}: {item if item is not None else 'Not recorded'}"
                )
        return lines or [indent + "No retained fields"]
    if isinstance(value, list):
        return [line for item in value for line in value_lines(item, indent=indent + "• ")]
    return [indent + str(value)]
