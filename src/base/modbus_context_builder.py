from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext

from utils.constants import FC_CODE_MAP, REGISTER_TYPE_MAP


class ModbusContextBuilder:
    def __init__(self, config: dict):
        self.config = config
        self.device_type = config.get("type", "").lower()
        self.register_type = config.get("register_type", "holding").lower()
        self.reg_key = REGISTER_TYPE_MAP[self.register_type]
        self.fx_code = FC_CODE_MAP[self.reg_key]
        self.max_addr = 0

    def build(self) -> tuple[ModbusSlaveContext, int]:
        initial_registers = self._collect_initial_registers() or {}  # Fail-safe: None -> {}
        # Fail-safe: when empty, set block_size = 1 to avoid a zero-length DataBlock
        block_size = (max(initial_registers.keys()) + 1) if initial_registers else 1

        context = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * block_size),
            co=ModbusSequentialDataBlock(0, [0] * block_size),
            hr=ModbusSequentialDataBlock(0, [0] * block_size),
            ir=ModbusSequentialDataBlock(0, [0] * block_size),
        )

        for addr, val in initial_registers.items():
            context.setValues(self.fx_code, addr, [val])

        return context, self.fx_code

    def _collect_initial_registers(self) -> dict[int, int] | None:
        """
        Collect initial registers:
        1) inverter: reuse the original initial_registers behavior
        2) pins: usable for all device types (including io_module, power_meter, etc.)
        3) also support aliases holding_registers / input_registers / discrete_registers
        """
        # 1) Keep legacy inverter behavior
        if self.device_type == "inverter":
            raw_map = self.config.get("initial_registers", {}) or {}
            return {int(addr): int(val) & 0xFFFF for addr, val in raw_map.items()}

        # 2) Try pins or a collection name that matches register_type
        #    e.g. holding_registers / input_registers / discrete_registers
        pins = self.config.get("pins")
        if pins is None:
            alt_key = f"{self.reg_key}_registers"  # e.g. "holding_registers"
            pins = self.config.get(alt_key)

        if not pins:
            # Legacy io_module branch (in case old configs only used pins under io_module)
            if self.device_type == "io_module":
                return {}
            # Other types also without pins → return empty dict (avoid None)
            return {}

        base_addr = self.config.get("base_address", 0)
        result: dict[int, int] = {}
        self.max_addr = 0

        for pin in pins:
            offset = pin.get("offset")
            if offset is None:
                continue

            addr = base_addr + int(offset)
            self.max_addr = max(self.max_addr, addr)

            # Prefer io_module convention: if n1/n2/n3 and value (engineering value) are provided, back-calculate raw
            raw_value = pin.get("value")
            n1 = pin.get("n1")
            n2 = pin.get("n2")
            n3 = pin.get("n3")

            if raw_value is not None:
                if n1 is not None or n2 is not None or n3 is not None:
                    n1 = float(pin.get("n1", 0))
                    n2 = float(pin.get("n2", 1))
                    n3 = float(pin.get("n3", 0))
                    val = self._compute_raw_from_value(raw_value, n1, n2, n3)
                else:
                    # No scaling parameters -> treat as raw (uint16)
                    val = int(raw_value)

                # Ensure uint16 (including signed integers)
                val &= 0xFFFF
                result[addr] = val

            # If a profile exists (used only to update max_addr / later threads), no value processing here
            profile = pin.get("profile", {})
            if isinstance(profile, dict):
                self.max_addr = max(self.max_addr, addr)

        return result

    @staticmethod
    def _compute_raw_from_value(value: float | int, n1: float, n2: float, n3: float) -> int:
        """Reverse compute raw value from a scaled value using n1, n2, n3."""
        if n3 not in (0, 0.0):
            raise NotImplementedError("Quadratic decoding not supported.")
        raw = int(round((float(value) - n1) / n2))
        if raw < 0:
            raw = (raw + 0x10000) & 0xFFFF  # 2's complement to uint16
        return raw
