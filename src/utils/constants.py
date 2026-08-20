REGISTER_TYPE_ALIASES = {
    "coil": "coil",
    "coils": "coil",
    "discrete": "discrete",
    "discrete_input": "discrete",
    "discrete_inputs": "discrete",
    "holding": "holding",
    "holding_register": "holding",
    "holding_registers": "holding",
    "input": "input",
    "input_register": "input",
    "input_registers": "input",
}

REGISTER_TYPE_MAP = {
    "coil": "co",
    "discrete": "di",
    "holding": "hr",
    "input": "ir",
}

FC_CODE_MAP = {
    "co": 1,
    "di": 2,
    "hr": 3,
    "ir": 4,
}


def normalize_register_type(register_type: str | None, default: str = "holding") -> str:
    value = (register_type or default).lower()
    normalized = REGISTER_TYPE_ALIASES.get(value)
    if normalized is None:
        raise ValueError(f"Unsupported register_type: {register_type}")
    return normalized


def fx_code_for_register_type(
    register_type: str | None, default: str = "holding"
) -> int:
    normalized = normalize_register_type(register_type, default)
    return FC_CODE_MAP[REGISTER_TYPE_MAP[normalized]]
