"""
Module Name: test_detector.py
Description: Unit tests for frame preprocessing and YOLO output decoding in wazeomatic/detector.py

Inputs: Synthetic frames and fake model outputs
Outputs: pytest pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write tests for letterbox geometry, input tensor layout, NMS and box mapping
How you validated and revised the AI output: Proofreading, and running pytest against the detector module
The challenges or limitations you faced while using AI: Building fake model outputs that exercise both output layouts without a real model
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
import numpy as np

from wazeomatic.detector import Letterbox, decode_yolo_output, letterbox, nms, to_input_tensor


def test_letterbox_geometry():
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    out, lb = letterbox(img, 416)
    assert out.shape == (416, 416, 3)
    assert abs(lb.scale - 416 / 1280) < 1e-6
    assert lb.pad_x == 0
    assert lb.pad_y == (416 - int(round(720 * lb.scale))) // 2
    # padding colour is the YOLO gray
    assert out[0, 0].tolist() == [114, 114, 114]


def test_input_tensor_layout():
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[..., 0] = 255  # blue channel in BGR
    x = to_input_tensor(img)
    assert x.shape == (1, 3, 4, 4) and x.dtype == np.float32
    assert x[0, 2].max() == 1.0 and x[0, 0].max() == 0.0  # BGR -> RGB swap


def test_nms_suppresses_overlaps():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.5) == [0, 2]


def test_decode_maps_back_to_source_frame():
    labels = ["pothole", "cone"]
    lb = Letterbox(scale=0.5, pad_x=0, pad_y=50, in_w=416, in_h=416, src_w=832, src_h=632)
    # one anchor: box centred at letterboxed (208, 208) size 100x60, class 1 with 0.9
    raw = np.zeros((1, 4 + len(labels), 3), dtype=np.float32)
    raw[0, :, 0] = [208, 208, 100, 60, 0.1, 0.9]
    raw[0, :, 1] = [208, 208, 100, 60, 0.1, 0.85]   # duplicate, should be NMS'd away
    raw[0, :, 2] = [10, 10, 5, 5, 0.2, 0.1]         # below threshold
    dets = decode_yolo_output(raw, lb, labels, conf_thres=0.5, iou_thres=0.5)
    assert len(dets) == 1
    d = dets[0]
    assert d.label == "cone" and abs(d.confidence - 0.9) < 1e-6
    cx, cy, w, h = d.bbox
    assert abs(cx - 0.5) < 1e-6                      # (208/0.5)/832
    assert abs(cy - ((208 - 50) / 0.5) / 632) < 1e-6
    assert abs(w - (100 / 0.5) / 832) < 1e-6
    assert abs(h - (60 / 0.5) / 632) < 1e-6


def test_decode_accepts_transposed_output():
    labels = ["a", "b", "c"]
    lb = Letterbox(1.0, 0, 0, 64, 64, 64, 64)
    raw = np.zeros((1, 5, 4 + len(labels)), dtype=np.float32)
    raw[0, 0] = [32, 32, 10, 10, 0.0, 0.0, 0.8]
    dets = decode_yolo_output(raw, lb, labels, 0.5, 0.5)
    assert [d.label for d in dets] == ["c"]
