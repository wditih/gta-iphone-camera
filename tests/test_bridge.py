# SPDX-License-Identifier: GPL-3.0-only
import argparse
import asyncio
import io
import json
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from protocol import LOCAL
from phone_bridge import Bridge, DemoCapture
from websockets.asyncio.server import serve
from websockets.asyncio.client import connect
from PIL import Image

class Native(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue()
    def connection_made(self, transport):
        self.transport = transport
    def datagram_received(self, data, addr):
        self.queue.put_nowait(LOCAL.unpack(data))
        status = dict(mode="live",recording=False,time_ms=500,lo_ms=0,hi_ms=1000,fps=30)
        self.transport.sendto(b"PSTS"+json.dumps(status).encode(), addr)

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pose_to_native_and_jpeg_to_phone_over_real_sockets(self):
        loop = asyncio.get_running_loop()
        native = Native()
        nt, _ = await loop.create_datagram_endpoint(lambda: native, local_addr=("127.0.0.1",0))
        args = argparse.Namespace(cutoff=6.,native_port=nt.get_extra_info("sockname")[1],
                                  preview_port=0,preview_fps=15)
        bridge = Bridge(args, DemoCapture())
        bt, _ = await loop.create_datagram_endpoint(lambda: bridge, local_addr=("127.0.0.1",0))
        pt, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=bt.get_extra_info("sockname"))
        task = None
        try:
            async with serve(bridge.websocket, "127.0.0.1", 0, compression=None) as server:
                port = server.sockets[0].getsockname()[1]
                args.preview_port = port
                async with connect(f"ws://127.0.0.1:{port}", compression=None) as phone:
                    m = (1.,0.,0.,0.,1.,0.,0.,0.,1.,2.,3.,4.)
                    pt.sendto(b"BM"+struct.pack("<12fBd", *m, 0, 10.))
                    wire = await asyncio.wait_for(native.queue.get(), 2)
                    self.assertEqual(wire[7:19], m)
                    task = asyncio.create_task(bridge.run_preview())
                    kinds = set()
                    for _ in range(8):
                        data = await asyncio.wait_for(phone.recv(), 2)
                        self.assertIsInstance(data, bytes)
                        kinds.add(data[:2])
                        if data[:2] == b"BP":
                            magic, w, h, length = struct.unpack("<2sHHI", data[:10])
                            self.assertEqual(length, len(data)-10)
                            image = Image.open(io.BytesIO(data[10:]))
                            image.verify()
                            self.assertEqual((w,h), (640,360))
                        if {b"BP",b"BF",b"BN"} <= kinds:
                            break
                    self.assertTrue({b"BP",b"BF",b"BN"} <= kinds)
                    self.assertEqual(bridge.peer,"127.0.0.1")
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            pt.close(); bt.close(); nt.close()

if __name__ == "__main__":
    unittest.main()
