"""보안 유틸리티 회귀 테스트 — RTSP/HTTP 비밀값 로그 마스킹.

mask_url/mask_text는 RTSP/SMTP 계정·비밀번호가 로그·감사 로그에 노출되는 것을
막는 보안 핵심 함수다(CLAUDE.md: 비밀값 저장소/로그 커밋 금지). 회귀로 깨지면
운영 로그에 평문 자격증명이 새므로 회귀 가드를 둔다.
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.utils.config import mask_text, mask_url


class TestMaskUrl(unittest.TestCase):
    def test_rtsp_credentials_masked(self):
        masked = mask_url("rtsp://admin:secret123@192.168.0.10:554/stream")
        self.assertNotIn("admin", masked)
        self.assertNotIn("secret123", masked)
        self.assertIn("***:***@192.168.0.10:554", masked)
        self.assertTrue(masked.startswith("rtsp://"))

    def test_http_credentials_masked(self):
        masked = mask_url("http://user:pw@example.com/path")
        self.assertNotIn("user", masked)
        self.assertNotIn("pw", masked)
        self.assertIn("***:***@example.com", masked)

    def test_url_without_credentials_unchanged(self):
        url = "rtsp://192.168.0.10:554/stream"
        self.assertEqual(mask_url(url), url)

    def test_non_string_returned_as_is(self):
        self.assertIsNone(mask_url(None))
        self.assertEqual(mask_url(""), "")
        self.assertEqual(mask_url(12345), 12345)

    def test_password_not_leaked_even_with_special_chars(self):
        masked = mask_url("rtsp://op:p%40ss@cam.local:554/h264")
        self.assertNotIn("p%40ss", masked)
        self.assertIn("***:***@cam.local:554", masked)


class TestMaskText(unittest.TestCase):
    def test_embedded_rtsp_url_masked_in_log_line(self):
        line = "카메라 연결 실패: rtsp://admin:secret123@10.0.0.5:554/stream (timeout)"
        masked = mask_text(line)
        self.assertNotIn("secret123", masked)
        self.assertNotIn("admin", masked)
        self.assertIn("연결 실패", masked)  # 비-URL 텍스트는 보존

    def test_embedded_http_url_masked(self):
        line = "webhook http://svc:token99@hooks.local/notify 호출"
        masked = mask_text(line)
        self.assertNotIn("token99", masked)

    def test_plain_text_unchanged(self):
        line = "정상 동작 중 — 자격증명 없음"
        self.assertEqual(mask_text(line), line)

    def test_non_string_returned_as_is(self):
        self.assertIsNone(mask_text(None))
        self.assertEqual(mask_text(""), "")


class TestMaskTextPII(unittest.TestCase):
    """PII 마스킹(#14) — VLM 자유서술이 전사한 연락처·주민번호를 가린다."""

    def test_email_masked(self):
        masked = mask_text("작업자가 명찰 hong.gildong@example.co.kr 를 착용")
        self.assertNotIn("hong.gildong@example.co.kr", masked)
        self.assertIn("작업자", masked)

    def test_korean_phone_masked(self):
        for raw in ("010-1234-5678", "01012345678", "+82-10-1234-5678"):
            with self.subTest(raw=raw):
                self.assertNotIn(raw.replace("+82-", "").replace("+82", ""), mask_text(f"연락처 {raw} 확인"))
        self.assertNotIn("1234", mask_text("연락처 010-1234-5678 확인"))

    def test_rrn_masked(self):
        masked = mask_text("신원 900101-1234567 노출")
        self.assertNotIn("900101-1234567", masked)
        self.assertNotIn("1234567", masked)

    def test_unix_timestamp_event_id_not_corrupted(self):
        """event_id의 Unix timestamp(10자리, 17로 시작)는 전화번호로 오탐되면 안 된다."""
        line = '{"event_id": "cam01_fall_1781893381", "confidence": 0.87}'
        self.assertEqual(mask_text(line), line)

    def test_log_json_structure_preserved(self):
        """실제 alerts.log 형태 JSON에서 PII만 가리고 구조·수치 필드는 보존한다."""
        import json

        payload = {
            "event_id": "camA_no_helmet_1781893381",
            "timestamp": "2026-06-15T14:23:01.123456",
            "confidence": 0.873,
            "detections": [{"bbox": [101, 2345, 678, 1011], "class_name": "person"}],
            "vlm_inference_ms": 1234.5,
            "description": "작업자가 helmet 미착용. 연락처 010-1234-5678, hong@corp.com",
        }
        line = json.dumps(payload, ensure_ascii=False)
        masked = mask_text(line)
        # 여전히 valid JSON이어야 한다(구조 파괴 없음)
        parsed = json.loads(masked)
        # PII는 사라짐
        self.assertNotIn("010-1234-5678", masked)
        self.assertNotIn("hong@corp.com", masked)
        # 수치/구조 필드는 그대로
        self.assertEqual(parsed["confidence"], 0.873)
        self.assertEqual(parsed["timestamp"], "2026-06-15T14:23:01.123456")
        self.assertEqual(parsed["event_id"], "camA_no_helmet_1781893381")
        self.assertEqual(parsed["detections"][0]["bbox"], [101, 2345, 678, 1011])
        self.assertEqual(parsed["vlm_inference_ms"], 1234.5)


if __name__ == "__main__":
    unittest.main()
