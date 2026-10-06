"""BlendMotion 3.3 legacy wire format and the local GTA camera transport.

SPDX-License-Identifier: GPL-3.0-only
Wire reference: skettchdev/BlendMotion, public addon v3.3.2.
"""
import math
import secrets
import struct
import time
from dataclasses import dataclass

LOCAL = struct.Struct("<4sIIIIQd12f3fI")
LIVE, RECORD, PLAYBACK = 1, 2, 4


def valid_matrix(m):
    if len(m) != 12 or not all(math.isfinite(v) and abs(v) <= 1000 for v in m):
        return False
    x, y, z = m[0:3], m[3:6], m[6:9]
    dot = lambda a, b: sum(u * v for u, v in zip(a, b))
    cross = (x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0])
    return (all(abs(dot(v, v)-1) <= .02 for v in (x, y, z))
            and all(abs(dot(a, b)) <= .02 for a, b in ((x, y), (x, z), (y, z)))
            and dot(cross, z) >= .98)


@dataclass(frozen=True)
class Pose:
    matrix: tuple
    recording: bool
    timestamp: float | None


def pose_packet(data):
    if len(data) not in (51, 59) or data[:2] != b"BM" or data[50] > 1:
        return None
    m = struct.unpack_from("<12f", data, 2)
    if not valid_matrix(m):
        return None
    stamp = struct.unpack_from("<d", data, 51)[0] if len(data) == 59 else None
    if stamp is not None and (not math.isfinite(stamp) or not 0 < stamp <= 1e9):
        return None
    return Pose(m, bool(data[50]), stamp)


def command_packet(data):
    if not 3 <= len(data) <= 130 or not data.startswith(b"BM:"):
        return None
    try:
        command = data[3:].decode("utf-8", errors="strict")
    except UnicodeError:
        return None
    if any(ord(c) < 32 or ord(c) > 126 for c in command):
        return None
    return command


def preview(jpeg, width, height):
    if not (0 < width <= 1920 and 0 < height <= 1920 and 0 < len(jpeg) <= 4_000_000):
        raise ValueError("invalid preview dimensions or size")
    return struct.pack("<2sHHI", b"BP", width, height, len(jpeg)) + jpeg


def notification(message, kind=1):
    encoded = message.encode("utf-8")
    while len(encoded) > 255:
        message = message[:-1]
        encoded = message.encode("utf-8")
    return struct.pack("<2sBB", b"BN", kind, len(encoded)) + encoded


def timeline(status):
    fps = int(status.get("fps", 30))
    lo, hi, current = (float(status.get(k, 0)) for k in ("lo_ms", "hi_ms", "time_ms"))
    if fps not in (24, 25, 30, 60) or not all(math.isfinite(v) for v in (lo, hi, current)):
        return None
    end = min(10_000_000, max(1, round((hi-lo)*fps/1000)+1))
    frame = min(end, max(1, round((current-lo)*fps/1000)+1))
    return struct.pack("<2siii", b"BF", frame, 1, end)


class State:
    def __init__(self, cutoff=6):
        self.session = secrets.randbits(32)
        self.sequence = 0
        self.calibration = 0
        self.flags = LIVE
        self.scale, self.cutoff, self.lens, self.fps = 1., cutoff, 35., 30
        self.last_pose = None
        self.received_at = 0.
        self.last_timestamp = None
        self.rec_bit = False
        self.block_record = False

    def ingest(self, pose, now=None):
        now = time.perf_counter() if now is None else now
        # ARKit time may reset when the app starts another session after a gap.
        if (pose.timestamp is not None and self.last_timestamp is not None
                and pose.timestamp <= self.last_timestamp and now-self.received_at < 2):
            return False
        if self.received_at and now-self.received_at > .5:
            self.calibration += 1
            self.flags = LIVE
            self.block_record = pose.recording
        self.last_timestamp = pose.timestamp
        self.last_pose, self.received_at = pose, now
        if not pose.recording:
            self.block_record = False
        if pose.recording and not self.rec_bit and not self.block_record:
            self.flags = LIVE | RECORD
        elif not pose.recording and self.rec_bit:
            self.flags = PLAYBACK
        self.rec_bit = pose.recording
        return True

    def encode(self, now=None):
        now = time.perf_counter() if now is None else now
        # Never restamp an old tracking pose to keep the game receiver alive.
        if self.last_pose is None or now-self.received_at > .5:
            return None
        self.sequence += 1
        return LOCAL.pack(b"PCAM", 1, self.session, self.flags, self.calibration,
                          self.sequence, self.received_at, *self.last_pose.matrix,
                          self.scale, self.cutoff, self.lens, self.fps)

    def command(self, text):
        if text == "RCAL":
            self.calibration += 1
            self.flags = LIVE
            self.block_record = self.rec_bit
        elif text in ("PAUSE", "END"):
            self.flags = PLAYBACK if text == "END" else 0
            self.block_record = self.rec_bit
        elif text == "RESUME":
            self.calibration += 1
            self.flags = LIVE
            self.block_record = self.rec_bit
        elif text in ("PREV:ON", "PREV:OFF", "TREC"):
            # rec byte is authoritative: TREC + changed byte must not toggle twice.
            pass
        elif text.startswith(("PSCL:", "LENS:", "RFPS:")):
            key, raw = text.split(":", 1)
            try:
                value = float(raw)
            except ValueError:
                return "Invalid camera setting"
            if not math.isfinite(value):
                return "Invalid camera setting"
            if key == "PSCL" and .1 <= value <= 12:
                self.scale = value
            elif key == "LENS" and 5 <= value <= 600:
                self.lens = value
            elif key == "RFPS" and value in (24, 25, 30, 60):
                self.fps = int(value)
            else:
                return "Camera setting out of range"
        else:
            return "Use Rockstar Editor for seek/retake/focus; this control is not supported"
        return None
