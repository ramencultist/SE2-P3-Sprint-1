"""
Module Name: test_sinks.py
Description: Unit tests for the event sinks that send detections to the phone

Inputs: Sample HazardEvents and a local TCP listener
Outputs: pytest pass or fail results
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write tests for the adb command shape, JSONL output, a TCP round trip and the down-link case
How you validated and revised the AI output: Proofreading, and running pytest against the sinks module
The challenges or limitations you faced while using AI: Testing network behaviour without a phone attached
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
import json
import socket
import threading

from wazeomatic.schema import INTENT_ACTION, HazardEvent
from wazeomatic.sinks import AdbBroadcastSink, JsonlSink, TcpSink, build_sinks


def ev():
    return HazardEvent(hazard_type="HAZARD_ON_ROAD_POTHOLE", confidence=0.91, source="sim", ts=1.5)


def test_adb_command_shape():
    cmd = AdbBroadcastSink(adb="adb", serial="emulator-5554", package="com.arlosoft.macrodroid", dry_run=True).command(ev())
    assert cmd[:4] == ["adb", "-s", "emulator-5554", "shell"]
    assert "-a" in cmd and cmd[cmd.index("-a") + 1] == INTENT_ACTION
    assert cmd[cmd.index("-p") + 1] == "com.arlosoft.macrodroid"
    es = {cmd[i + 1]: cmd[i + 2] for i, t in enumerate(cmd) if t == "--es"}
    assert es["hazard_type"] == "HAZARD_ON_ROAD_POTHOLE"
    assert es["dry_run"] == "true"
    assert es["waze_subcategory"] == "Pothole"


def test_jsonl_sink(tmp_path):
    p = tmp_path / "events.jsonl"
    s = JsonlSink(p)
    s.send(ev())
    s.close()
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert rows[0]["hazard_type"] == "HAZARD_ON_ROAD_POTHOLE"


def test_tcp_sink_roundtrip():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = []

    def accept():
        conn, _ = srv.accept()
        with conn:
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(4096)
            got.append(buf)

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    s = TcpSink("127.0.0.1", port)
    s.send(ev())
    t.join(timeout=3)
    s.close()
    srv.close()
    assert got and json.loads(got[0].decode().strip())["confidence"] == 0.91


def test_tcp_sink_never_raises_when_down():
    s = TcpSink("127.0.0.1", 1, retry_s=0)  # nothing listens on port 1
    s.send(ev())
    assert s.dropped == 1


def test_build_sinks_specs(tmp_path):
    sinks = build_sinks(["stdout", f"jsonl:{tmp_path / 'e.jsonl'}", "tcp:10.0.0.5:9000", "adb:emulator-5554"])
    assert [type(s).__name__ for s in sinks] == ["StdoutSink", "JsonlSink", "TcpSink", "AdbBroadcastSink"]
    assert sinks[2].port == 9000 and sinks[3].serial == "emulator-5554"
