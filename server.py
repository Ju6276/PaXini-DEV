#!/usr/bin/env python3
"""Local Paxini viewer. Tactile SDK + documented PX6D USB protocol."""
import argparse
import asyncio
import contextlib
import copy
import math
from pathlib import Path
import struct
import sys
import threading
import time

import serial
from aiohttp import web

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from paxini_sdk import HighSpeedHandBoard
from paxini_sdk import registers
from paxini_sdk.protocol import parse_auto_push
from paxini_sdk.boards.high_speed import AutoPushFrame


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (7 if value & 128 else 0)) & 255
    return value


def find_device_port(pattern, directory=Path('/dev/serial/by-id')):
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise ConnectionError('未检测到设备，请检查 USB 连接')
    if len(paths) > 1:
        raise ConnectionError('Multiple matching USB devices found. Connect one device of each type.')
    return str(paths[0])


class FTParser:
    """PX6D user manual §5.2: AA557F03 + Fx,Fy,Fz,Mx,My,Mz + CRC8.

    Six little-endian float32 values, in N and N·m. CRC-8/SMBUS matches
    the manual's command/response examples and live hardware captures.
    """
    HEAD = b'\xaa\x55\x7f\x03'
    START_ACK = bytes.fromhex('AA 55 7F 03 00 00 00 00 00 00 00 00 EC')

    def __init__(self, *, expect_start_ack=False):
        self.buffer = bytearray()
        self.invalid = 0
        self.expect_start_ack = expect_start_ack

    def feed(self, data):
        self.buffer.extend(data)
        values = []
        while True:
            index = self.buffer.find(self.HEAD)
            if index < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            del self.buffer[:index]
            # FW v1.1.0 acknowledges start with a 13-byte control frame using
            # the same header as data. Check the next data header as well to
            # avoid confusing a zero-valued data prefix with an acknowledgment.
            if (self.expect_start_ack and len(self.buffer) >= 17
                    and self.buffer.startswith(self.START_ACK)
                    and self.buffer[13:17] == self.HEAD):
                del self.buffer[:13]
                self.expect_start_ack = False
                continue
            if len(self.buffer) < 29:
                break
            frame = bytes(self.buffer[:29])
            if crc8(frame[:-1]) != frame[-1]:
                self.invalid += 1
                del self.buffer[0]
                continue
            decoded = struct.unpack('<6f', frame[4:28])
            del self.buffer[:29]
            if not all(math.isfinite(v) for v in decoded):
                self.invalid += 1
                continue
            self.expect_start_ack = False
            values.append(list(decoded))
        return values


class Device:
    def __init__(self, name, pattern, stop):
        self.name, self.pattern, self.stop = name, pattern, stop
        self.lock = threading.Lock()
        self.state = {'name': name, 'port': None, 'connected': False,
                      'error': '等待设备', 'frames': 0, 'rate': 0, 'invalid': 0,
                      'received_at': None, 'modules': [], 'values': None}
        self.last_rx = None
        self.rate_start, self.rate_frames = time.monotonic(), 0

    def update(self, **fields):
        with self.lock:
            self.state.update(fields)

    def received(self, **fields):
        now = time.monotonic()
        with self.lock:
            self.last_rx = now
            self.state.update(fields, connected=True, error='', received_at=time.time())
            self.state['frames'] += 1
            self.rate_frames += 1
            elapsed = now - self.rate_start
            if elapsed >= 1:
                self.state['rate'] = round(self.rate_frames / elapsed)
                self.rate_start, self.rate_frames = now, 0

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            age = time.monotonic() - self.last_rx if self.last_rx else None
            state['age_ms'] = round(age * 1000) if age is not None else None
            state['connected'] = bool(state['connected'] and age is not None and age < 0.5)
            if not state['connected']:
                state['rate'] = 0
                if not state['error']:
                    state['error'] = '等待新数据，旧数据已停止更新'
            return state

    def run(self):
        while not self.stop.is_set():
            try:
                port = find_device_port(self.pattern)
                self.update(port=port, error='正在读取设备')
                self.rate_start, self.rate_frames = time.monotonic(), 0
                self.collect(port)
            except Exception as exc:
                self.update(connected=False, error=str(exc), rate=0)
                self.stop.wait(1)


class Tactile(Device):
    def collect(self, port):
        hand = HighSpeedHandBoard(port, response_timeout=.8)
        hand.transport._kwargs['exclusive'] = True
        try:
            hand.open()
            firmware = hand.read_version()
            config = hand.read_auto_push_config()
            if not config.active_modules:
                raise ConnectionError('通讯板在线，但未检测到触觉模块')
            if not config.has_distributed or not config.has_resultant:
                raise ConnectionError('当前输出未同时启用合力和分布力，请用厂商工具检查配置')
            expected = config.expected_payload_size()
            known = {1: 135, 4: 150, 8: 150, 17: 135}
            missing = [m for m in known if m not in config.active_modules]
            warning = (f'槽位 {", ".join(map(str, missing))} 未检测到；正在显示其余 '
                       f'{len(config.active_modules)} 个模块。' if missing else '')
            self.update(firmware=firmware, expected_points=sum(config.point_counts.values()),
                        missing_slots=missing, known_point_counts=known, warning=warning)
            hand.enable_auto_push()
            hand.transport.ser.timeout = .1
            buffer = bytearray()
            last_frame = time.monotonic()
            while not self.stop.is_set():
                stream = hand.transport.ser
                buffer.extend(stream.read(max(1, min(stream.in_waiting, 65536))))
                while True:
                    index = buffer.find(b'\xaa\x56')
                    if index < 0:
                        buffer[:] = buffer[-1:]
                        break
                    del buffer[:index]
                    if len(buffer) < 6:
                        break
                    length = int.from_bytes(buffer[3:5], 'little') + 6
                    if length != expected + 7:
                        self.update(invalid=self.state['invalid'] + 1)
                        del buffer[0]
                        continue
                    if len(buffer) < length:
                        break
                    raw = bytes(buffer[:length])
                    del buffer[:length]
                    try:
                        parsed = parse_auto_push(raw)
                        if parsed.error_code:
                            raise ValueError(f'设备错误码 0x{parsed.error_code:02X}')
                        frame = AutoPushFrame(config, parsed.error_code, parsed.valid_data, raw)
                        resultant = frame.resultant_forces_newtons
                        points = frame.distributed_forces_newtons
                        modules = [{'slot': m, 'name': registers.MODULE_NAMES[m],
                                    'count': config.point_counts[m],
                                    'force': resultant[registers.MODULE_NAMES[m]],
                                    'points': points[registers.MODULE_NAMES[m]]}
                                   for m in config.active_modules]
                        self.received(modules=modules)
                        last_frame = time.monotonic()
                    except Exception as exc:
                        self.update(invalid=self.state['invalid'] + 1, error=str(exc))
                if time.monotonic() - last_frame > 2:
                    raise ConnectionError('触觉数据超时，正在重新连接')
        finally:
            # Return this viewer's auto-push acquisition to stopped state.
            if hand.transport._ser is not None:
                hand.close()


class ForceTorque(Device):
    def collect(self, port):
        parser = FTParser(expect_start_ack=True)
        with serial.Serial(port, 921600, timeout=.1, exclusive=True) as stream:
            self.update(protocol_verified=True, force_unit='N', torque_unit='N·m',
                        axes=['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'])
            stream.write(bytes.fromhex('AA 55 7F 07 01 D0'))
            probe = bytearray()
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                probe.extend(stream.read(max(1, min(stream.in_waiting, 65536))))
                index = probe.find(bytes.fromhex('AA 55 7F 07'))
                if index >= 0 and len(probe) >= index + 13:
                    frame = probe[index:index + 13]
                    if crc8(frame[:-1]) == frame[-1]:
                        firmware = bytes(frame[4:12]).decode('ascii', errors='replace').strip('\x00')
                        self.update(firmware=firmware)
                        break
            # Documented runtime acquisition command; no persistent settings or
            # calibration/zero commands are sent. Stop our stream when closing.
            stream.write(bytes.fromhex('AA 55 7F 03 04 9F'))
            try:
                last_frame = time.monotonic()
                while not self.stop.is_set():
                    data = stream.read(max(1, min(stream.in_waiting, 65536)))
                    for values in parser.feed(data):
                        warning = ('All six axes are returning 998. These readings are abnormal; '
                                   'check the sensor status before using them.'
                                   if values == [998.0] * 6 else '')
                        self.received(values=values, measurement_valid=not warning, warning=warning)
                        last_frame = time.monotonic()
                    if parser.invalid:
                        self.update(invalid=self.state['invalid'] + parser.invalid)
                        parser.invalid = 0
                    if time.monotonic() - last_frame > 2:
                        raise ConnectionError('六维力数据超时，正在重新连接')
            finally:
                with contextlib.suppress(serial.SerialException, OSError):
                    stream.write(bytes.fromhex('AA 55 7F 04 00 E8'))


def create_app():
    stop = threading.Event()
    tactile = Tactile('PX6AX · 触觉阵列', 'usb-PaxiniAdapte3_Paxini__Adapter_*-if00', stop)
    ft = ForceTorque('PX6D-75 · 六维力', 'usb-GigaDevice_GD32-CDC_ACM_*-if00', stop)
    devices = [tactile, ft]

    def snapshot():
        return {'timestamp': time.time(), 'tactile': tactile.snapshot(), 'ft': ft.snapshot()}

    async def index(request):
        return web.FileResponse(ROOT / 'index.html', headers={'Cache-Control': 'no-store'})

    async def chinese_index(request):
        return web.FileResponse(ROOT / 'index.zh.html', headers={'Cache-Control': 'no-store'})

    async def status(request):
        return web.json_response(snapshot())

    async def websocket(request):
        origin = request.headers.get('Origin')
        if origin and origin != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden(text='Origin mismatch')
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)

        async def publish():
            while not ws.closed:
                await ws.send_json(snapshot())
                await asyncio.sleep(.05)

        publisher = asyncio.create_task(publish())
        try:
            async for message in ws:
                pass
        finally:
            publisher.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await publisher
        return ws

    async def lifecycle(app):
        threads = [threading.Thread(target=d.run, daemon=True, name=d.name) for d in devices]
        for thread in threads:
            thread.start()
        yield
        stop.set()
        for thread in threads:
            await asyncio.to_thread(thread.join, 3)

    app = web.Application()
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get('/', index)
    app.router.add_get('/zh', chinese_index)
    app.router.add_get('/api/status', status)
    app.router.add_get('/ws', websocket)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    web.run_app(create_app(), host='127.0.0.1', port=args.port)
