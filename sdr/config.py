"""Minimal config dataclass for the SDR source.

Trimmed down from GrunnAlert's config.py: this standalone tool takes its
settings from command-line flags (see cli.py), not a JSON config file, so
only the dataclass rtl_source.py actually needs is kept here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SdrConfig:
    device_index: int
    device_serial: str
    frequency_hz: int
    sample_rate_hz: int
    resample_rate_hz: int
    gain_db: str  # "auto" or a numeric string, e.g. "20.7"
    ppm_correction: int
    squelch_level: int
    rtl_fm_path: Path
