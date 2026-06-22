import json
import os
import time
import threading

import paho.mqtt.client as mqtt


MQTT_BROKER = os.environ.get("MQTT_BROKER", "10.0.0.11")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "application/+/device/A840412051896D52/event/up")

latest_data = {}
data_lock = threading.Lock()


def extract_temperature(payload):
    obj = payload.get("object", {})
    for key in ("temperature", "temp", "Temperature"):
        if key in obj:
            return obj[key]
    data = payload.get("data", {})
    for key in ("temperature", "temp"):
        if key in data:
            return data[key]
    return None


def extract_device_id(payload):
    device_info = payload.get("deviceInfo", {})
    return device_info.get("devEui") or payload.get("devEUI") or "unknown_device"


def on_connect(client, userdata, flags, reason_code, properties=None):
    print("Connecté MQTT, code:", reason_code)
    client.subscribe(MQTT_TOPIC)
    print("Abonné au topic:", MQTT_TOPIC)


def on_disconnect(client, userdata, reason_code, properties=None):
    print("Déconnecté MQTT, reconnexion automatique en cours...")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("Message ignoré (JSON invalide) sur", msg.topic)
        return

    device_id = extract_device_id(payload)
    temp = extract_temperature(payload)
    timestamp = payload.get("time") or payload.get("received_at")

    if temp is None:
        print(f"Aucune température trouvée pour {device_id}. Payload reçu: {payload}")
        return

    record = {
        "device_id": device_id,
        "temperature": temp,
        "timestamp": timestamp,
        "topic": msg.topic,
    }

    print("Reçu:", record)

    with data_lock:
        latest_data[device_id] = record


def start_mqtt_listener():
    while True:
        try:
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
            if MQTT_USERNAME:
                client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.on_message = on_message

            print(f"MQTT: Connexion à {MQTT_BROKER}:{MQTT_PORT}...")
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"MQTT: Échec de connexion ({e}), nouvelle tentative dans 10s...")
            time.sleep(10)

