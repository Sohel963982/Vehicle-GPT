from pathlib import Path
import cantools

DBC = Path(__file__).resolve().parents[1] / "dbc" / "vehicle.dbc"


def load():
    return cantools.database.load_file(str(DBC))


def test_messages_exist():
    db = load()
    names = {m.name for m in db.messages}
    assert names == {"Powertrain", "Thermal", "ADAS"}


def test_powertrain_roundtrip():
    db = load()
    values = {"Speed_kmh": 72.5, "Battery_SoC": 80.0, "Power_kW": -12.3}
    data = db.encode_message("Powertrain", values)
    back = db.decode_message("Powertrain", data)
    assert abs(back["Speed_kmh"] - 72.5) < 0.01
    assert abs(back["Battery_SoC"] - 80.0) < 0.01
    assert abs(back["Power_kW"] - (-12.3)) < 0.1


def test_thermal_roundtrip_negative_temperature():
    db = load()
    values = {"Batt_Temp_C": 52.37, "Coolant_Pump_Pct": 35.0,
              "Ambient_C": -5.0, "HVAC_kW": 2.4}
    data = db.encode_message("Thermal", values)
    back = db.decode_message("Thermal", data)
    assert abs(back["Batt_Temp_C"] - 52.37) < 0.01
    assert abs(back["Ambient_C"] - (-5.0)) < 0.01


def test_adas_roundtrip():
    db = load()
    data = db.encode_message(
        "ADAS", {"LKA_Active": 0, "Takeover_Req": 1, "Disengage_Reason": 2}
    )
    back = db.decode_message("ADAS", data)
    assert back["Disengage_Reason"] == 2
    assert back["Takeover_Req"] == 1


def test_frame_ids():
    db = load()
    assert db.get_message_by_name("Powertrain").frame_id == 256
    assert db.get_message_by_name("Thermal").frame_id == 512
    assert db.get_message_by_name("ADAS").frame_id == 768