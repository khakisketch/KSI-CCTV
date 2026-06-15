"""
시스템 리소스 모니터링 모듈

CPU, RAM, GPU, 디스크 사용량 수집 및 헬스체크 정보 제공.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SystemMetrics:
    """시스템 메트릭"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    gpu_percent: float | None = None
    gpu_memory_percent: float | None = None
    gpu_temperature: float | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "memory_used_mb": round(self.memory_used_mb, 1),
            "memory_total_mb": round(self.memory_total_mb, 1),
            "disk_percent": round(self.disk_percent, 1),
            "disk_used_gb": round(self.disk_used_gb, 2),
            "disk_total_gb": round(self.disk_total_gb, 2),
            "gpu_percent": round(self.gpu_percent, 1) if self.gpu_percent is not None else None,
            "gpu_memory_percent": round(self.gpu_memory_percent, 1) if self.gpu_memory_percent is not None else None,
            "gpu_temperature": round(self.gpu_temperature, 1) if self.gpu_temperature is not None else None,
            "timestamp": self.timestamp,
        }


def get_cpu_percent() -> float:
    """CPU 사용률 조회"""
    try:
        import psutil
        return psutil.cpu_percent(interval=0.1)
    except ImportError:
        # psutil 없으면 /proc/stat 기반 간단 계산
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            total = sum(int(x) for x in parts[1:])
            idle = int(parts[4])
            return 100.0 * (1 - idle / total) if total > 0 else 0.0
        except Exception:
            return 0.0


def get_memory_info() -> dict[str, float]:
    """메모리 사용 정보 조회"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "used_mb": mem.used / (1024 * 1024),
            "total_mb": mem.total / (1024 * 1024),
        }
    except ImportError:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                key, value = line.split(":", 1)
                info[key.strip()] = int(value.strip().split()[0])
            total = info.get("MemTotal", 0) / 1024  # KB → MB
            available = info.get("MemAvailable", 0) / 1024
            used = total - available
            return {
                "percent": (used / total * 100) if total > 0 else 0,
                "used_mb": used,
                "total_mb": total,
            }
        except Exception:
            return {"percent": 0, "used_mb": 0, "total_mb": 0}


def get_disk_info(path: str = "/") -> dict[str, float]:
    """디스크 사용 정보 조회"""
    try:
        usage = shutil.disk_usage(path)
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        return {
            "percent": (used_gb / total_gb * 100) if total_gb > 0 else 0,
            "used_gb": used_gb,
            "total_gb": total_gb,
        }
    except Exception:
        return {"percent": 0, "used_gb": 0, "total_gb": 0}


def get_dir_size(path: str) -> float:
    """디렉터리 전체 크기를 바이트 단위로 반환.

    디렉터리가 없거나 접근 불가 시 0.0을 반환한다.
    심볼릭 링크는 따라가지 않아 이중 집계를 방지한다.
    """
    import os
    total = 0.0
    try:
        for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
            for fname in filenames:
                try:
                    fp = os.path.join(dirpath, fname)
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total


def get_gpu_info() -> dict[str, float | None]:
    """GPU 정보 조회 (pynvml 사용).

    호출 실패를 세분화한다:
    - util 조회 실패 시 percent=None, 다른 필드는 계속 시도한다.
    - mem 조회 실패 시 memory_percent=None (0 오보고 방지).
    - GPU 자체가 없으면(pynvml 예외) 전부 None 반환.
    이렇게 하면 "GPU 없음"과 "GPU 있으나 일부 조회 실패"를 구분할 수 있다.
    """
    try:
        import pynvml
        pynvml.nvmlInit()
    except Exception:
        # GPU 없음 또는 pynvml 초기화 실패
        return {"percent": None, "memory_percent": None, "temperature": None}

    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception:
        return {"percent": None, "memory_percent": None, "temperature": None}

    # 각 지표를 독립적으로 조회해 부분 실패를 허용
    gpu_percent: float | None = None
    memory_percent: float | None = None
    temperature: float | None = None

    try:
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        gpu_percent = float(util.gpu)
    except Exception:
        gpu_percent = None

    try:
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        if mem.total > 0:
            memory_percent = mem.used / mem.total * 100
        # mem.total == 0 이면 memory_percent는 None 유지 (0 오보고 방지)
    except Exception:
        memory_percent = None

    try:
        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        temperature = float(temp)
    except Exception:
        temperature = None

    return {
        "percent": gpu_percent,
        "memory_percent": memory_percent,
        "temperature": temperature,
    }


def collect_metrics() -> SystemMetrics:
    """전체 시스템 메트릭 수집"""
    mem = get_memory_info()
    disk = get_disk_info()
    gpu = get_gpu_info()

    return SystemMetrics(
        cpu_percent=get_cpu_percent(),
        memory_percent=mem["percent"],
        memory_used_mb=mem["used_mb"],
        memory_total_mb=mem["total_mb"],
        disk_percent=disk["percent"],
        disk_used_gb=disk["used_gb"],
        disk_total_gb=disk["total_gb"],
        gpu_percent=gpu["percent"],
        gpu_memory_percent=gpu["memory_percent"],
        gpu_temperature=gpu["temperature"],
    )
