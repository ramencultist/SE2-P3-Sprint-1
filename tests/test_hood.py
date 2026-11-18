"""
Module Name: test_hood.py
Description: Unit tests for hoodEstimator: learns a static dashboard band and ignores a moving scene

Inputs: Synthetic detections and frames
Outputs: pytest pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/6/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the unit tests alongside the module they cover
How you validated and revised the AI output: Proofreading, and running pytest
The challenges or limitations you faced while using AI: Making the tests deterministic without a model file
Authors: Blake Pennel
Creation Date: 11/6/2026

Ported to the SE2-P3 repository 11/17/2026 by Luke Reicherter with Claude Opus 5.5 for Sprint 2 (requirements 7 and 9); code unchanged from the team's wazeomatic development repository unless a Revision entry says otherwise
"""
import numpy as np

from wazeomatic.hood import HoodEstimator


def _frames(n, h=180, w=320, dash_frac=0.0, still=False, seed=0):
    """Synthetic drive: scrolling texture (road) with an optional static band at the bottom."""
    rng = np.random.default_rng(seed)
    texture = rng.integers(0, 255, size=(h * 3, w), dtype=np.uint8)
    dash = rng.integers(60, 120, size=(h, w), dtype=np.uint8)
    cut = int(h * (1 - dash_frac))
    for i in range(n):
        off = 0 if still else (i * 7) % (h * 2)
        img = texture[off:off + h].copy()
        if dash_frac > 0:
            img[cut:] = dash[cut:]
        yield np.repeat(img[:, :, None], 3, axis=2)


def test_estimator_finds_a_static_dashboard_band():
    est = HoodEstimator(min_frac=0.10)
    for fr in _frames(60, dash_frac=0.25):
        frac = est.update(fr)
    assert 0.20 <= frac <= 0.30


def test_estimator_never_drops_below_minimum_or_above_maximum():
    est = HoodEstimator(min_frac=0.10, max_frac=0.40)
    for fr in _frames(60, dash_frac=0.0):
        frac = est.update(fr)
    assert frac == 0.10
    est2 = HoodEstimator(min_frac=0.10, max_frac=0.30)
    for fr in _frames(60, dash_frac=0.4):
        frac2 = est2.update(fr)
    assert frac2 == 0.30


def test_estimator_waits_for_min_frames_and_ignores_standstill():
    est = HoodEstimator(min_frac=0.10, min_frames=30)
    for i, fr in enumerate(_frames(20, dash_frac=0.25)):
        assert est.update(fr) == 0.10
    # a still car (no scene motion) must not grow the zone
    est3 = HoodEstimator(min_frac=0.10)
    for fr in _frames(60, dash_frac=0.25, still=True):
        frac = est3.update(fr)
    assert frac == 0.10
