"""华威科 V1.6 串口协议的无硬件编解码实现。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from struct import unpack
from time import time
from typing import Iterable

from .errors import CRCError, FrameError, ProtocolError

HEAD = b"<<"
TAIL = b">>"
CHANNEL_DEVICE_INFO = 0x01
CHANNEL_DATA = 0x02
CHANNEL_CALIBRATION = 0x07
CHANNEL_SET_ADDRESS = 0x09
FRAME_OVERHEAD = 10


class Operation(IntEnum):
    """FLAGS 低两位。

    协议正文写 ACK=0b11，但设备返回示例使用 0b10，因此同时保留两者。
    """

    PUT = 0b00
    GET = 0b01
    ACK_EXAMPLE = 0b10
    ACK = 0b11


@dataclass(frozen=True, slots=True)
class Frame:
    """一帧已经通过结构与 CRC 校验的数据。"""

    device_id: int
    channel: int
    flags: int
    payload: bytes

    @property
    def request_id(self) -> int:
        return self.flags >> 2

    @property
    def operation(self) -> Operation:
        return Operation(self.flags & 0b11)


@dataclass(frozen=True, slots=True)
class PressurePacket:
    """一个传感器数据块；values 是内部标定后的小端输出值。"""

    device_id: int
    request_id: int
    total_packets: int
    packet_index: int
    columns: int
    rows: int
    values: tuple[int | float, ...]
    received_at: float
    bytes_per_value: int = 2
    unit: str | None = None
    data_type: str = "pressure_map"
    labels: tuple[str, ...] = ()

    @property
    def matrix(self) -> tuple[tuple[int | float, ...], ...]:
        """按文档给出的行列数，以行优先形式返回不可变二维矩阵。"""

        return tuple(
            self.values[row * self.columns : (row + 1) * self.columns]
            for row in range(self.rows)
        )


@dataclass(frozen=True, slots=True)
class PressureReading:
    """一次轮询得到的一个或多个压力数据包。"""

    device_id: int
    request_id: int
    packets: tuple[PressurePacket, ...]

    @property
    def received_at(self) -> float:
        return max(packet.received_at for packet in self.packets)

    @property
    def matrices(self) -> tuple[tuple[tuple[int | float, ...], ...], ...]:
        return tuple(packet.matrix for packet in self.packets)


def crc16(data: bytes | bytearray | memoryview | Iterable[int]) -> int:
    """计算协议使用的 CRC16（初值 0，多项式 0xA001）。"""

    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc >> 1) ^ 0xA001) if crc & 1 else crc >> 1
    return crc


def make_flags(request_id: int, operation: Operation | int) -> int:
    if not 0 <= request_id <= 0x3F:
        raise ValueError("request_id 必须在 0..63 之间")
    operation_value = int(operation)
    if not 0 <= operation_value <= 0b11:
        raise ValueError("operation 必须占 2 位")
    return (request_id << 2) | operation_value


def encode_frame(
    device_id: int,
    channel: int,
    flags: int,
    payload: bytes | bytearray | memoryview = b"",
) -> bytes:
    """编码主从模式帧。"""

    if not 0 <= device_id <= 0x0F:
        raise ValueError("device_id 必须在 0..15 之间")
    if not 0 <= channel <= 0x0F:
        raise ValueError("channel 必须在 0..15 之间")
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags 必须在 0..255 之间")
    payload_bytes = bytes(payload)
    if len(payload_bytes) > 0xFFFF:
        raise ValueError("payload 不能超过 65535 字节")

    id_channel = (device_id << 4) | channel
    checksum = crc16(payload_bytes)
    return b"".join(
        (
            HEAD,
            bytes((id_channel, flags)),
            len(payload_bytes).to_bytes(2, "little"),
            payload_bytes,
            checksum.to_bytes(2, "little"),
            TAIL,
        )
    )


def build_request(
    device_id: int,
    channel: int,
    request_id: int,
    payload: bytes = b"\x01",
    operation: Operation = Operation.GET,
) -> bytes:
    return encode_frame(
        device_id=device_id,
        channel=channel,
        flags=make_flags(request_id, operation),
        payload=payload,
    )


def decode_frame(data: bytes | bytearray | memoryview, max_payload: int = 8192) -> Frame:
    """解码一帧完整数据；数据必须恰好只包含一帧。"""

    raw = bytes(data)
    if len(raw) < FRAME_OVERHEAD:
        raise FrameError(f"帧过短：{len(raw)} 字节")
    if raw[:2] != HEAD:
        raise FrameError("帧头不是 3C 3C")
    payload_length = int.from_bytes(raw[4:6], "little")
    if payload_length > max_payload:
        raise FrameError(f"负载长度 {payload_length} 超过限制 {max_payload}")
    expected_length = FRAME_OVERHEAD + payload_length
    if len(raw) != expected_length:
        raise FrameError(f"帧长度应为 {expected_length}，实际为 {len(raw)}")
    if raw[-2:] != TAIL:
        raise FrameError("帧尾不是 3E 3E")

    payload = raw[6 : 6 + payload_length]
    expected_crc = int.from_bytes(raw[6 + payload_length : 8 + payload_length], "little")
    actual_crc = crc16(payload)
    if actual_crc != expected_crc:
        raise CRCError(f"CRC 错误：收到 0x{expected_crc:04X}，计算得 0x{actual_crc:04X}")

    id_channel = raw[2]
    return Frame(
        device_id=id_channel >> 4,
        channel=id_channel & 0x0F,
        flags=raw[3],
        payload=payload,
    )


class FrameParser:
    """可处理拆包、粘包、噪声和坏帧的增量解析器。"""

    def __init__(self, max_payload: int = 8192) -> None:
        self.max_payload = max_payload
        self._buffer = bytearray()
        self.discarded_bytes = 0
        self.crc_errors = 0
        self.tail_errors = 0
        self.length_errors = 0

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes | bytearray | memoryview) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []

        while True:
            head_index = self._buffer.find(HEAD)
            if head_index < 0:
                keep = 1 if self._buffer.endswith(HEAD[:1]) else 0
                discard = len(self._buffer) - keep
                if discard:
                    del self._buffer[:discard]
                    self.discarded_bytes += discard
                break
            if head_index:
                del self._buffer[:head_index]
                self.discarded_bytes += head_index
            # Noise can end with one '<' and overlap the next frame as '<<<'.
            if self._buffer.startswith(b"<<<"):
                del self._buffer[0]
                self.discarded_bytes += 1
                continue
            if len(self._buffer) < 6:
                break

            payload_length = int.from_bytes(self._buffer[4:6], "little")
            if payload_length > self.max_payload:
                del self._buffer[0]
                self.length_errors += 1
                self.discarded_bytes += 1
                continue
            frame_length = FRAME_OVERHEAD + payload_length
            if len(self._buffer) < frame_length:
                break
            candidate = bytes(self._buffer[:frame_length])
            if candidate[-2:] != TAIL:
                del self._buffer[0]
                self.tail_errors += 1
                self.discarded_bytes += 1
                continue
            try:
                frame = decode_frame(candidate, max_payload=self.max_payload)
            except CRCError:
                del self._buffer[0]
                self.crc_errors += 1
                self.discarded_bytes += 1
                continue
            frames.append(frame)
            del self._buffer[:frame_length]

        return frames


def parse_pressure_payloads(
    frame: Frame, received_at: float | None = None
) -> tuple[PressurePacket, ...]:
    """解析 CHANNEL 0x02 中一个或多个连续数据块。

    协议示例为 ``总块数, 块序号, 列, 行, uint16 数据``。COM7 实机还会
    在同一负载中继续追加 ``块序号, 列, 行, 数据``，并允许辅助块使用
    uint8。此函数同时兼容单帧单块、多帧分块和同帧复合块。
    """

    if frame.channel != CHANNEL_DATA:
        raise ProtocolError(f"期望数据通道 0x02，实际为 0x{frame.channel:02X}")
    if len(frame.payload) < 4:
        raise ProtocolError("压力负载不足 4 字节")

    total_packets = frame.payload[0]
    if total_packets == 0:
        raise ProtocolError("总包数不能为 0")

    timestamp = time() if received_at is None else received_at

    def parse_from(offset: int, blocks_left: int, first_block: bool) -> list[PressurePacket] | None:
        if blocks_left == 0:
            return [] if offset == len(frame.payload) else None
        if offset + 3 > len(frame.payload):
            return None
        packet_index, columns, rows = frame.payload[offset : offset + 3]
        if columns == 0 or rows == 0:
            return None
        data_start = offset + 3

        # 实机第二包：03 表示 Fx/Fy/Fnormal_sum 三个分量，04 表示每项 4 字节 float32。
        if (
            not first_block
            and packet_index == 2
            and columns == 3
            and rows == 4
            and blocks_left == 1
            and len(frame.payload) - data_start == 12
        ):
            data = frame.payload[data_start : data_start + 12]
            packet = PressurePacket(
                device_id=frame.device_id,
                request_id=frame.request_id,
                total_packets=total_packets,
                packet_index=packet_index,
                columns=3,
                rows=1,
                values=unpack("<fff", data),
                received_at=timestamp,
                bytes_per_value=4,
                unit="g",
                data_type="force_summary",
                labels=("Fx", "Fy", "Fnormal_sum"),
            )
            return [packet]

        cells = columns * rows
        widths = (2,) if first_block else (2, 1, 4)
        for width in widths:
            data_end = data_start + cells * width
            if data_end > len(frame.payload):
                continue
            remainder = parse_from(data_end, blocks_left - 1, False)
            if remainder is None:
                continue
            data = frame.payload[data_start:data_end]
            values = tuple(
                int.from_bytes(data[index : index + width], "little")
                for index in range(0, len(data), width)
            )
            packet = PressurePacket(
                device_id=frame.device_id,
                request_id=frame.request_id,
                total_packets=total_packets,
                packet_index=packet_index,
                columns=columns,
                rows=rows,
                values=values,
                received_at=timestamp,
                bytes_per_value=width,
                unit="g" if width == 2 else None,
            )
            return [packet, *remainder]
        return None

    # 第一块头部从总块数字段之后开始，压力主矩阵按协议固定为 uint16。
    complete = parse_from(1, total_packets, True)
    if complete is not None:
        return tuple(complete)

    # 兼容总块数大于 1、但本帧只携带当前 uint16 块的协议实现。
    packet_index, columns, rows = frame.payload[1:4]
    expected_end = 4 + columns * rows * 2
    if columns and rows and len(frame.payload) == expected_end:
        data = frame.payload[4:expected_end]
        return (
            PressurePacket(
                device_id=frame.device_id,
                request_id=frame.request_id,
                total_packets=total_packets,
                packet_index=packet_index,
                columns=columns,
                rows=rows,
                values=tuple(
                    int.from_bytes(data[index : index + 2], "little")
                    for index in range(0, len(data), 2)
                ),
                received_at=timestamp,
                bytes_per_value=2,
                unit="g",
            ),
        )

    raise ProtocolError(
        f"无法解析数据负载：声明 {total_packets} 块，负载共 {len(frame.payload)} 字节"
    )


def parse_pressure_payload(frame: Frame, received_at: float | None = None) -> PressurePacket:
    """兼容旧 API，返回负载中的第一个压力数据块。"""

    return parse_pressure_payloads(frame, received_at)[0]
