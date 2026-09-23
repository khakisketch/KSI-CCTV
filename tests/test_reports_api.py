"""P3-T4: 보고서 조회 API 계약 테스트 (TDD RED).

읽기 전용 3종: GET /api/reports (목록) · GET /api/reports/{id} (열람) ·
GET /api/reports/{id}/download (Markdown). 쓰기 API는 없다.
RBAC·감사 기존 패턴: auth 의존성(401/503) + 조회 무감사(과도 로깅 금지).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    from fastapi.testclient import TestClient

    from src.api import service
    from src.api.app import create_app
    from src.reports.store import ReportStore
    _HAS_API = True
except Exception:
    _HAS_API = False

_KEY = "test-api-key-strong"

_audit_redirect = None


def setUpModule():
    global _audit_redirect
    if not _HAS_API:
        return
    _audit_redirect = tempfile.TemporaryDirectory()
    service._AUDIT_LOG = Path(_audit_redirect.name) / "audit.jsonl"


def tearDownModule():
    if _audit_redirect is not None:
        _audit_redirect.cleanup()


def _seed(store: ReportStore) -> list[int]:
    ids = []
    for period in ("daily", "weekly"):
        ids.append(store.save({
            "period": period,
            "since": "2026-09-21T00:00:00+00:00",
            "until": "2026-09-22T00:00:00+00:00",
            "model": "report-llm-test",
            "markdown": f"# {period} report\n\n- item\n",
        }))
    return ids


class _FakeScheduler:
    """live 스케줄러 대역 — status() 키 규격(last_run/next_run/failure_count)."""

    def __init__(self) -> None:
        self.calls = 0

    def status(self):
        self.calls += 1
        return {
            "daily": {
                "last_run": "2026-09-22T00:00:00+00:00",
                "last_success": "2026-09-22T00:00:00+00:00",
                "next_run": "2026-09-23T00:00:00+00:00",
                "failure_count": 0,
            },
            "weekly": {
                "last_run": None,
                "last_success": None,
                "next_run": None,
                "failure_count": 2,
            },
        }


@unittest.skipUnless(_HAS_API, "fastapi 미설치 — skip")
class TestReportsApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.app = create_app()
        self.store = ReportStore(Path(self._tmp.name) / "reports.db")
        self.app.state.report_store = self.store
        self.ids = _seed(self.store)
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": _KEY}

    def test_list_returns_metadata_without_markdown(self):
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["reports"]), 2)
        for item in body["reports"]:
            for key in ("id", "period", "since", "until", "model", "created_at"):
                self.assertIn(key, item)
            self.assertNotIn("markdown", item)

    def test_list_exposes_schedule_status(self):
        scheduler = _FakeScheduler()
        self.app.state.report_scheduler = scheduler
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        status = r.json()["status"]
        self.assertIn("daily", status["periods"])
        daily = status["periods"]["daily"]
        for key in ("last_run", "last_success", "next_run", "failure_count"):
            self.assertIn(key, daily)
        self.assertEqual(daily["failure_count"], 0)
        self.assertEqual(status["periods"]["weekly"]["failure_count"], 2)

    def test_list_without_scheduler_falls_back_to_derived_status(self):
        self.assertIsNone(getattr(self.app.state, "report_scheduler", None))
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        status = r.json()["status"]
        self.assertFalse(status["live"])
        daily = status["periods"]["daily"]
        self.assertIsNone(daily["last_run"])
        self.assertIsNone(daily["failure_count"])
        self.assertEqual(daily["last_success"], self.store.get(self.ids[0])["created_at"])
        self.assertIsNotNone(daily["next_run"])

    def test_live_status_normalizes_disabled_period(self):
        """I-1: live에 없는 주기도 enabled:false+null로 보완 — 카드 렌더 유지."""
        self.app.state.config = {
            "reports": {
                "enabled": True,
                "daily_enabled": True,
                "weekly_enabled": False,
                "retention_days": 90,
            }
        }

        class _DailyOnly:
            def status(self):
                return {
                    "daily": {
                        "last_run": "2026-09-22T00:00:00+00:00",
                        "last_success": "2026-09-22T00:00:00+00:00",
                        "next_run": "2026-09-23T00:00:00+00:00",
                        "failure_count": 0,
                    }
                }

        self.app.state.report_scheduler = _DailyOnly()
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        status = r.json()["status"]
        self.assertTrue(status["live"])
        self.assertIn("weekly", status["periods"])
        weekly = status["periods"]["weekly"]
        self.assertFalse(weekly["enabled"])
        self.assertIsNone(weekly["last_success"])
        self.assertIsNone(weekly["next_run"])
        self.assertIsNone(weekly["failure_count"])

    def test_live_status_failure_falls_back(self):
        """I-4: live status() 예외는 500이 아니라 유도값 fallback으로 격리."""

        class _Broken:
            def status(self):
                raise RuntimeError("scheduler exploded")

        self.app.state.report_scheduler = _Broken()
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        status = r.json()["status"]
        self.assertFalse(status["live"])
        self.assertIsNotNone(status["periods"]["daily"]["last_success"])

    def test_period_filter_applies_before_limit_cut(self):
        """I-2: period 필터는 SQL WHERE로 절단 전 적용 — limit 안에 없어도 조회된다."""
        for index in range(3):
            self.store.save({
                "period": "weekly",
                "since": "2026-09-21T00:00:00+00:00",
                "until": "2026-09-22T00:00:00+00:00",
                "model": "report-llm-test",
                "markdown": f"# weekly {index}\n",
            })
        r = self.client.get("/api/reports?period=daily&limit=2", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["reports"][0]["period"], "daily")

    def test_injected_store_is_shared_not_recreated(self):
        """I-3: 주입된 스케줄러 저장소를 그대로 공유 — 교체·재생성 없음."""
        before = self.app.state.report_store
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIs(self.app.state.report_store, before)

    def test_missing_db_file_returns_empty_without_creating(self):
        """R8 I-2: 파일 부재+미주입 GET은 200 빈 목록 + live:false, 파일 미생성."""
        delattr(self.app.state, "report_store")
        missing = Path(self._tmp.name) / "nodata.db"
        self.app.state.config = {"reports": {"db_path": str(missing)}}
        r = self.client.get("/api/reports", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["reports"], [])
        self.assertFalse(body["status"]["live"])
        self.assertFalse(missing.exists())
        self.assertIsNone(getattr(self.app.state, "report_store", None))

    def test_missing_db_file_detail_and_download_404_without_creating(self):
        """R8 I-2: 파일 부재 시 열람·다운로드도 404이며 파일을 만들지 않는다."""
        delattr(self.app.state, "report_store")
        missing = Path(self._tmp.name) / "nodata.db"
        self.app.state.config = {"reports": {"db_path": str(missing)}}
        self.assertEqual(
            self.client.get("/api/reports/1", headers=self.headers).status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/reports/1/download", headers=self.headers).status_code,
            404,
        )
        self.assertFalse(missing.exists())

    def test_lazy_store_creation_is_singleton_under_race(self):
        """M-2: 동시 지연 생성은 단일 인스턴스로 수렴 (락)."""
        import threading

        from src.api.routes import reports as reports_routes
        from src.reports.store import ReportStore

        db_path = Path(self._tmp.name) / "race.db"
        # 파일을 미리 만들어 I-2 게이트를 통과시킨다 (생성은 _connect 시 발생).
        seed = ReportStore(db_path)
        seed.list()
        seed.close()
        delattr(self.app.state, "report_store")
        self.app.state.config = {"reports": {"db_path": str(db_path)}}
        barrier = threading.Barrier(8)
        results: list = []

        def grab() -> None:
            barrier.wait(timeout=10)
            results.append(reports_routes._store_for(self.app))

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(results), 8)
        self.assertEqual(len({id(store) for store in results}), 1)

    def test_abnormal_retention_clamps_to_default(self):
        """M-1: 비정상 보존기간은 그대로 노출하지 않고 기본 90일로 clamp."""
        for bad in (-5, 0, "abc"):
            with self.subTest(bad=bad):
                self.app.state.config = {"reports": {"retention_days": bad}}
                r = self.client.get("/api/reports", headers=self.headers)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["status"]["retention_days"], 90)

    def test_detail_returns_full_markdown(self):
        r = self.client.get(f"/api/reports/{self.ids[0]}", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        report = r.json()["report"]
        self.assertEqual(report["id"], self.ids[0])
        self.assertIn("# daily report", report["markdown"])

    def test_detail_missing_returns_404(self):
        r = self.client.get("/api/reports/9999", headers=self.headers)
        self.assertEqual(r.status_code, 404)

    def test_download_returns_markdown_attachment(self):
        r = self.client.get(f"/api/reports/{self.ids[1]}/download", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/markdown", r.headers["content-type"])
        self.assertIn("attachment", r.headers.get("content-disposition", ""))
        self.assertIn("# weekly report", r.text)

    def test_download_missing_returns_404(self):
        r = self.client.get("/api/reports/9999/download", headers=self.headers)
        self.assertEqual(r.status_code, 404)

    def test_requires_api_key(self):
        r = self.client.get("/api/reports")
        self.assertEqual(r.status_code, 401)

    def test_fail_closed_when_key_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KSI_CCTV_API_KEY", None)
            # _configured_key가 ""를 읽도록 보장 (환경에 키가 없을 때만 유효)
            r = self.client.get("/api/reports", headers={"X-API-Key": "anything"})
            self.assertEqual(r.status_code, 503)

    def test_no_write_api(self):
        r = self.client.post("/api/reports", json={}, headers=self.headers)
        self.assertIn(r.status_code, (404, 405))

    def test_reads_do_not_write_audit(self):
        audit_log = service._AUDIT_LOG
        before = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
        self.client.get("/api/reports", headers=self.headers)
        self.client.get(f"/api/reports/{self.ids[0]}", headers=self.headers)
        after = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
