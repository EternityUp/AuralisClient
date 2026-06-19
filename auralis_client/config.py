from __future__ import annotations

from dataclasses import dataclass


CLIENT_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class AudioDeviceSelection:
    input_device: int
    output_device: int
