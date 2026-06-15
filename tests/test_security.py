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


if __name__ == "__main__":
    unittest.main()
