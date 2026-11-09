"""
Module Name: schema.py
Description: Shared hazard taxonomy and message format: maps model classes to Waze report types and defines the JSON event sent to the phone

Class Name: HazardType
Description: One reportable hazard and the Waze report menu category and subcategory it maps to

Class Name: Detection
Description: One model detection: label, confidence, normalized box and the model that produced it

Class Name: HazardEvent
Description: A confirmed hazard sent to the phone, with hazard type, confidence, source and timestamp, serialized to JSON and Intent extras

Inputs: Model class labels and confirmed detections from the report manager
Outputs: JSON event lines and broadcast Intent extras with hazard category, confidence score and timestamp
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to define the hazard to Waze menu mapping and the event JSON so the desktop pipeline, the Android app and the macro share one contract
How you validated and revised the AI output: Proofreading, tests/test_report_manager.py checking the event JSON and Intent extras, and the schema check in tools/emulator/run_e2e.py against the Kotlin mirror
The challenges or limitations you faced while using AI: Keeping the hazard_type strings identical across Python, Kotlin and the macro, and matching them to the current Waze menu labels
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""Hazard taxonomy shared by the desktop pipeline, the Android app and the macro.

The ``hazard_type`` strings are the contract with the MacroDroid macro: the macro
switches on this value to decide which Waze menu path to tap. Keep them stable.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

SCHEMA_VERSION = 1

# Broadcast action the Android app sends and MacroDroid listens for.
INTENT_ACTION = "com.wazeomatic.HAZARD_DETECTED"


@dataclass(frozen=True)
class HazardType:
    """One reportable thing, mapped onto Waze's report menu."""

    key: str              # value of the ``hazard_type`` variable the macro reads
    waze_category: str    # top-level Waze report tile
    waze_subcategory: str # second-level choice inside that tile ("" if none)
    description: str


# Waze report menu (Android, 2024 redesign; see docs/pipeline-to-macro.md). Under "Hazard"
# Waze offers a flat list of subtypes: Construction, Car on shoulder, Broken traffic light,
# Pothole, Object. Animals/roadkill subtypes were removed, so animals report as a plain
# Hazard with no subtype. Keys are kept stable because the macro switches on them.
HAZARD_TYPES: dict[str, HazardType] = {
    h.key: h
    for h in [
        HazardType("HAZARD_ON_ROAD_POTHOLE", "Hazard", "Pothole", "Pothole / road damage"),
        HazardType("HAZARD_ON_ROAD_CONSTRUCTION", "Hazard", "Construction", "Cones, work-zone signs, delineators"),
        HazardType("HAZARD_ON_ROAD_OBJECT", "Hazard", "Object", "Debris or object on the road"),
        HazardType("HAZARD_SHOULDER_CAR_STOPPED", "Hazard", "Car on shoulder", "Vehicle stopped on the shoulder"),
        HazardType("HAZARD_SHOULDER_ANIMALS", "Hazard", "", "Animal on or near the road (generic Hazard)"),
        HazardType("POLICE", "Police", "", "Police vehicle visible"),
        HazardType("CRASH", "Crash", "", "Crash / collision"),
    ]
}

# Model class name -> hazard key. Model classes come from configs/classes.yaml.
DEFAULT_CLASS_TO_HAZARD: dict[str, str] = {
    # custom hazard model
    "pothole": "HAZARD_ON_ROAD_POTHOLE",
    "cone": "HAZARD_ON_ROAD_CONSTRUCTION",
    "construction_sign": "HAZARD_ON_ROAD_CONSTRUCTION",
    "delineator": "HAZARD_ON_ROAD_CONSTRUCTION",
    "barricade": "HAZARD_ON_ROAD_CONSTRUCTION",
    "worker": "HAZARD_ON_ROAD_CONSTRUCTION",
    "police_vehicle": "POLICE",
    # COCO classes used from the stock detector
    "dog": "HAZARD_SHOULDER_ANIMALS",
    "horse": "HAZARD_SHOULDER_ANIMALS",
    "sheep": "HAZARD_SHOULDER_ANIMALS",
    "cow": "HAZARD_SHOULDER_ANIMALS",
    "bear": "HAZARD_SHOULDER_ANIMALS",
    # "car"/"truck"/"bus" are NOT mapped directly: they only become a hazard through
    # the stopped-vehicle heuristic in report_manager.py.
}


@dataclass
class Detection:
    """A single box from one inference pass. bbox is normalized xywh (center)."""

    label: str
    confidence: float
    bbox: tuple[float, float, float, float]
    model: str = "hazard"


@dataclass
class HazardEvent:
    """What the pipeline emits once a detection is trusted enough to report."""

    hazard_type: str
    confidence: float
    source: str = "sim"            # "phone" | "pi" | "sim"
    ts: float = field(default_factory=time.time)
    frame_index: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    label: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    schema: int = SCHEMA_VERSION

    @property
    def waze(self) -> HazardType:
        return HAZARD_TYPES[self.hazard_type]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["waze_category"] = self.waze.waze_category
        d["waze_subcategory"] = self.waze.waze_subcategory
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def intent_extras(self) -> dict[str, str]:
        """String extras for the Android broadcast (MacroDroid reads strings)."""
        return {
            "hazard_type": self.hazard_type,
            "confidence": f"{self.confidence:.3f}",
            "ts": f"{self.ts:.3f}",
            "source": self.source,
            "waze_category": self.waze.waze_category,
            "waze_subcategory": self.waze.waze_subcategory,
        }
