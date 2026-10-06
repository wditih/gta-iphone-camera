"""Windows GTA -> iPhone preview bridge. SPDX-License-Identifier: GPL-3.0-only"""
import argparse
import asyncio
import ctypes
import io
import json
import logging
import math
import os
import socket
import sys
import threading
import time
from pathlib import Path

from protocol import State, command_packet, notification, pose_packet, preview, timeline


class GameCapture:
    """Capture only the visible foreground GTA5.exe client area, never a monitor."""
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Game preview requires Windows")
        from ctypes import wintypes
        self.w = wintypes
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.IsIconic.argtypes = [wintypes.HWND]
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            self.user.SetProcessDPIAware()
        self.local = threading.local()

    def capture(self):
        from PIL import Image
        import mss
        window = self.user.GetForegroundWindow()
        if not window or self.user.IsIconic(window):
            return None
        pid = self.w.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            return None
        try:
            size = self.w.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                return None
            if Path(path.value).name.lower() != "gta5.exe":
                return None
        finally:
            self.kernel.CloseHandle(process)
        rect, point = self.w.RECT(), self.w.POINT()
        if not self.user.GetClientRect(window, ctypes.byref(rect)) or not self.user.ClientToScreen(window, ctypes.byref(point)):
            return None
        width, height = rect.right-rect.left, rect.bottom-rect.top
        if width < 160 or height < 90 or width > 16384 or height > 16384:
            return None
        if not hasattr(self.local, "mss"):
            self.local.mss = mss.mss()
        shot = self.local.mss.grab(dict(left=point.x, top=point.y, width=width, height=height))
        if self.user.GetForegroundWindow() != window:
            return None
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        image.thumbnail((640, 640))
        for quality in (65, 50, 35, 20):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality)
            packet = preview(buffer.getvalue(), *image.size)
            if len(packet) <= 60000:
                return packet
        return None


class DemoCapture:
    def capture(self):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (640, 360), (14, 24, 36))
        draw = ImageDraw.Draw(image)
        x = int((math.sin(time.perf_counter())*.4+.5)*640)
        draw.ellipse((x-20, 160, x+20, 200), fill=(50, 210, 160))
        draw.text((20, 20), "PHONE CAMERA TRANSPORT TEST / NO GAME", fill="white")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=65)
        return preview(buffer.getvalue(), 640, 360)


class Bridge(asyncio.DatagramProtocol):
    def __init__(self, args, capture):
        self.args, self.capture = args, capture
        self.state = State(args.cutoff)
        self.transport = None
        self.peer = None
        self.clients = set()
        self.status_at = 0.
        self.status = {}
        self.notify_at = {}
        self.preview_enabled = True
        self.messages = []
        self.last_print = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        now = time.perf_counter()
        if addr == ("127.0.0.1", self.args.native_port) and data.startswith(b"PSTS"):
            try:
                status = json.loads(data[4:])
                if not isinstance(status, dict):
                    return
                packet = timeline(status)
            except (ValueError, TypeError, OverflowError):
                return
            if status.get("saved", 0) > self.status.get("saved", 0):
                self.notice("Camera take saved. Replay it in Rockstar Editor before exporting.")
            self.status, self.status_at = status, now
            if packet:
                self.messages.append(packet)
                self.messages = self.messages[-8:]
            if status.get("error"):
                self.notice(str(status["error"]), 2)
            key = (status.get("mode"), status.get("recording"), status.get("saved"), status.get("error"))
            if key != self.last_print:
                self.last_print = key
                print("GTA:", json.dumps(status, ensure_ascii=False), flush=True)
            return
        pose = pose_packet(data)
        # Pair with the first valid pose, restrict controls and previews to that IP.
        if pose is not None:
            if self.peer != addr[0]:
                if self.peer and now-self.state.received_at < 2:
                    return
                self.peer = addr[0]
                self.state = State(self.args.cutoff)
                print(f"iPhone connected: {self.peer}", flush=True)
                self.notice("GTA camera connected. Open a named project in Rockstar Editor.")
            if self.state.ingest(pose, now):
                self.forward()
            return
        if not self.peer or addr[0] != self.peer:
            return
        command = command_packet(data)
        if command:
            if command.startswith("PREV:"):
                self.preview_enabled = command == "PREV:ON"
            message = self.state.command(command)
            if message:
                self.notice(message, 2)
            self.forward()

    def forward(self):
        packet = self.state.encode()
        if packet:
            self.transport.sendto(packet, ("127.0.0.1", self.args.native_port))

    def notice(self, message, kind=1):
        now = time.perf_counter()
        if now-self.notify_at.get(message, -100) < 5:
            return
        self.notify_at[message] = now
        print(message, flush=True)
        self.messages.append(notification(message, kind))
        self.messages = self.messages[-8:]

    async def websocket(self, client):
        remote = client.remote_address
        self.clients.add(client)
        try:
            async for _ in client:
                # Commands and pose use UDP. Limit inbound WS traffic in serve().
                if self.peer and remote[0] != self.peer:
                    await client.close(code=1008, reason="Unpaired device")
        finally:
            self.clients.discard(client)

    async def send(self, packet):
        if not self.peer:
            return
        clients = [c for c in self.clients if c.remote_address and c.remote_address[0] == self.peer]
        if clients:
            async def deliver(client):
                try:
                    await asyncio.wait_for(client.send(packet), timeout=.2)
                except Exception:
                    await client.close()
            await asyncio.gather(*(deliver(c) for c in clients))
        elif len(packet) <= 60000:
            self.transport.sendto(packet, (self.peer, self.args.preview_port))

    async def run_preview(self):
        interval = 1/self.args.preview_fps
        while True:
            begin = time.perf_counter()
            if self.peer and begin-self.state.received_at < 2:
                if begin-self.status_at > 2:
                    self.notice("GTA module is not responding. Open Rockstar Editor; check ASI install.", 2)
                for packet in self.messages:
                    await self.send(packet)
                self.messages.clear()
                if self.preview_enabled:
                    try:
                        frame = await asyncio.to_thread(self.capture.capture)
                    except Exception as error:
                        self.notice(f"Preview unavailable: {type(error).__name__}", 2)
                        frame = None
                    if frame:
                        await self.send(frame)
                    else:
                        self.notice("Preview paused: keep GTA5.exe foreground in borderless/windowed mode.", 2)
            await asyncio.sleep(max(.001, interval-(time.perf_counter()-begin)))


async def main(args):
    from websockets.asyncio.server import serve
    capture = DemoCapture() if args.demo else GameCapture()
    bridge = Bridge(args, capture)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: bridge, local_addr=(args.bind, args.pose_port))
    try:
        async with serve(bridge.websocket, args.bind, args.preview_port, compression=None,
                         max_size=4096, max_queue=2, write_limit=65536, open_timeout=5):
            print(f"Ready: UDP {args.pose_port}, preview TCP/UDP {args.preview_port}.", flush=True)
            print("Connect iPhone and PC to the same private Wi-Fi. In BlendMotion enter this PC's LAN IPv4.", flush=True)
            for ip in sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}):
                print("PC address:", ip, flush=True)
            print("Ctrl+C exits. Demo:", args.demo, flush=True)
            await bridge.run_preview()
    finally:
        transport.close()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--pose-port", type=int, default=9090)
    parser.add_argument("--preview-port", type=int, default=9091)
    parser.add_argument("--native-port", type=int, default=9092)
    parser.add_argument("--preview-fps", type=int, choices=range(1, 31), default=15)
    parser.add_argument("--cutoff", type=float, default=6.)
    parser.add_argument("--demo", action="store_true", help="Synthetic image only; no screen capture")
    result = parser.parse_args()
    if not math.isfinite(result.cutoff) or not 0 <= result.cutoff <= 120:
        parser.error("cutoff must be 0..120")
    if any(not 1024 <= n <= 65535 for n in (result.pose_port, result.preview_port, result.native_port)):
        parser.error("ports must be 1024..65535")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    try:
        asyncio.run(main(arguments()))
    except KeyboardInterrupt:
        print("Bridge stopped.")
    except Exception as error:
        print(f"Bridge failed: {error}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            input("Press Enter to close.")
        raise SystemExit(1)
