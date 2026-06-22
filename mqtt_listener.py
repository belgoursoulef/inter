import json
import os
import time
import threading

import paho.mqtt.client as mqtt


MQTT_BROKER   = os.environ.get("MQTT_BROKER",   "10.0.0.11")
MQTT_PORT     = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TOPIC    = os.environ.get("MQTT_TOPIC",    "application/2/device/+/event/up")

latest_data = {}
data_lock   = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────

def _search(d, *keys):
    """Return the first value found among keys, searching d and d['object'] / d['data']."""
    for src in (d, d.get("object", {}), d.get("data", {})):
        if not isinstance(src, dict):
            continue
        for k in keys:
            if k in src:
                return src[k]
    return None


def extract_device_id(payload):
    info = payload.get("deviceInfo", {})
    return (info.get("devEui")
            or payload.get("devEUI")
            or payload.get("device_id")
            or "unknown_device")


def extract_device_name(payload):
    info = payload.get("deviceInfo", {})
    return info.get("deviceName") or info.get("name") or None


def extract_application_id_or_name(payload, topic=""):
    # 1. Try payload deviceInfo (ChirpStack v4)
    info = payload.get("deviceInfo", {})
    if info.get("applicationId"):
        return str(info.get("applicationId"))
    if info.get("applicationName"):
        return str(info.get("applicationName"))

    # 2. Try payload top-level (ChirpStack v3)
    if payload.get("applicationID"):
        return str(payload.get("applicationID"))
    if payload.get("applicationName"):
        return str(payload.get("applicationName"))

    # 3. Try topic parsing (e.g. application/2/device/...)
    if topic:
        parts = topic.split("/")
        if len(parts) >= 2 and parts[0] == "application":
            return parts[1]

    return None


def extract_all_sensor_fields(payload):
    """
    Return a dict of every known sensor field found anywhere in the payload.
    Keys left out if value is None.
    """
    fields = {
        "temperature":  _search(payload, "temperature", "temp", "Temperature"),
        "humidity":     _search(payload, "humidity",    "hum",  "Humidity"),
        "pressure":     _search(payload, "pressure",    "pres", "Pressure"),
        "battery":      _search(payload, "battery",     "bat",  "batteryLevel", "battery_level"),
        "rssi":         (payload.get("rxInfo") or [{}])[0].get("rssi")
                        if isinstance(payload.get("rxInfo"), list) else
                        _search(payload, "rssi", "RSSI"),
        "snr":          (payload.get("rxInfo") or [{}])[0].get("snr")
                        if isinstance(payload.get("rxInfo"), list) else
                        _search(payload, "snr", "SNR", "loRaSNR"),
        "spreadingFactor": _search(payload, "spreadingFactor", "sf"),
        "fCnt":         payload.get("fCnt"),
        "fPort":        payload.get("fPort"),
    }
    # also expose any extra keys inside object/data that aren't already covered
    covered = {
        "temperature", "temp", "Temperature",
        "humidity",    "hum",  "Humidity",
        "pressure",    "pres", "Pressure",
        "battery",     "bat",  "batteryLevel", "battery_level",
        "rssi", "snr", "spreadingFactor", "sf",
    }
    for src_key in ("object", "data"):
        src = payload.get(src_key, {})
        if isinstance(src, dict):
            for k, v in src.items():
                if k not in covered and v is not None:
                    fields[k] = v
    return {k: v for k, v in fields.items() if v is not None}


# ── MQTT callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print(f"[MQTT] ❌ Connexion refusée, code: {reason_code}")
        return
    print(f"[MQTT] ✅ Connecté à {MQTT_BROKER}:{MQTT_PORT}")
    # Subscribe to configured topic AND a broad wildcard so we never miss messages
    for topic in (MQTT_TOPIC, "application/#", "#"):
        try:
            result, mid = client.subscribe(topic)
            if result == 0:
                print(f"[MQTT] Abonné: {topic}")
                break   # stop at the first successful subscription
        except Exception as e:
            print(f"[MQTT] Erreur subscribe {topic}: {e}")


def on_disconnect(client, userdata, reason_code, properties=None):
    print(f"[MQTT] Déconnecté (code {reason_code}), reconnexion automatique...")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("Message ignoré (JSON invalide) sur", msg.topic)
        return

    # Filter by application (Société 2)
    app_info = extract_application_id_or_name(payload, msg.topic)
    if app_info not in ("2", "societe2", "société2"):
        return

    device_id   = extract_device_id(payload)
    device_name = extract_device_name(payload)
    timestamp   = payload.get("time") or payload.get("received_at")
    fields      = extract_all_sensor_fields(payload)

    record = {
        "device_id":   device_id,
        "device_name": device_name,
        "timestamp":   timestamp,
        "topic":       msg.topic,
        "fields":      fields,          # all sensor readings
        # keep top-level temperature for backward-compat with existing API consumers
        "temperature": fields.get("temperature"),
    }

    print("Reçu:", record)

    with data_lock:
        latest_data[device_id] = record


# ── entry point ───────────────────────────────────────────────────────────────

def start_mqtt_listener():
    while True:
        try:
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            if MQTT_USERNAME:
                client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

            client.on_connect    = on_connect
            client.on_disconnect = on_disconnect
            client.on_message    = on_message

            print(f"[MQTT] Tentative de connexion à {MQTT_BROKER}:{MQTT_PORT}...")
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"[MQTT] ❌ Échec: {e}")
            print(f"[MQTT] Vérifiez que MQTT_BROKER={MQTT_BROKER} est accessible depuis le conteneur.")
            print(f"[MQTT] Nouvelle tentative dans 10s...")
            time.sleep(10)
