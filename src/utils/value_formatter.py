from datetime import datetime


def decode_value(raw: int, n1: float, n2: float, n3: float) -> float:
    return n1 + n2 * raw + n3 * (raw**2)


def simulate_log(msg: str):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")


def format_decoded_value(pin_name: str, raw: int) -> str:
    match pin_name:
        case name if "Temp" in name:
            value = raw * 0.01220703125
            return f"{value:.2f}°C"
        case name if "Pressure" in name:
            value = raw * 0.006103515625
            return f"{value:.2f} bar"
        case name if "Humidity" in name:
            value = raw * 0.0390625
            return f"{value:.2f}%RH"
        case name if "Current" in name:
            return f"{raw:.0f} A"
        case name if "Voltage" in name:
            value = raw * 0.390625
            return f"{value:.0f} V"
        case name if "CO2" in name:
            value = raw * 0.01
            return f"{value:.2f}% CO₂"
        case name if "PM2_5" in name or "PM10" in name:
            value = raw * 0.2
            return f"{value:.1f} μg/m³"
        case name if "Light" in name:
            value = raw * 0.4
            return f"{value:.0f} lux"
        case name if "Noise" in name:
            value = raw * 0.1 + 30
            return f"{value:.1f} dB"
        case name if "Vibration" in name:
            value = raw * 0.01
            return f"{value:.2f} mm/s²"
        case name if "AI_Spare" in name:
            return f"{raw:.0f}"
        case _:
            return f"{raw}"
