# SPDX-License-Identifier: GPL-3.0-only
import math
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from protocol import State, LOCAL, LIVE, RECORD, PLAYBACK, command_packet, notification, pose_packet, preview, timeline

MATRIX = (1.,0.,0.,0.,1.,0.,0.,0.,1.,2.,3.,4.)

def wire(rec=0, stamp=10., m=MATRIX):
    base = b"BM" + struct.pack("<12fB", *m, rec)
    return base if stamp is None else base+struct.pack("<d", stamp)

class ProtocolTests(unittest.TestCase):
    def test_actual_legacy_formats(self):
        for stamp in (None, 10.):
            p = pose_packet(wire(1, stamp))
            self.assertEqual(p.matrix, MATRIX)
            self.assertTrue(p.recording)
            self.assertEqual(p.timestamp, stamp)

    def test_bad_poses_rejected(self):
        for raw in (b"BM", wire()+b"x", wire(2), wire(stamp=float("nan")),
                    wire(m=(float("inf"),)+MATRIX[1:]), wire(m=(-1.,)+MATRIX[1:])):
            self.assertIsNone(pose_packet(raw))

    def test_timestamp_order_and_restart(self):
        s = State()
        self.assertTrue(s.ingest(pose_packet(wire()), 20.))
        self.assertFalse(s.ingest(pose_packet(wire(stamp=9.)), 20.1))
        self.assertTrue(s.ingest(pose_packet(wire(stamp=1.)), 23.))
        self.assertEqual(s.calibration, 1)

    def test_record_button_does_not_double_toggle(self):
        s = State()
        s.ingest(pose_packet(wire(0)), 20.)
        s.command("TREC")
        s.ingest(pose_packet(wire(1, 11.)), 20.1)
        self.assertEqual(s.flags, LIVE | RECORD)
        s.command("TREC")
        s.ingest(pose_packet(wire(0, 12.)), 20.2)
        self.assertEqual(s.flags, PLAYBACK)

    def test_disconnect_does_not_keep_or_resume_recording(self):
        s = State()
        s.ingest(pose_packet(wire(1)), 20.)
        self.assertIsNone(s.encode(20.6))
        s.ingest(pose_packet(wire(1, 11.)), 21.)
        self.assertEqual(s.flags, LIVE)
        s.ingest(pose_packet(wire(0, 12.)), 21.1)
        s.ingest(pose_packet(wire(1, 13.)), 21.2)
        self.assertEqual(s.flags, LIVE | RECORD)

    def test_wire_matches_native_and_does_not_restamp_pose(self):
        s = State()
        s.ingest(pose_packet(wire()), 20.)
        a = LOCAL.unpack(s.encode(20.1))
        b = LOCAL.unpack(s.encode(20.2))
        self.assertEqual(LOCAL.size, 100)
        self.assertEqual(a[:2], (b"PCAM", 1))
        self.assertEqual(a[6], 20.)
        self.assertEqual(b[6], 20.)
        self.assertEqual(b[5], a[5]+1)
        self.assertEqual(a[7:19], MATRIX)

    def test_controls_are_bounded(self):
        s = State()
        for bad in ("LENS:nan", "PSCL:inf", "RFPS:29.97", "LENS:601", "PSCL:0", "SEEK:12"):
            self.assertIsNotNone(s.command(bad))
        for ok in ("LENS:50", "PSCL:2", "RFPS:60"):
            self.assertIsNone(s.command(ok))
        self.assertEqual((s.scale, s.lens, s.fps), (2., 50., 60))

    def test_pause_recenter_does_not_resume_old_record_flag(self):
        s = State()
        s.ingest(pose_packet(wire(1)), 20.)
        s.command("RCAL")
        s.ingest(pose_packet(wire(1, 11.)), 20.1)
        self.assertEqual(s.flags, LIVE)

    def test_preview_timeline_notification(self):
        jpeg = b"\xff\xd8test\xff\xd9"
        p = preview(jpeg, 640, 360)
        self.assertEqual(struct.unpack("<2sHHI", p[:10]), (b"BP",640,360,len(jpeg)))
        self.assertEqual(p[10:], jpeg)
        p = timeline(dict(fps=30,lo_ms=1000,hi_ms=2000,time_ms=1500))
        self.assertEqual(struct.unpack("<2siii", p), (b"BF",16,1,31))
        self.assertIsNone(timeline(dict(fps=30,time_ms=float("nan"))))
        n = notification("я"*200)
        self.assertLessEqual(len(n), 259)
        n[4:].decode("utf8")
        self.assertEqual(n[3],len(n)-4)

    def test_command_validation(self):
        self.assertEqual(command_packet(b"BM:RCAL"), "RCAL")
        for raw in (b"BM:\xff", b"BM:\nRCAL", b"BM:"+b"a"*128, b"XX:RCAL"):
            self.assertIsNone(command_packet(raw))

if __name__ == "__main__":
    unittest.main()
