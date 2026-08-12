"""
알림 아웃박스 워커 — 지속 가능하고 멱등한 알림 전송.

특징:
- 영속 큐(JSONL 파일) 기반으로 재시작 후에도 전송 보장
- 멱등키로 중복 전송 방지
- 지수 백오프 재시도
- 채널별 전송기 플러그인 구조
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from src.events.models import (
    NotificationAttempt,
    NotificationChannel,
    NotificationStatus,
)

logger = logging.getLogger(__name__)
_STOP = object()


@dataclass
class OutboxRecord:
    """아웃박스 레코드 — JSONL로 영속 저장"""
    attempt: NotificationAttempt
    retry_count: int = 0
    next_retry_at: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_json(self) -> str:
        data = {
            "attempt": self.attempt.to_dict(),
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(),
        }
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> OutboxRecord:
        data = json.loads(line)
        attempt_data = dict(data["attempt"])
        attempt_data["channel"] = NotificationChannel(attempt_data["channel"])
        attempt_data["status"] = NotificationStatus(attempt_data["status"])
        for field_name in ("sent_at", "created_at"):
            value = attempt_data.get(field_name)
            if isinstance(value, str) and value:
                attempt_data[field_name] = datetime.fromisoformat(value)
            elif value is None and field_name == "created_at":
                attempt_data[field_name] = datetime.now()
        attempt = NotificationAttempt(**attempt_data)
        return cls(
            attempt=attempt,
            retry_count=data.get("retry_count", 0),
            next_retry_at=data.get("next_retry_at", 0.0),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


class NotificationDispatcher:
    """채널별 알림 디스패처"""

    def __init__(self):
        self._handlers: dict[NotificationChannel, Callable[[NotificationAttempt], bool]] = {}

    def register(self, channel: NotificationChannel, handler: Callable[[NotificationAttempt], bool]):
        self._handlers[channel] = handler

    def dispatch(self, attempt: NotificationAttempt) -> bool:
        handler = self._handlers.get(attempt.channel)
        if not handler:
            logger.warning(f"채널 핸들러 없음: {attempt.channel}")
            return False
        try:
            return handler(attempt)
        except Exception as e:
            logger.error(f"알림 전송 실패 [{attempt.channel}]: {e}")
            return False


class OutboxWorker:
    """알림 아웃박스 워커 — 별도 스레드에서 전송/재시도 처리"""

    DEFAULT_MAX_RETRIES = 5
    DEFAULT_BASE_BACKOFF = 10.0  # seconds
    DEFAULT_MAX_BACKOFF = 300.0  # 5 minutes

    def __init__(
        self,
        outbox_dir: str = "./data/outbox",
        dispatcher: NotificationDispatcher | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff: float = DEFAULT_BASE_BACKOFF,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        poll_interval: float = 5.0,
    ):
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.outbox_dir, 0o700)
        self.pending_file = self.outbox_dir / "pending.jsonl"
        self.dead_file = self.outbox_dir / "dead.jsonl"
        self.terminal_file = self.outbox_dir / "terminal.jsonl"

        self.dispatcher = dispatcher or NotificationDispatcher()
        self.max_retries = max(1, int(max_retries))
        self.base_backoff = max(0.0, float(base_backoff))
        self.max_backoff = max(0.0, float(max_backoff))
        self.poll_interval = max(0.01, float(poll_interval))

        self._queue: Queue[OutboxRecord] = Queue()
        self._seen_keys: set[str] = set()  # 대기/전송 중인 멱등키
        self._completed_keys: set[str] = set()
        self._delivered_keys: set[str] = set()
        # Durable pending SSOT. Queue/in-flight state is only a scheduling detail.
        self._pending_records: dict[str, OutboxRecord] = {}
        self._inflight: dict[str, OutboxRecord] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._load_terminal()
        self._load_pending()

        logger.info(f"OutboxWorker 초기화: {self.outbox_dir}")

    def _load_terminal(self):
        """종결 저널을 로드해 재시작 시 이미 전송된 알림을 재발송하지 않는다."""
        if not self.terminal_file.exists():
            return
        with self.terminal_file.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    key = str(data["idempotency_key"])
                    self._completed_keys.add(key)
                    if data.get("status") == NotificationStatus.SENT.value:
                        self._delivered_keys.add(key)
                except Exception as exc:
                    logger.error("아웃박스 종결 레코드 로드 실패: %s", exc)

    def _load_pending(self):
        """시작 시 대기 중인 레코드 로드"""
        if not self.pending_file.exists():
            return
        records_by_key: dict[str, OutboxRecord] = {}
        with self.pending_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = OutboxRecord.from_json(line)
                    records_by_key[record.attempt.idempotency_key] = record
                except Exception as e:
                    logger.error(f"아웃박스 레코드 로드 실패: {e}")
        loaded = 0
        for key, record in records_by_key.items():
            if key in self._completed_keys:
                continue
            self._seen_keys.add(key)
            self._pending_records[key] = record
            self._queue.put(record)
            loaded += 1
        logger.info(f"아웃박스 {loaded}개 레코드 로드 완료")

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _persist_record(self, record: OutboxRecord):
        """레코드를 pending.jsonl에 추가"""
        with self._lock:
            record.updated_at = datetime.now().isoformat()
            self._append_line(self.pending_file, record.to_json())

    def _persist_dead(self, record: OutboxRecord):
        """최대 재시도 초과 시 dead.jsonl로 이동"""
        with self._lock:
            self._append_line(self.dead_file, record.to_json())
            logger.warning(f"알림 dead-letter 이동: {record.attempt.attempt_id} (재시도 {record.retry_count}회)")

    def _persist_terminal(self, record: OutboxRecord) -> None:
        attempt = record.attempt
        data = {
            "attempt_id": attempt.attempt_id,
            "idempotency_key": attempt.idempotency_key,
            "status": attempt.status.value,
            "completed_at": datetime.now().isoformat(),
        }
        with self._lock:
            self._append_line(self.terminal_file, json.dumps(data, ensure_ascii=False))
            self._completed_keys.add(attempt.idempotency_key)
            self._pending_records.pop(attempt.idempotency_key, None)
            if attempt.status == NotificationStatus.SENT:
                self._delivered_keys.add(attempt.idempotency_key)

    def _rewrite_pending(self):
        """큐에 남은 레코드로 pending.jsonl 재작성 (주기적 압축)"""
        with self._lock:
            temp_file = self.pending_file.with_name(
                f".{self.pending_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            with temp_file.open("w", encoding="utf-8") as f:
                os.chmod(temp_file, 0o600)
                for record in self._pending_records.values():
                    f.write(record.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            temp_file.replace(self.pending_file)

    def enqueue(self, attempt: NotificationAttempt) -> bool:
        """알림 시도를 아웃박스에 추가 (멱등키 중복 방지)"""
        if not attempt.idempotency_key:
            logger.error("빈 멱등키 알림은 enqueue할 수 없습니다")
            return False
        record = OutboxRecord(
            attempt=attempt,
            retry_count=0,
            next_retry_at=time.time(),
        )
        with self._lock:
            if (
                attempt.idempotency_key in self._seen_keys
                or attempt.idempotency_key in self._completed_keys
            ):
                logger.debug(f"중복 알림 무시: {attempt.idempotency_key}")
                return False
            try:
                # 내구성 확보 후 워커에 공개한다. 반대 순서는 send-before-persist race를 만든다.
                self._append_line(self.pending_file, record.to_json())
            except Exception as exc:
                logger.error("아웃박스 영속화 실패: %s", exc)
                return False
            self._seen_keys.add(attempt.idempotency_key)
            self._pending_records[attempt.idempotency_key] = record
            self._queue.put(record)
        logger.debug(f"아웃박스 추가: {attempt.attempt_id} ({attempt.channel.value})")
        return True

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="OutboxWorker")
        self._thread.start()
        logger.info("OutboxWorker 시작")

    def stop(self, timeout: float = 30.0):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._queue.put(_STOP)  # type: ignore[arg-type]
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("OutboxWorker가 제한 시간 내 종료되지 않았습니다")
                return
        with self._queue.mutex:
            while _STOP in self._queue.queue:
                self._queue.queue.remove(_STOP)
        # 종료 전 남은 큐 저장
        self._rewrite_pending()
        logger.info("OutboxWorker 정지")

    def status(self) -> dict[str, int | bool]:
        """Return aggregate delivery state without exposing recipients or payloads."""
        with self._lock:
            return {
                "running": self._running,
                "pending": len(self._pending_records),
                "inflight": len(self._inflight),
                "delivered": len(self._delivered_keys),
                "dead_letter": self._record_count(self.dead_file),
            }

    @staticmethod
    def _record_count(path: Path) -> int:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def _run(self):
        last_rewrite = time.time()
        while self._running:
            try:
                record = self._queue.get(timeout=self.poll_interval)
            except Empty:
                pass
            else:
                if record is _STOP:
                    break
                key = record.attempt.idempotency_key
                with self._lock:
                    self._inflight[key] = record
                terminal = False
                try:
                    terminal = self._process_record(record)
                except Exception:
                    logger.exception("아웃박스 레코드 처리 중 예기치 않은 오류: %s", key)
                    record.next_retry_at = time.time() + self.base_backoff
                    self._queue.put(record)
                finally:
                    with self._lock:
                        self._inflight.pop(key, None)
                if terminal:
                    try:
                        self._rewrite_pending()
                    except Exception:
                        logger.exception("아웃박스 pending 압축 실패")

            # 주기적 pending 재작성 (압축)
            if time.time() - last_rewrite > 300:  # 5분마다
                try:
                    self._rewrite_pending()
                except Exception:
                    logger.exception("아웃박스 주기적 pending 압축 실패")
                last_rewrite = time.time()

    def _process_record(self, record: OutboxRecord) -> bool:
        attempt = record.attempt
        now = time.time()

        # 긴 backoff 동안 단일 워커를 막지 않고 큐 뒤로 보낸다.
        if now < record.next_retry_at:
            self._queue.put(record)
            time.sleep(min(record.next_retry_at - now, self.poll_interval))
            return False

        success = self.dispatcher.dispatch(attempt)

        if success:
            attempt.status = NotificationStatus.SENT
            attempt.sent_at = datetime.now()
            logger.info(f"알림 전송 성공: {attempt.attempt_id}")
            self._persist_terminal(record)
            with self._lock:
                self._seen_keys.discard(attempt.idempotency_key)
            return True
        else:
            record.retry_count += 1
            if record.retry_count >= self.max_retries:
                # 최대 재시도 초과 -> dead letter
                attempt.status = NotificationStatus.FAILED
                self._persist_dead(record)
                self._persist_terminal(record)
                with self._lock:
                    self._seen_keys.discard(attempt.idempotency_key)
                return True
            else:
                # 지수 백오프로 재시도 예약
                backoff = min(
                    self.base_backoff * (2 ** (record.retry_count - 1)),
                    self.max_backoff
                )
                record.next_retry_at = time.time() + backoff
                attempt.status = NotificationStatus.PENDING
                self._persist_record(record)
                self._queue.put(record)
                logger.warning(f"알림 재시도 예약: {attempt.attempt_id} (시도 {record.retry_count}/{self.max_retries}, {backoff:.0f}초 후)")
        return False

    def stats(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "seen_keys": len(self._seen_keys),
            "pending_file": str(self.pending_file),
            "dead_file": str(self.dead_file),
            "delivered_keys": len(self._delivered_keys),
        }
