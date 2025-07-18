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
        initial_registers = self._collect_initial_registers()
        block_size = max(initial_registers.keys(), default=0) + 1
        context = ModbusSlaveContext(
            di=ModbusSequentialDataBlock(0, [0] * block_size),
            co=ModbusSequentialDataBlock(0, [0] * block_size),
            hr=ModbusSequentialDataBlock(0, [0] * block_size),
            ir=ModbusSequentialDataBlock(0, [0] * block_size),
        )
        for addr, val in initial_registers.items():
            context.setValues(self.fx_code, addr, [val])
        return context, self.fx_code

    def _collect_initial_registers(self) -> dict[int, int]:
        if self.device_type == "inverter":
            return {int(addr): val for addr, val in self.config.get("initial_registers", {}).items()}

        if self.device_type == "io_module":
            base_addr = self.config.get("base_address", 0)
            result = {}
            self.max_addr = 0

            for pin in self.config.get("pins", []):
                offset = pin.get("offset")
                if offset is None:
                    continue
                addr = base_addr + int(offset)
                self.max_addr = max(self.max_addr, addr)
                raw_value = pin.get("value")
                if raw_value is not None:
                    n1 = float(pin.get("n1", 0))
                    n2 = float(pin.get("n2", 1))
                    n3 = float(pin.get("n3", 0))
                    val = self._compute_raw_from_value(raw_value, n1, n2, n3)
                    result[addr] = val

                profile = pin.get("profile", {})
                if isinstance(profile, dict):
                    max_val = profile.get("max")
                    if max_val is not None:
                        self.max_addr = max(self.max_addr, addr)

            return result

    @staticmethod
    def _compute_raw_from_value(value: float, n1: float, n2: float, n3: float) -> int:
        """Reverse compute raw value from scaled value using n1, n2, n3."""
        if n3 != 0:
            raise NotImplementedError("Quadratic decoding not supported.")
        return int(round((value - n1) / n2))
