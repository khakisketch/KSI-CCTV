"""Immutable release qualification for experimental event capabilities."""


# These event names remain experimental regardless of mutable site policy YAML.
EXPERIMENTAL_EVENT_CAPABILITIES = frozenset({
    "fall",
    "fight",
    "no_vest",
    "loitering",
    "tamper",
    "line_crossing",
})

# A qualified release must add an event here in source control. This release has none.
RELEASE_QUALIFIED_EXPERIMENTAL_EVENTS = frozenset()

UNQUALIFIED_EXPERIMENTAL_REASON = (
    "이 시험 기능은 이번 릴리스에서 자격 검증되지 않아 선택하거나 실행할 수 없습니다."
)


def release_qualification(event_name: str, configured_status: str) -> tuple[bool, str | None]:
    """Return immutable release eligibility without trusting mutable status upgrades."""
    if event_name in EXPERIMENTAL_EVENT_CAPABILITIES:
        if event_name in RELEASE_QUALIFIED_EXPERIMENTAL_EVENTS:
            return True, None
        return False, UNQUALIFIED_EXPERIMENTAL_REASON
    if configured_status == "experimental":
        if event_name in RELEASE_QUALIFIED_EXPERIMENTAL_EVENTS:
            return True, None
        return False, UNQUALIFIED_EXPERIMENTAL_REASON
    if configured_status == "active":
        return True, None
    return False, "이 기능은 현재 릴리스에서 사용할 수 없습니다."
