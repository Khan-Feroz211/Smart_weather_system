import json
from pathlib import Path

from multi_hazard import CrisisCommunicationSystem


ROOT = Path(__file__).resolve().parents[1]


def _alert(level="orange"):
    return {
        "alert_required": True,
        "overall_risk_level": level,
        "location": "Lahore",
        "active_hazards": ["extreme_heat_heatwave", "high_wind_storm"],
        "risk_levels": {
            "extreme_heat_heatwave": level,
            "high_wind_storm": level,
        },
    }


def test_urdu_sms_is_gated_and_within_sms_limit():
    communication = CrisisCommunicationSystem()

    assert communication.generate_urdu_sms_alert(_alert("green")) == ""
    assert len(communication.generate_urdu_sms_alert(_alert("orange"))) <= 160
    assert len(communication.generate_urdu_sms_alert(_alert("red"))) <= 160


def test_translation_assets_include_map_and_alert_strings():
    for locale in ("en", "ur"):
        path = ROOT / "static" / "i18n" / f"{locale}.json"
        translations = json.loads(path.read_text(encoding="utf-8"))
        assert "map.wind_particles" in translations
        assert "alerts.active" in translations


def test_map_script_contains_requested_particle_limits():
    script = (ROOT / "static" / "js" / "map.js").read_text(encoding="utf-8")

    assert "MAX_PARTICLES = 3000" in script
    assert "MAX_AGE = 90" in script
    assert "requestAnimationFrame" in script
    assert "destination-in" in script
    assert "/api/weather/grid" in script
