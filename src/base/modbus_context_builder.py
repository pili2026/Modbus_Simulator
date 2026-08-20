import struct

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext

from utils.constants import (
    FC_CODE_MAP,
    REGISTER_TYPE_MAP,
    fx_code_for_register_type,
    normalize_register_type,
)
from utils.float_codec import encode_float32_little_swap


class ModbusContextBuilder:
    """Build a slave context with enough headroom for pymodbus address translation."""

    def __init__(self, config: dict):
        self.config = config
        self.device_type = config.get("type", "").lower()
        self.register_type = normalize_register_type(
            config.get("register_type", "holding")
        )
        self.reg_key = REGISTER_TYPE_MAP[self.register_type]
        self.fx_code = FC_CODE_MAP[self.reg_key]

        self.max_end_addr = 1
        self.initial_registers_16: dict[tuple[int, int], int] = {}

    def build(self) -> tuple[ModbusSlaveContext, int]:
        pins = self._get_pins_or_alt()
        self._collect_initial_from_pins(pins)

        legacy_init = self._collect_legacy_initials()
        self.initial_registers_16.update(legacy_init)

        legacy_max = 1
        if self.initial_registers_16:
            legacy_max = max(addr + 2 for _, addr in self.initial_registers_16)

        block_size = max(self.max_end_addr, legacy_max, 1)
        assert block_size >= self.max_end_addr

        context = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * block_size),
            co=ModbusSequentialDataBlock(0, [0] * block_size),
            hr=ModbusSequentialDataBlock(0, [0] * block_size),
            ir=ModbusSequentialDataBlock(0, [0] * block_size),
        )

        for (fx_code, addr), val in self.initial_registers_16.items():
            context.setValues(fx_code, addr, [val & 0xFFFF])

        self._write_full_initials(context, pins)
        self._assert_pin_addresses_fit(block_size, pins)
        return context, self.fx_code

    def _get_pins_or_alt(self):
        pins = self.config.get("pins")
        if pins is None:
            alt_key = f"{self.reg_key}_registers"
            pins = self.config.get(alt_key)
        return pins or []

    @staticmethod
    def _word_width_for_type(pin_type: str) -> int:
        pin_type = (pin_type or "").lower()
        if pin_type in ("float", "uint32", "int32"):
            return 2
        return 1

    def _fx_for_pin(self, pin: dict) -> int:
        return fx_code_for_register_type(pin.get("register_type"), self.register_type)

    def _collect_initial_from_pins(self, pins: list[dict]):
        base_addr = int(self.config.get("base_address", 0))
        max_end = 1

        for pin in pins:
            if "offset" not in pin:
                continue
            addr = base_addr + int(pin["offset"])
            width = self._word_width_for_type(pin.get("type"))
            datastore_end = addr + 1 + width
            max_end = max(max_end, datastore_end)

            raw_value = pin.get("value")
            pin_type = (pin.get("type") or "").lower()
            if raw_value is not None and pin_type not in ("float", "uint32", "int32"):
                fx_code = self._fx_for_pin(pin)
                self.initial_registers_16[(fx_code, addr)] = int(raw_value) & 0xFFFF

        self.max_end_addr = max_end

    def _collect_legacy_initials(self) -> dict[tuple[int, int], int]:
        if self.device_type != "inverter":
            return {}
        raw_map = self.config.get("initial_registers", {}) or {}
        return {
            (self.fx_code, int(addr)): int(value) & 0xFFFF
            for addr, value in raw_map.items()
        }

    def _write_full_initials(self, context: ModbusSlaveContext, pins: list[dict]):
        base_addr = int(self.config.get("base_address", 0))
        for pin in pins:
            if "offset" not in pin:
                continue
            addr = base_addr + int(pin["offset"])
            pin_type = (pin.get("type") or "").lower()
            raw_value = pin.get("value")
            if raw_value is None:
                continue

            fx_code = self._fx_for_pin(pin)
            if pin_type == "float":
                context.setValues(
                    fx_code, addr, encode_float32_little_swap(float(raw_value))
                )
            elif pin_type in ("uint32", "int32"):
                signed = pin_type == "int32"
                words = self._encode_int32_le(int(raw_value), signed)
                context.setValues(fx_code, addr, words)
            elif (fx_code, addr) not in self.initial_registers_16:
                context.setValues(fx_code, addr, [int(raw_value) & 0xFFFF])

    def _assert_pin_addresses_fit(self, block_size: int, pins: list[dict]):
        base_addr = int(self.config.get("base_address", 0))
        for pin in pins:
            if "offset" not in pin:
                continue
            addr = base_addr + int(pin["offset"])
            width = self._word_width_for_type(pin.get("type"))
            assert addr + 1 + width <= block_size, (
                f"pin {pin.get('name', '<unnamed>')} at {addr} width={width} "
                f"exceeds datastore size {block_size}"
            )

    @staticmethod
    def _encode_float32_le_swap(value: float) -> list[int]:
        return encode_float32_little_swap(value)

    @staticmethod
    def _encode_int32_le(value: int, signed: bool) -> list[int]:
        if signed:
            raw = (value & 0xFFFFFFFF).to_bytes(4, "little", signed=False)
        else:
            raw = int(value & 0xFFFFFFFF).to_bytes(4, "little", signed=False)
        low_word = int.from_bytes(raw[0:2], "little")
        high_word = int.from_bytes(raw[2:4], "little")
        return [low_word & 0xFFFF, high_word & 0xFFFF]
