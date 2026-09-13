"""문서 부채 회귀 테스트 (U7).

- 실행 코드·스크립트·설정에는 Streamlit 잔재가 없다 (역사 문서는 제외).
- troubleshooting은 폐기된 ui.dashboard_port를 가리키지 않는다.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HISTORICAL = {"docs", ".ai", ".git", ".venv", "node_modules", "__pycache__"}


def _live_files():
    for base in ("src", "scripts", "config", "installer"):
        root = REPO / base
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".yaml", ".yml", ".json"}:
                yield path
    for name in ("pyproject.toml", "ruff.toml", "requirements.txt", "requirements-dev.txt"):
        path = REPO / name
        if path.exists():
            yield path


def test_no_streamlit_references_in_live_code():
    hits = [
        str(path.relative_to(REPO))
        for path in _live_files()
        if re.search("streamlit", path.read_text(encoding="utf-8", errors="replace"), re.IGNORECASE)
    ]
    assert hits == [], f"Streamlit 잔재: {hits}"


def test_troubleshooting_does_not_point_to_retired_port_key():
    text = (REPO / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    assert "ui.dashboard_port" not in text
