"""
시스템 리소스 모니터링 모듈

CPU, RAM, GPU, 디스크 사용량 수집 및 헬스체크 정보 제공.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

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
    gpu_percent: Optional[float] = None
    gpu_memory_percent: Optional[float] = None
    gpu_temperature: Optional[float] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
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
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.split()
            total = sum(int(x) for x in parts[1:])
            idle = int(parts[4])
            return 100.0 * (1 - idle / total) if total > 0 else 0.0
        except Exception:
            return 0.0


def get_memory_info() -> Dict[str, float]:
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
            with open("/proc/meminfo", "r") as f:
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


def get_disk_info(path: str = "/") -> Dict[str, float]:
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


def get_gpu_info() -> Dict[str, Optional[float]]:
    """GPU 정보 조회 (pynvml 사용)"""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        try:
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            temp = None
        return {
            "percent": util.gpu,
            "memory_percent": (mem.used / mem.total * 100) if mem.total > 0 else 0,
            "temperature": float(temp) if temp is not None else None,
        }
    except Exception:
        return {"percent": None, "memory_percent": None, "temperature": None}


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
