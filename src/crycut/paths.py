"""Locations in this repository; user projects stay beside the source tree."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRINTER_CONFIG = ROOT / "config/printer.json"
PLACEMENT_PROFILE = ROOT / "config/profiles/stickerpix-v2.json"
TIMING_MODEL = ROOT / "config/timing.json"
