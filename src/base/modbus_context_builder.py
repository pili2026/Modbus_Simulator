from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext
import struct

from utils.constants import FC_CODE_MAP, REGISTER_TYPE_MAP


class ModbusContextBuilder:
    def __init__(self, config: dict):
        self.config = config
        self.device_type = config.get("type", "").lower()
        self.register_type = config.get("register_type", "holding").lower()
        self.reg_key = REGISTER_TYPE_MAP[self.register_type]
        self.fx_code = FC_CODE_MAP[self.reg_key]

        self.max_end_addr = 0
        self.initial_registers_16 = {}

    def build(self) -> tuple[ModbusSlaveContext, int]:
        pins = self._get_pins_or_alt()

        self._collect_initial_from_pins(pins)

        legacy_init = self._collect_legacy_initials()
        self.initial_registers_16.update(legacy_init)

        legacy_max = (max(self.initial_registers_16.keys()) + 1) if self.initial_registers_16 else 0
        block_size = max(self.max_end_addr, legacy_max, 1)

        context = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * block_size),
            co=ModbusSequentialDataBlock(0, [0] * block_size),
            hr=ModbusSequentialDataBlock(0, [0] * block_size),
            ir=ModbusSequentialDataBlock(0, [0] * block_size),
        )

        for addr, val in self.initial_registers_16.items():
            context.setValues(self.fx_code, addr, [val & 0xFFFF])

        self._write_full_initials(context, pins)

        return context, self.fx_code

    # -------- helpers --------

    def _get_pins_or_alt(self):
        pins = self.config.get("pins")
        if pins is None:
            alt_key = f"{self.reg_key}_registers"  # e.g. "holding_registers"
            pins = self.config.get(alt_key)
        return pins or []

    def _word_width_for_type(self, t: str) -> int:
        t = (t or "").lower()
        if t in ("float", "uint32", "int32"):
            return 2
        return 1

    def _collect_initial_from_pins(self, pins: list[dict]):
        base_addr = int(self.config.get("base_address", 0))
        max_end = 0

        for pin in pins:
            if "offset" not in pin:
                continue
            addr = base_addr + int(pin["offset"])
            width = self._word_width_for_type(pin.get("type"))

            max_end = max(max_end, addr + width)
            raw_value = pin.get("value")
            ptype = (pin.get("type") or "").lower()

            if raw_value is not None and ptype not in ("float", "uint32", "int32"):

                val16 = int(raw_value) & 0xFFFF
                self.initial_registers_16[addr] = val16

        self.max_end_addr = max_end

    def _collect_legacy_initials(self) -> dict[int, int]:
        if self.device_type == "inverter":
            raw_map = self.config.get("initial_registers", {}) or {}
            return {int(a): int(v) & 0xFFFF for a, v in raw_map.items()}
        return {}

    def _write_full_initials(self, context: ModbusSlaveContext, pins: list[dict]):
        base_addr = int(self.config.get("base_address", 0))

        for pin in pins:
            if "offset" not in pin:
                continue
            addr = base_addr + int(pin["offset"])
            ptype = (pin.get("type") or "").lower()
            raw_value = pin.get("value")
            if raw_value is None:
                continue

            if ptype == "float":
                words = self._encode_float32_le_swap(float(raw_value))
                context.setValues(self.fx_code, addr, words)
            elif ptype in ("uint32", "int32"):
                signed = ptype == "int32"
                words = self._encode_int32_le(int(raw_value), signed)
                context.setValues(self.fx_code, addr, words)
            else:
                if addr not in self.initial_registers_16:
                    context.setValues(self.fx_code, addr, [int(raw_value) & 0xFFFF])

    # ---- encoders ----
    @staticmethod
    def _encode_float32_le_swap(value: float) -> list[int]:
        b = struct.pack("<f", float(value))
        lo = int.from_bytes(b[0:2], "little")
        hi = int.from_bytes(b[2:4], "little")
        return [hi & 0xFFFF, lo & 0xFFFF]

    @staticmethod
    def _encode_int32_le(value: int, signed: bool) -> list[int]:
        if signed:
            b = (value & 0xFFFFFFFF).to_bytes(4, "little", signed=False)
        else:
            b = int(value & 0xFFFFFFFF).to_bytes(4, "little", signed=False)
        lo = int.from_bytes(b[0:2], "little")
        hi = int.from_bytes(b[2:4], "little")
        return [lo & 0xFFFF, hi & 0xFFFF]
