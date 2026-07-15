"""华威科灵巧手压力传感器接口。"""

from .bus import PortInfo, TactileSensorBus, list_serial_ports
from .errors import (
    CRCError,
    FrameError,
    PortSelectionError,
    ProtocolError,
    TactileError,
    TactileTimeoutError,
)
from .protocol import (
    CHANNEL_CALIBRATION,
    CHANNEL_DATA,
    CHANNEL_DEVICE_INFO,
    Frame,
    FrameParser,
    Operation,
    PressurePacket,
    PressureReading,
    build_request,
    crc16,
    decode_frame,
    encode_frame,
    parse_pressure_payload,
    parse_pressure_payloads,
)

__all__ = [
    "CHANNEL_CALIBRATION",
    "CHANNEL_DATA",
    "CHANNEL_DEVICE_INFO",
    "CRCError",
    "Frame",
    "FrameError",
    "FrameParser",
    "Operation",
    "PortInfo",
    "PortSelectionError",
    "PressurePacket",
    "PressureReading",
    "ProtocolError",
    "TactileError",
    "TactileSensorBus",
    "TactileTimeoutError",
    "build_request",
    "crc16",
    "decode_frame",
    "encode_frame",
    "list_serial_ports",
    "parse_pressure_payload",
    "parse_pressure_payloads",
]
