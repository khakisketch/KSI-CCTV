"""확장 게이팅 회귀 테스트 (U6).

- 불변 실험 게이팅으로 선택 불가한 이벤트를 지정하면 거부 오류에
  이벤트명과 차단 사유(자격 미검증 등)가 포함되어야 한다.
- 기본 OFF 확장분(pose/ST-GCN/open-vocab)은 설정 없이 실행되지 않는다.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.api.service import validate_event_selection
from src.release_capabilities import UNQUALIFIED_EXPERIMENTAL_REASON


def test_unavailable_selection_error_names_reason():
    try:
        validate_event_selection("construction", ["fall"])
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("unqualified experimental 선택이 거부되지 않았다")
    assert "fall" in message
    assert "자격" in message
    assert UNQUALIFIED_EXPERIMENTAL_REASON in message
