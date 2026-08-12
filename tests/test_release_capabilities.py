"""Release qualification must not be granted by mutable configuration."""
from src.release_capabilities import (
    RELEASE_QUALIFIED_EXPERIMENTAL_EVENTS,
    release_qualification,
)


def test_release_has_no_qualified_experimental_capabilities():
    assert not RELEASE_QUALIFIED_EXPERIMENTAL_EVENTS
    assert release_qualification("fall", "experimental")[0] is False
    assert release_qualification("fire", "active") == (True, None)


def test_mutable_status_upgrade_cannot_qualify_known_experimental_capability():
    qualified, reason = release_qualification("fall", "active")

    assert qualified is False
    assert reason
