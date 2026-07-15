"""Windows COM 串口上的灵巧手传感器轮询接口。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import monotonic, sleep
from typing import Callable, Iterable, Iterator, Protocol

from .errors import PortSelectionError, ProtocolError, TactileTimeoutError
from .protocol import (
    CHANNEL_CALIBRATION,
    CHANNEL_DATA,
    FrameParser,
    Operation,
    PressurePacket,
    PressureReading,
    build_request,
)

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # 允许只运行不需要硬件的协议测试
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


DEFAULT_BAUDRATE = 921600
DEFAULT_DEVICE_IDS = (1, 2, 3, 4, 5, 6)
ACK_OPERATIONS = {Operation.ACK_EXAMPLE, Operation.ACK}


class SerialLike(Protocol):
    is_open: bool
    in_waiting: int

    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
    def reset_input_buffer(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PortInfo:
    device: str
    description: str
    hwid: str


def list_serial_ports() -> tuple[PortInfo, ...]:
    """列出 Windows 当前可见的 COM 串口。"""

    if list_ports is None:
        raise RuntimeError("缺少 pyserial，请先执行：python -m pip install pyserial")
    return tuple(
        PortInfo(port.device, port.description, port.hwid)
        for port in sorted(list_ports.comports(), key=lambda item: item.device)
    )


class TactileSensorBus:
    """轮询连接在同一 USB 转接板上的主从传感器。"""

    def __init__(
        self,
        port: str | None = None,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        response_timeout: float = 0.2,
        write_timeout: float = 0.2,
        max_payload: int = 8192,
        serial_factory: Callable[..., SerialLike] | None = None,
    ) -> None:
        if response_timeout <= 0:
            raise ValueError("response_timeout 必须大于 0")
        self.port = port
        self.baudrate = baudrate
        self.response_timeout = response_timeout
        self.write_timeout = write_timeout
        self._serial_factory = serial_factory
        self._serial: SerialLike | None = None
        self._parser = FrameParser(max_payload=max_payload)
        self._request_id = 0

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def _resolve_port(self) -> str:
        if self.port:
            return self.port
        ports = list_serial_ports()
        if not ports:
            raise PortSelectionError("未发现 COM 口；请连接转接板并检查 Windows 设备管理器")
        if len(ports) > 1:
            names = ", ".join(port.device for port in ports)
            raise PortSelectionError(f"发现多个 COM 口（{names}），请用 port='COMx' 明确指定")
        self.port = ports[0].device
        return self.port

    def open(self) -> TactileSensorBus:
        if self.is_open:
            return self
        port = self._resolve_port()
        factory = self._serial_factory
        if factory is None:
            if serial is None:
                raise RuntimeError("缺少 pyserial，请先执行：python -m pip install pyserial")
            factory = serial.Serial
        self._serial = factory(
            port=port,
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.01,
            write_timeout=self.write_timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._parser.reset()
        return self

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self._parser.reset()

    def reconnect(self, delay: float = 1.0) -> None:
        self.close()
        if delay > 0:
            sleep(delay)
        self.open()

    def __enter__(self) -> TactileSensorBus:
        return self.open()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _ensure_open(self) -> SerialLike:
        if not self.is_open:
            self.open()
        assert self._serial is not None
        return self._serial

    def _next_request_id(self) -> int:
        request_id = self._request_id
        self._request_id = (self._request_id + 1) & 0x3F
        return request_id

    def poll_device(self, device_id: int, timeout: float | None = None) -> PressureReading:
        """轮询一个设备并收齐本次响应包含的所有数据包。"""

        if not 1 <= device_id <= 0x0F:
            raise ValueError("device_id 必须在 1..15 之间")
        serial_port = self._ensure_open()
        request_id = self._next_request_id()
        request = build_request(device_id, CHANNEL_DATA, request_id, b"\x01")
        self._parser.reset()
        serial_port.reset_input_buffer()
        written = serial_port.write(request)
        if written != len(request):
            raise OSError(f"串口只写入 {written}/{len(request)} 字节")
        serial_port.flush()

        deadline = monotonic() + (self.response_timeout if timeout is None else timeout)
        packets: dict[int, PressurePacket] = {}
        expected_total: int | None = None

        while monotonic() < deadline:
            chunk = serial_port.read(max(1, serial_port.in_waiting))
            if not chunk:
                continue
            for frame in self._parser.feed(chunk):
                if frame.device_id != device_id or frame.channel != CHANNEL_DATA:
                    continue
                if frame.request_id != request_id or frame.operation not in ACK_OPERATIONS:
                    continue
                new_packets = self._parse_consistent_packets(frame, expected_total)
                if expected_total is None:
                    expected_total = new_packets[0].total_packets
                for packet in new_packets:
                    packets[packet.packet_index] = packet
                if expected_total is not None and len(packets) == expected_total:
                    ordered = tuple(packets[index] for index in sorted(packets))
                    return PressureReading(device_id, request_id, ordered)

        detail = f"，已收到 {len(packets)}/{expected_total} 包" if expected_total else ""
        raise TactileTimeoutError(
            f"等待设备 0x{device_id:02X} 响应超时（{timeout or self.response_timeout:.3f}s）{detail}"
        )

    @staticmethod
    def _parse_consistent_packets(
        frame: object, expected_total: int | None
    ) -> tuple[PressurePacket, ...]:
        from .protocol import Frame, parse_pressure_payloads

        assert isinstance(frame, Frame)
        packets = parse_pressure_payloads(frame)
        if expected_total is not None and packets[0].total_packets != expected_total:
            raise ProtocolError(
                f"同一响应的总包数不一致：{expected_total} 和 {packets[0].total_packets}"
            )
        return packets

    def poll_devices(
        self,
        device_ids: Iterable[int] = DEFAULT_DEVICE_IDS,
        *,
        ignore_timeouts: bool = False,
        interval: float = 0.0,
    ) -> dict[int, PressureReading]:
        """按顺序完成一轮设备轮询。"""

        readings: dict[int, PressureReading] = {}
        ids = tuple(device_ids)
        for index, device_id in enumerate(ids):
            try:
                readings[device_id] = self.poll_device(device_id)
            except TactileTimeoutError:
                if not ignore_timeouts:
                    raise
            if interval > 0 and index + 1 < len(ids):
                sleep(interval)
        return readings

    def iter_readings(
        self,
        device_ids: Iterable[int] = DEFAULT_DEVICE_IDS,
        *,
        interval: float = 0.0,
        ignore_timeouts: bool = True,
        reconnect: bool = True,
        reconnect_delay: float = 1.0,
        stop_event: Event | None = None,
    ) -> Iterator[PressureReading]:
        """持续轮询；串口断开时默认自动重连。"""

        ids = tuple(device_ids)
        if not ids:
            raise ValueError("device_ids 不能为空")
        while stop_event is None or not stop_event.is_set():
            for device_id in ids:
                if stop_event is not None and stop_event.is_set():
                    return
                try:
                    yield self.poll_device(device_id)
                except TactileTimeoutError:
                    if not ignore_timeouts:
                        raise
                except (OSError, IOError):
                    if not reconnect:
                        raise
                    self.reconnect(reconnect_delay)
                if interval > 0:
                    sleep(interval)

    def send_zero_calibration(self, device_id: int, command: int = 2) -> None:
        """发送归零控制：0=关闭、1=开启、2=重新校准。"""

        if command not in (0, 1, 2):
            raise ValueError("command 只能是 0、1 或 2")
        serial_port = self._ensure_open()
        request_id = self._next_request_id()
        request = build_request(
            device_id,
            CHANNEL_CALIBRATION,
            request_id,
            bytes((0x01, command)),
            operation=Operation.PUT,
        )
        written = serial_port.write(request)
        if written != len(request):
            raise OSError(f"串口只写入 {written}/{len(request)} 字节")
        serial_port.flush()
