"""Camera-scoped object tracking."""

from .bytetrack import ByteTrackManager
from .line_crossing import LineCrossingCandidate, LineCrossingMonitor
from .loitering import LoiteringCandidate, LoiteringMonitor
from .models import TrackSnapshot

__all__ = [
    "ByteTrackManager",
    "LineCrossingCandidate",
    "LineCrossingMonitor",
    "LoiteringCandidate",
    "LoiteringMonitor",
    "TrackSnapshot",
]
