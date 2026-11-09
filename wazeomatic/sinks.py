"""
Module Name: sinks.py
Description: Delivers hazard events once the pipeline trusts them: to stdout, a JSONL log, the phone over TCP, adb broadcast or HTTP

Class Name: StdoutSink
Description: Prints each event as one JSON line

Class Name: JsonlSink
Description: Appends each event to a JSONL log file

Class Name: TcpSink
Description: Sends newline-delimited JSON to the phone app over TCP and reconnects after failures without raising

Class Name: AdbBroadcastSink
Description: Sends each event as a broadcast Intent to an emulator or phone over adb

Class Name: HttpSink
Description: Posts each event to a webhook or local HTTP endpoint

Inputs: HazardEvents from the pipeline and sink specs from the command line
Outputs: Event JSON delivered to the phone, a log file, the console or a webhook
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the transports that carry detections from the Raspberry Pi or desktop to the phone
How you validated and revised the AI output: Proofreading, and the pytest unit tests in tests/ run on the desktop (tests/test_sinks.py checks the adb command, JSONL output, a TCP round trip and that a down TCP link never raises)
The challenges or limitations you faced while using AI: Keeping the pipeline running when the phone connection drops and reconnecting on its own
Authors: Blake Pennel
Creation Date: 11/5/2026
"""
from __future__ import annotations

"""Where HazardEvents go once the pipeline trusts them.

* ``StdoutSink`` / ``JsonlSink`` - for logs and tests.
* ``TcpSink`` - newline-delimited JSON to the phone app (port 8765). This is the
  Raspberry Pi transport: the app turns each line into the same broadcast Intent it
  would send for its own camera detections.
* ``AdbBroadcastSink`` - sends the Intent directly to an emulator/phone over adb,
  which lets the desktop simulator drive MacroDroid or the mock Waze app with no
  detector app installed.
* ``HttpSink`` - MacroDroid webhook fallback (needs internet) or any local HTTP endpoint.
"""

import json
import shlex
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from .schema import INTENT_ACTION, HazardEvent


class StdoutSink:
    def send(self, ev: HazardEvent) -> None:
        sys.stdout.write(ev.to_json() + "\n")
        sys.stdout.flush()

    def close(self) -> None:
        pass


class JsonlSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")

    def send(self, ev: HazardEvent) -> None:
        self._f.write(ev.to_json() + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class TcpSink:
    """Persistent TCP connection, reconnects on failure, never raises on send."""

    def __init__(self, host: str, port: int = 8765, timeout: float = 2.0, retry_s: float = 3.0):
        self.host, self.port, self.timeout, self.retry_s = host, port, timeout, retry_s
        self._sock: Optional[socket.socket] = None
        self._next_try = 0.0
        self.sent = 0
        self.dropped = 0

    def _connect(self) -> bool:
        if self._sock is not None:
            return True
        if time.time() < self._next_try:
            return False
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            return True
        except OSError as e:
            print(f"[tcp] connect {self.host}:{self.port} failed: {e}", file=sys.stderr)
            self._next_try = time.time() + self.retry_s
            return False

    def send(self, ev: HazardEvent) -> None:
        if not self._connect():
            self.dropped += 1
            return
        try:
            assert self._sock is not None
            self._sock.sendall((ev.to_json() + "\n").encode("utf-8"))
            self.sent += 1
        except OSError as e:
            print(f"[tcp] send failed: {e}", file=sys.stderr)
            self.dropped += 1
            self.close()
            self._next_try = time.time() + self.retry_s

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


class AdbBroadcastSink:
    """``adb shell am broadcast -a com.wazeomatic.HAZARD_DETECTED --es hazard_type ...``"""

    def __init__(self, adb: str = "adb", serial: Optional[str] = None, package: Optional[str] = None,
                 dry_run: bool = False):
        self.adb, self.serial, self.package, self.dry_run = adb, serial, package, dry_run

    def command(self, ev: HazardEvent) -> list[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["shell", "am", "broadcast", "-a", INTENT_ACTION, "-f", "0x00000020"]  # FLAG_INCLUDE_STOPPED_PACKAGES
        if self.package:
            cmd += ["-p", self.package]
        for k, v in ev.intent_extras().items():
            cmd += ["--es", k, v]
        cmd += ["--es", "dry_run", "true" if self.dry_run else "false"]
        return cmd

    def send(self, ev: HazardEvent) -> None:
        cmd = self.command(ev)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                print(f"[adb] broadcast failed: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"[adb] {shlex.join(cmd)} -> {e}", file=sys.stderr)

    def close(self) -> None:
        pass


class HttpSink:
    """POST the event JSON, or GET a MacroDroid webhook URL with the extras as query params."""

    def __init__(self, url: str, method: str = "POST", timeout: float = 5.0):
        self.url, self.method, self.timeout = url, method.upper(), timeout

    def send(self, ev: HazardEvent) -> None:
        try:
            if self.method == "GET":
                q = urllib.parse.urlencode(ev.intent_extras())
                sep = "&" if "?" in self.url else "?"
                req = urllib.request.Request(self.url + sep + q, method="GET")
            else:
                req = urllib.request.Request(self.url, data=ev.to_json().encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=self.timeout).read()
        except Exception as e:  # noqa: BLE001 - never let a sink kill the pipeline
            print(f"[http] {self.url}: {e}", file=sys.stderr)

    def close(self) -> None:
        pass


def build_sinks(specs: list[str], **kw) -> list:
    """Parse CLI sink specs: stdout | jsonl:<path> | tcp:<host>[:port] | adb[:<serial>] | http:<url>"""
    sinks = []
    for s in specs:
        kind, _, arg = s.partition(":")
        if kind == "stdout":
            sinks.append(StdoutSink())
        elif kind == "jsonl":
            sinks.append(JsonlSink(arg or "runs/events.jsonl"))
        elif kind == "tcp":
            host, _, port = arg.partition(":")
            sinks.append(TcpSink(host or "127.0.0.1", int(port or 8765)))
        elif kind == "adb":
            sinks.append(AdbBroadcastSink(adb=kw.get("adb", "adb"), serial=arg or None,
                                          package=kw.get("package"), dry_run=kw.get("dry_run", False)))
        elif kind in ("http", "https"):
            sinks.append(HttpSink(s, method=kw.get("http_method", "POST")))
        else:
            raise ValueError(f"unknown sink spec: {s}")
    return sinks
