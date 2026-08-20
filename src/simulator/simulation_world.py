import heapq
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.constants import fx_code_for_register_type
from utils.log_formatter import simulate_log


@dataclass(frozen=True)
class PointRef:
    slave_id: int
    register_type: str
    offset: int
    bit: int | None = None
    name: str | None = None
    serial_port: str | None = None

    @classmethod
    def from_dict(cls, value: dict) -> "PointRef":
        return cls(
            slave_id=int(value["slave_id"]),
            register_type=str(value.get("register_type", "holding")),
            offset=int(value["offset"]),
            bit=int(value["bit"]) if value.get("bit") is not None else None,
            name=value.get("name"),
            serial_port=value.get("serial_port"),
        )


class EventHistory:
    def __init__(self, output_file: str | None = None):
        self.events: list[dict[str, Any]] = []
        self.output_file = Path(output_file) if output_file else None
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]):
        payload = {
            "monotonic": time.monotonic(),
            "wall_time": datetime.now().isoformat(timespec="milliseconds"),
            **event,
        }
        with self._lock:
            self.events.append(payload)
            if self.output_file:
                self.output_file.parent.mkdir(parents=True, exist_ok=True)
                with self.output_file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SimulationWorld:
    """Cross-slave plant behavior layer for end-to-end control testing."""

    def __init__(
        self,
        device_registry: dict[tuple[str, int], dict[str, Any]],
        config: dict | None = None,
        scenario: dict | None = None,
    ):
        self.device_registry = device_registry
        self.config = config or {}
        self.poll_interval_sec = float(self.config.get("poll_interval_sec", 0.1))
        if self.poll_interval_sec <= 0:
            raise ValueError("simulation.poll_interval_sec must be greater than zero")

        self.behaviors = {
            behavior["id"]: behavior for behavior in self.config.get("behaviors", [])
        }
        self.faults: dict[str, dict] = {
            fault["behavior"]: fault for fault in self.config.get("faults", [])
        }
        self.overrides: dict[str, dict] = {}
        self.apply_scenario(scenario or {})

        self.history = EventHistory(self.config.get("event_history_file"))
        self.running = False
        self._thread: threading.Thread | None = None
        self._last_commands: dict[str, int] = {}
        self._generation: dict[str, int] = {
            behavior_id: 0 for behavior_id in self.behaviors
        }
        self._queue: list[tuple[float, int, str, int, dict[str, Any]]] = []
        self._queue_seq = 0

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.history.events

    def apply_scenario(self, scenario: dict):
        for fault in scenario.get("faults", []):
            self.faults[fault["behavior"]] = fault
        for override in scenario.get("overrides", []):
            self.overrides[override["behavior"]] = override

    def start(self):
        if self.running or not self.behaviors:
            return
        self.running = True
        self._prime_command_state()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        simulate_log(f"[SimulationWorld] started with {len(self.behaviors)} behaviors")

    def stop(self):
        self.running = False

    def _prime_command_state(self):
        for behavior_id, behavior in self.behaviors.items():
            self._last_commands[behavior_id] = self.read_point(
                PointRef.from_dict(behavior["command"])
            )

    def _loop(self):
        while self.running:
            now = time.monotonic()
            self._detect_commands(now)
            self._run_due(now)
            time.sleep(self.poll_interval_sec)

    def _detect_commands(self, now: float):
        for behavior_id, behavior in self.behaviors.items():
            command = PointRef.from_dict(behavior["command"])
            current = self.read_point(command)
            previous = self._last_commands.get(behavior_id, current)
            if current == previous:
                continue

            self._last_commands[behavior_id] = current
            self._record_point_change(
                behavior_id,
                command,
                previous,
                current,
                source="master_command",
            )
            self._generation[behavior_id] += 1
            generation = self._generation[behavior_id]
            self._handle_command(behavior_id, behavior, current, generation, now)

    def _handle_command(
        self,
        behavior_id: str,
        behavior: dict,
        command_value: int,
        generation: int,
        now: float,
    ):
        behavior_type = behavior.get("type", "actuator_feedback")

        if command_value and not self._interlocks_satisfied(behavior):
            self.history.record(
                {
                    "behavior": behavior_id,
                    "source": "interlock_blocked",
                    "old": 0,
                    "new": command_value,
                }
            )
            return

        if behavior_type == "actuator_feedback":
            self._schedule_actuator(
                behavior_id, behavior, command_value, generation, now
            )
        elif behavior_type == "chiller_status":
            self._schedule_chiller(
                behavior_id, behavior, command_value, generation, now
            )
        else:
            raise ValueError(f"Unsupported simulation behavior type: {behavior_type}")

    def _schedule_actuator(
        self,
        behavior_id: str,
        behavior: dict,
        command_value: int,
        generation: int,
        now: float,
    ):
        fault = self.faults.get(behavior_id, {})
        mode = fault.get("mode")
        if mode == "ignore":
            return
        if command_value and mode == "stuck_off":
            return
        if not command_value and mode == "stuck_on":
            return

        delay_key = "on_delay_sec" if command_value else "off_delay_sec"
        delay = self._effective_delay(behavior_id, behavior, delay_key)
        feedback = PointRef.from_dict(behavior["feedback"])
        feedback_value = int(bool(command_value))
        if mode == "fail" and command_value:
            feedback_value = int(fault.get("feedback_value", 0))
        self._schedule(
            now + delay,
            behavior_id,
            generation,
            {"kind": "write", "point": feedback, "value": feedback_value},
        )

    def _schedule_chiller(
        self,
        behavior_id: str,
        behavior: dict,
        command_value: int,
        generation: int,
        now: float,
    ):
        fault = self.faults.get(behavior_id, {})
        mode = fault.get("mode")
        if mode == "ignore":
            return

        status_points = [
            PointRef.from_dict(point) for point in behavior.get("status_points", [])
        ]
        if not status_points:
            return

        if command_value:
            if mode == "stuck_off":
                return
            starting = int(behavior.get("starting_status", 1))
            running = int(behavior.get("running_status", 2))
            start_delay = self._effective_delay(
                behavior_id, behavior, "start_delay_sec"
            )
            self._schedule_status_group(
                now, behavior_id, generation, status_points, starting
            )
            if mode == "fail":
                failed = int(fault.get("status", behavior.get("fault_status", 6)))
                self._schedule_status_group(
                    now + start_delay,
                    behavior_id,
                    generation,
                    status_points,
                    failed,
                )
            else:
                self._schedule_status_group(
                    now + start_delay,
                    behavior_id,
                    generation,
                    status_points,
                    running,
                )
        else:
            if mode == "stuck_on":
                return
            unloading = int(behavior.get("unloading_status", 9))
            stopped = int(behavior.get("stopped_status", 0))
            stop_delay = self._effective_delay(behavior_id, behavior, "stop_delay_sec")
            self._schedule_status_group(
                now, behavior_id, generation, status_points, unloading
            )
            self._schedule_status_group(
                now + stop_delay,
                behavior_id,
                generation,
                status_points,
                stopped,
            )

    def _schedule_status_group(
        self,
        due: float,
        behavior_id: str,
        generation: int,
        points: list[PointRef],
        value: int,
    ):
        for point in points:
            self._schedule(
                due,
                behavior_id,
                generation,
                {"kind": "write", "point": point, "value": value},
            )

    def _schedule(
        self,
        due: float,
        behavior_id: str,
        generation: int,
        action: dict[str, Any],
    ):
        self._queue_seq += 1
        heapq.heappush(
            self._queue,
            (due, self._queue_seq, behavior_id, generation, action),
        )

    def _run_due(self, now: float):
        while self._queue and self._queue[0][0] <= now:
            _, _, behavior_id, generation, action = heapq.heappop(self._queue)
            if generation != self._generation.get(behavior_id):
                continue
            if action["kind"] == "write":
                self.write_point(
                    action["point"],
                    int(action["value"]),
                    behavior_id=behavior_id,
                    source="plant_behavior",
                )

    def _effective_delay(self, behavior_id: str, behavior: dict, key: str) -> float:
        override = self.overrides.get(behavior_id, {})
        value = float(override.get(key, behavior.get(key, 0.0)))
        fault = self.faults.get(behavior_id, {})
        multiplier = float(fault.get("delay_multiplier", 1.0))
        return max(0.0, value * multiplier)

    def _interlocks_satisfied(self, behavior: dict) -> bool:
        for requirement in behavior.get("requires", []):
            point = PointRef.from_dict(requirement)
            expected = int(requirement.get("equals", 1))
            if self.read_point(point) != expected:
                return False
        return True

    def _resolve_context(self, point: PointRef):
        if point.serial_port is not None:
            key = (point.serial_port, point.slave_id)
            if key not in self.device_registry:
                raise KeyError(f"No simulator device for port/slave {key}")
            return self.device_registry[key]["context"]

        matches = [
            entry
            for (_, slave_id), entry in self.device_registry.items()
            if slave_id == point.slave_id
        ]
        if len(matches) != 1:
            raise KeyError(
                f"slave_id={point.slave_id} is not unique; specify serial_port in simulation point"
            )
        return matches[0]["context"]

    def read_point(self, point: PointRef) -> int:
        context = self._resolve_context(point)
        fx_code = fx_code_for_register_type(point.register_type)
        values = context.getValues(fx_code, point.offset, count=1)
        if not values:
            raise ValueError(f"Cannot read simulation point {point}")
        raw = int(values[0])
        if point.bit is None:
            return raw
        return (raw >> point.bit) & 1

    def write_point(
        self,
        point: PointRef,
        value: int,
        behavior_id: str,
        source: str,
    ):
        context = self._resolve_context(point)
        fx_code = fx_code_for_register_type(point.register_type)
        old = self.read_point(point)

        if point.bit is None:
            raw = int(value) & 0xFFFF
            context.setValues(fx_code, point.offset, [raw])
        else:
            words = context.getValues(fx_code, point.offset, count=1)
            if not words:
                raise ValueError(f"Cannot read simulation point {point}")
            raw = int(words[0])
            mask = 1 << point.bit
            raw = (raw & ~mask) | ((int(bool(value)) << point.bit) & mask)
            context.setValues(fx_code, point.offset, [raw & 0xFFFF])

        new = self.read_point(point)
        if old != new:
            self._record_point_change(behavior_id, point, old, new, source)

    def _record_point_change(
        self,
        behavior_id: str,
        point: PointRef,
        old: int,
        new: int,
        source: str,
    ):
        self.history.record(
            {
                "behavior": behavior_id,
                "slave_id": point.slave_id,
                "serial_port": point.serial_port,
                "register_type": point.register_type,
                "offset": point.offset,
                "bit": point.bit,
                "pin": point.name,
                "old": old,
                "new": new,
                "source": source,
            }
        )
