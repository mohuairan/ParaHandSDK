"""FSR402 与华威科触觉传感器的统一非阻塞接口。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional


FSR402_MODE = "fsr402"
HWK_MODE = "hwk"
TACTILE_MODES = (FSR402_MODE, HWK_MODE)
FINGERTIP_REGIONS = ("thumb", "index", "middle", "ring", "little")
TactileValue = int | float


def normalize_tactile_mode(mode: Any) -> str:
    """规范化传感器模式名，并为常见旧称提供兼容。"""

    value = str(mode).strip().lower()
    aliases = {
        "fsr": FSR402_MODE,
        "fsr402": FSR402_MODE,
        "hit": HWK_MODE,
        "hwk": HWK_MODE,
        "华威科": HWK_MODE,
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError(f"tactile.mode 必须是 {list(TACTILE_MODES)} 之一") from exc


@dataclass(frozen=True, slots=True)
class TactileRegion:
    """一个手指或掌心区域的一次触觉读数。"""

    name: str
    source_id: int
    values: tuple[TactileValue, ...]
    matrices: tuple[tuple[tuple[TactileValue, ...], ...], ...]
    unit: str
    received_at: float
    force_summary: Mapping[str, TactileValue] = field(default_factory=dict)

    @property
    def representative_value(self) -> float:
        """供状态栏和阈值判断使用的代表值；矩阵传感器取主块最大值。"""

        return max(self.values) if self.values else -1.0


@dataclass(frozen=True, slots=True)
class TactileSnapshot:
    """与具体硬件无关的触觉快照，同时保留原生数据。"""

    mode: str
    timestamp: float
    unit: str
    regions: Mapping[str, TactileRegion]
    raw: Any

    @property
    def value_map(self) -> dict[str, float]:
        return {
            name: region.representative_value
            for name, region in self.regions.items()
        }

    @property
    def matrices(self) -> dict[str, tuple[tuple[tuple[TactileValue, ...], ...], ...]]:
        return {name: region.matrices for name, region in self.regions.items()}

    @property
    def force_summaries(self) -> dict[str, dict[str, TactileValue]]:
        """返回各区域的 Fx、Fy 与 Fnormal_sum 物理力汇总。"""

        return {
            name: dict(region.force_summary)
            for name, region in self.regions.items()
            if region.force_summary
        }


class FSR402TactileAdapter:
    """将五路 FSR402 接口适配为统一触觉快照。"""

    mode = FSR402_MODE
    unit = "g"

    def __init__(
        self,
        port: Optional[str] = None,
        *,
        baudrate: int = 115200,
        timeout_s: float = 1.0,
        channel_fingers: tuple[str, ...] = FINGERTIP_REGIONS,
        sensor_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if len(channel_fingers) != len(FINGERTIP_REGIONS):
            raise ValueError("FSR402 channel_fingers 必须包含 5 个手指")
        if set(channel_fingers) != set(FINGERTIP_REGIONS):
            raise ValueError("FSR402 channel_fingers 必须各包含一次五指名称")
        if baudrate <= 0 or timeout_s < 0:
            raise ValueError("FSR402 baudrate 必须大于 0，timeout_s 不能小于 0")
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.channel_fingers = tuple(channel_fingers)
        self._sensor_factory = sensor_factory
        self._sensor: Any = None

    @property
    def is_running(self) -> bool:
        return self._sensor is not None

    def start(self) -> None:
        if self._sensor is not None:
            return
        factory = self._sensor_factory
        if factory is None:
            from fsr402_sensor import FSR402Sensor

            factory = FSR402Sensor
        sensor = factory(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
        )
        sensor.start()
        self._sensor = sensor

    def stop(self) -> None:
        sensor = self._sensor
        self._sensor = None
        if sensor is not None:
            sensor.stop()

    def read(self) -> Optional[TactileSnapshot]:
        sensor = self._sensor
        if sensor is None:
            return None
        frame = sensor.read()
        if frame is None:
            return None

        regions: dict[str, TactileRegion] = {}
        for channel_index, channel in enumerate(frame.channels):
            if channel_index >= len(self.channel_fingers):
                break
            name = self.channel_fingers[channel_index]
            force_g = float(channel.force_g)
            regions[name] = TactileRegion(
                name=name,
                source_id=int(channel.channel),
                values=(force_g,),
                matrices=(((force_g,),),),
                unit=self.unit,
                received_at=float(frame.timestamp),
            )
        return TactileSnapshot(
            mode=self.mode,
            timestamp=float(frame.timestamp),
            unit=self.unit,
            regions=regions,
            raw=frame,
        )


class HWKTactileAdapter:
    """在后台轮询华威科设备，并发布最新的完整区域快照。"""

    mode = HWK_MODE
    unit = "g"

    def __init__(
        self,
        port: Optional[str] = None,
        *,
        baudrate: int = 921600,
        response_timeout_s: float = 0.2,
        write_timeout_s: float = 0.2,
        poll_interval_s: float = 0.0,
        device_regions: Mapping[int, str],
        bus_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not device_regions:
            raise ValueError("华威科 device_regions 不能为空")
        self.port = port
        self.baudrate = baudrate
        self.response_timeout_s = response_timeout_s
        self.write_timeout_s = write_timeout_s
        self.poll_interval_s = poll_interval_s
        if baudrate <= 0 or response_timeout_s <= 0:
            raise ValueError("华威科 baudrate 和 response_timeout_s 必须大于 0")
        if write_timeout_s < 0 or poll_interval_s < 0:
            raise ValueError("华威科 write_timeout_s 和 poll_interval_s 不能小于 0")
        self.device_regions = {
            int(key): str(value).strip() for key, value in device_regions.items()
        }
        if any(not 1 <= device_id <= 15 for device_id in self.device_regions):
            raise ValueError("华威科设备 ID 必须在 1..15 之间")
        if any(not region.strip() for region in self.device_regions.values()):
            raise ValueError("华威科区域名不能为空")
        if len(set(self.device_regions.values())) != len(self.device_regions):
            raise ValueError("华威科区域名不能重复")
        self._bus_factory = bus_factory
        self._bus: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict[int, Any] = {}
        self._last_error: Optional[BaseException] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    def start(self) -> None:
        if self.is_running:
            return
        factory = self._bus_factory
        if factory is None:
            from hwk_tactile import TactileSensorBus

            factory = TactileSensorBus
        bus = factory(
            port=self.port,
            baudrate=self.baudrate,
            response_timeout=self.response_timeout_s,
            write_timeout=self.write_timeout_s,
        )
        bus.open()
        self._bus = bus
        self._last_error = None
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="hwk-tactile-reader",
            daemon=True,
        )
        self._thread.start()

    def _reader_loop(self) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            for reading in bus.iter_readings(
                tuple(self.device_regions),
                interval=self.poll_interval_s,
                ignore_timeouts=True,
                reconnect=True,
                stop_event=self._stop_event,
            ):
                with self._lock:
                    self._latest[int(reading.device_id)] = reading
        except Exception as exc:
            if not self._stop_event.is_set():
                self._last_error = exc

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            wait_s = max(2.0, self.response_timeout_s * len(self.device_regions) + 1.0)
            thread.join(timeout=wait_s)
        bus = self._bus
        self._bus = None
        self._thread = None
        if bus is not None:
            bus.close()
        with self._lock:
            self._latest.clear()

    def read(self) -> Optional[TactileSnapshot]:
        with self._lock:
            latest = dict(self._latest)
        if not latest:
            return None

        regions: dict[str, TactileRegion] = {}
        timestamps: list[float] = []
        for device_id, region_name in self.device_regions.items():
            reading = latest.get(device_id)
            if reading is None or not reading.packets:
                continue
            pressure_packets = tuple(
                packet
                for packet in reading.packets
                if getattr(packet, "data_type", "pressure_map") == "pressure_map"
            )
            if not pressure_packets:
                continue
            primary_packet = pressure_packets[0]
            force_summary: dict[str, TactileValue] = {}
            for packet in reading.packets:
                if getattr(packet, "data_type", "pressure_map") != "force_summary":
                    continue
                labels = tuple(getattr(packet, "labels", ()))
                force_summary.update(
                    {
                        label: value
                        for label, value in zip(labels, packet.values)
                    }
                )
            received_at = float(reading.received_at)
            timestamps.append(received_at)
            regions[region_name] = TactileRegion(
                name=region_name,
                source_id=device_id,
                values=tuple(primary_packet.values),
                matrices=tuple(packet.matrix for packet in pressure_packets),
                unit=primary_packet.unit or self.unit,
                received_at=received_at,
                force_summary=force_summary,
            )
        if not regions:
            return None
        return TactileSnapshot(
            mode=self.mode,
            timestamp=max(timestamps),
            unit=self.unit,
            regions=regions,
            raw=latest,
        )

    def calibrate(self, device_id: int, command: int = 2) -> None:
        bus = self._bus
        if bus is None:
            raise RuntimeError("华威科触觉传感器尚未启动")
        bus.send_zero_calibration(device_id, command)


def create_tactile_sensor(mode: Any, **kwargs: Any) -> Any:
    """按模式创建统一适配器。"""

    normalized = normalize_tactile_mode(mode)
    if normalized == FSR402_MODE:
        return FSR402TactileAdapter(**kwargs)
    return HWKTactileAdapter(**kwargs)


__all__ = [
    "FINGERTIP_REGIONS",
    "FSR402_MODE",
    "FSR402TactileAdapter",
    "HWK_MODE",
    "HWKTactileAdapter",
    "TACTILE_MODES",
    "TactileValue",
    "TactileRegion",
    "TactileSnapshot",
    "create_tactile_sensor",
    "normalize_tactile_mode",
]
