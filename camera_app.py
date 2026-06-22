import os, sys, time, datetime, threading, json, socket, base64, urllib.request

# Force UTF-8 I/O regardless of the system locale
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"

import cv2, mysql.connector
from flask import Flask, Response, render_template, request, redirect, url_for, send_file, session, jsonify
from mqtt_listener import start_mqtt_listener, latest_data, data_lock
from functools import wraps

# --- Config ---
DB_HOST     = os.environ.get('DB_HOST', 'db')
DB_USER     = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'rootpassword')
DB_NAME     = os.environ.get('DB_NAME', 'carhorizon')

camera_frames = {"Camera 1 (Entrance)": None, "Camera 2 (Garage)": None}

latest_access_scan = {"timestamp": 0.0, "badge": "", "employee": "", "service": "", "status": ""}

FALLBACK_EMPLOYEES = {
    "EMP001": {"nom": "BELGOUR",  "prenom": "Aicha Soulef",      "service": "IT",            "color": "blue",  "initials": "AB"},
    "EMP002": {"nom": "ROLIN",    "prenom": "Tom",                "service": "Production",    "color": "amber", "initials": "TR"},
    "EMP003": {"nom": "Balde",    "prenom": "Mamadou",            "service": "Administratif", "color": "green", "initials": "MB"},
    "EMP004": {"nom": "Diahouila","prenom": "Ferancel Iverson",   "service": "Production",    "color": "amber", "initials": "FD"},
    "EMP005": {"nom": "Jacaton",  "prenom": "Paul",               "service": "IT",            "color": "blue",  "initials": "PJ"},
}

FALLBACK_DB_LOGS = [
    {"time": "09:14:32", "level": "INFO",    "message": "Access GRANTED - Aicha Soulef BELGOUR (IT)"},
    {"time": "08:12:03", "level": "WARNING", "message": "Access DENIED - Unknown badge scanned: BADGE-4921X"},
    {"time": "00:00:01", "level": "INFO",    "message": "Systeme de surveillance demarre"},
]

# --- DB helpers ---
def db_query(query, params=(), fetch=False, commit=False, dictionary=False):
    conn = cursor = None
    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, connect_timeout=2)
        if conn.is_connected():
            cursor = conn.cursor(dictionary=dictionary)
            cursor.execute(query, params)
            if commit: conn.commit()
            if fetch: return cursor.fetchall()
    except Exception as e:
        print(f"DB Error: {e}")
        if commit: raise
    finally:
        if cursor: cursor.close()
        if conn and conn.is_connected(): conn.close()
    return None

def get_employees():
    rows = db_query("SELECT badge_id, nom, prenom, service, color, initials FROM employees", fetch=True, dictionary=True)
    return {r['badge_id']: {k: r[k] for k in r if k != 'badge_id'} for r in rows} if rows else FALLBACK_EMPLOYEES

def insert_log(device, level, msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    FALLBACK_DB_LOGS.insert(0, {"time": ts, "level": level, "message": msg})
    if len(FALLBACK_DB_LOGS) > 30: FALLBACK_DB_LOGS.pop()
    db_query("INSERT INTO device_logs (device_name, log_level, message) VALUES (%s, %s, %s)", (device, level, msg), commit=True)

# --- Brevo email alert ---
def send_alert(subject, body, attachment_bytes=None):
    api_key    = os.environ.get('BREVO_API_KEY')
    emails_raw = os.environ.get('NOTIFICATION_EMAILS') or os.environ.get('NOTIFICATION_EMAIL', '')
    recipients = [{"email": e.strip()} for e in emails_raw.split(',') if e.strip()]
    if not api_key or not recipients:
        return
    _orig = socket.getaddrinfo
    socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _orig(h, p, socket.AF_INET, t, pr, fl)
    try:
        att = [{"name": "alert.jpg", "content": base64.b64encode(attachment_bytes).decode()}] if attachment_bytes else []
        payload = json.dumps({
            "sender":      {"name": "Car Horizon Security", "email": "carhorizonalert@gmail.com"},
            "to":          recipients,
            "subject":     subject,
            "textContent": body,
            **( {"attachment": att} if att else {})
        }).encode()
        req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=payload,
                                     headers={"api-key": api_key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"Alert sent [{subject}]: {r.read().decode()}")
    except Exception as e:
        print(f"Alert error: {e}")
    finally:
        socket.getaddrinfo = _orig

# --- Badge scan ---
def process_badge_scan(badge_id):
    now      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    emps     = get_employees()
    emp      = emps.get(badge_id)
    valid    = emp is not None
    statut   = "AUTORISE" if valid else "REFUSE"
    emp_name = f"{emp['prenom']} {emp['nom']}" if valid else "Inconnu"
    service  = emp['service'] if valid else "Inconnu"
    msg      = f"Access GRANTED - {emp_name} ({service})" if valid else f"Access DENIED - Unknown badge: {badge_id}"
    print(f"[{now}] {statut}: {msg}")
    insert_log("Camera 1 (Entrance)", "INFO" if valid else "WARNING", msg)
    if not valid:
        threading.Thread(target=send_alert, args=(
            f"[ALERTE QR INCONNU] Car Horizon - {now}",
            f"QR code inconnu scanne a {now}.\nCode: {badge_id}\n\nCar Horizon Security"
        ), daemon=True).start()
    try:
        with open("historique_acces.csv", "a", encoding="utf-8") as f:
            f.write(f"{now},{badge_id},{statut}\n")
    except Exception as e:
        print(f"CSV error: {e}")
    latest_access_scan.update({"timestamp": time.time(), "badge": badge_id,
                                "employee": emp_name, "service": service, "status": statut})
    return statut, emp_name

# --- Background threads ---
def run_badge_scanner():
    src = os.environ.get('CAMERA_URL_1', "rtsp://192.168.32.98/live2.sdp")
    src = int(src) if src.isdigit() else src
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    qr = cv2.QRCodeDetector()
    last_badge, last_time = None, 0
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(5); cap.release(); cap = cv2.VideoCapture(src); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1); continue
        ok, jpeg = cv2.imencode('.jpg', frame)
        if ok: camera_frames["Camera 1 (Entrance)"] = jpeg.tobytes()
        try:
            data, _, _ = qr.detectAndDecode(frame)
        except cv2.error:
            data = None
        if data:
            now = time.time()
            if data != last_badge or now - last_time > 3:
                last_badge, last_time = data, now
                process_badge_scan(data)
        time.sleep(0.01)

def run_intrusion_alarm():
    src = os.environ.get('CAMERA_URL_2', "rtsp://192.168.32.99/live2.sdp")
    src = int(src) if src.isdigit() else src
    cap = cv2.VideoCapture(src)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    last_alert = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(5); cap.release(); cap = cv2.VideoCapture(src); cap.set(cv2.CAP_PROP_BUFFERSIZE, 1); continue
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (400, int(400 * h / w)))
        rects, _ = hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        for (x, y, rw, rh) in rects:
            sx = w / 400
            cv2.rectangle(frame, (int(x*sx), int(y*sx)), (int((x+rw)*sx), int((y+rh)*sx)), (0, 0, 255), 2)
        ok, jpeg = cv2.imencode('.jpg', frame)
        if ok: camera_frames["Camera 2 (Garage)"] = jpeg.tobytes()
        hour = datetime.datetime.now().hour
        if len(rects) > 0 and (hour >= 20 or hour < 7) and time.time() - last_alert > 30:
            last_alert = time.time()
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            insert_log("Camera 2 (Garage)", "ALERT", "Intrusion detected - Human silhouette during off-hours!")
            threading.Thread(target=send_alert, args=(
                f"[ALERTE INTRUSION] Car Horizon - {now}",
                f"Intrusion detectee (silhouette humaine) a {now} dans le garage.\n\nCar Horizon Security",
                jpeg.tobytes() if ok else None
            ), daemon=True).start()
            try:
                with open("historique_intrusion.csv", "a", encoding="utf-8") as f: f.write(f"{now},INTRUSION\n")
            except Exception as e: print(e)
        time.sleep(0.01)

# --- Flask app ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'carhorizon-secret-key-2026')

# Ensure every response carries an explicit UTF-8 charset header
@app.after_request
def set_utf8_charset(response):
    ct = response.content_type
    if 'text/' in ct and 'charset' not in ct:
        response.content_type = ct + '; charset=utf-8'
    return response

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        return f(*a, **kw) if session.get('authenticated') else redirect(url_for('login'))
    return wrap

def gen_feed(cam):
    while True:
        fb = camera_frames.get(cam)
        if fb: yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + fb + b'\r\n'
        time.sleep(0.01)

def read_access_logs():
    logs = []
    if os.path.exists("historique_acces.csv"):
        try:
            emps = get_employees()
            with open("historique_acces.csv", "r", encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    parts = line.strip().split(',')
                    if len(parts) >= 3:
                        ts, badge, status = parts[0], parts[1], parts[2]
                        emp = emps.get(badge, {})
                        logs.append({"time": ts.split(" ")[1] if " " in ts else ts, "badge": badge,
                                     "employee": f"{emp.get('prenom','')} {emp.get('nom','')}".strip() or "—",
                                     "service": emp.get('service', 'Inconnu'), "status": status})
        except Exception as e: print(e)
    return logs[:8] or [
        {"time": "09:14:32", "badge": "EMP001", "employee": "Aicha Soulef BELGOUR", "service": "IT",            "status": "AUTORISE"},
        {"time": "09:02:17", "badge": "EMP002", "employee": "Tom ROLIN",             "service": "Production",    "status": "AUTORISE"},
        {"time": "08:12:03", "badge": "UNKNWN", "employee": "—",                     "service": "Inconnu",       "status": "REFUSE"},
    ]

def get_kpis():
    logs = read_access_logs()
    auth = sum(1 for l in logs if l['status'] == 'AUTORISE')
    denied = len(logs) - auth
    active = len(set(l['badge'] for l in logs if l['status'] == 'AUTORISE' and l['badge'] in get_employees()))
    intrusions = 0
    if os.path.exists("historique_intrusion.csv"):
        try:
            with open("historique_intrusion.csv") as f: intrusions = len(f.readlines())
        except: pass
    total = auth + denied
    return {"authorized": auth, "denied": denied, "active_employees": active,
            "intrusions": intrusions, "total_scans": total,
            "auth_rate": round(auth / total * 100) if total else 100}

def get_device_logs():
    rows = db_query("SELECT timestamp, log_level, message FROM device_logs ORDER BY timestamp DESC LIMIT 30", fetch=True)
    if rows:
        return [{"time": r[0].strftime("%H:%M:%S") if isinstance(r[0], datetime.datetime) else str(r[0]),
                 "level": r[1], "message": r[2]} for r in rows]
    return FALLBACK_DB_LOGS

# --- 2FA helpers ---
import random
import secrets

def send_otp_email(otp_code):
    """Send the OTP code via Brevo email to all notification recipients."""
    api_key    = os.environ.get('BREVO_API_KEY')
    emails_raw = os.environ.get('NOTIFICATION_EMAILS') or os.environ.get('NOTIFICATION_EMAIL', '')
    recipients = [{"email": e.strip()} for e in emails_raw.split(',') if e.strip()]
    if not api_key or not recipients:
        print(f"[2FA] OTP (no email configured): {otp_code}")
        return
    _orig = socket.getaddrinfo
    socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _orig(h, p, socket.AF_INET, t, pr, fl)
    try:
        payload = json.dumps({
            "sender":      {"name": "Car Horizon Security", "email": "carhorizonalert@gmail.com"},
            "to":          recipients,
            "subject":     "Car Horizon — Code de vérification",
            "htmlContent": f"""
            <div style="font-family:sans-serif;max-width:400px;margin:auto;padding:30px;background:#0d1117;color:#e8ecf4;border-radius:8px;">
              <h2 style="color:#3d8eff;letter-spacing:2px;">CAR<span style="color:#e8ecf4">HORIZON</span></h2>
              <p style="color:#6b7a99;font-size:14px;">Votre code de vérification à usage unique :</p>
              <div style="font-size:42px;font-weight:700;letter-spacing:12px;color:#00d97e;padding:20px 0;text-align:center;">
                {otp_code}
              </div>
              <p style="color:#6b7a99;font-size:12px;">Ce code expire dans <strong>5 minutes</strong>. Ne le partagez avec personne.</p>
            </div>
            """
        }).encode()
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[2FA] OTP email sent: {r.read().decode()}")
    except Exception as e:
        print(f"[2FA] Email error: {e}")
        print(f"[2FA] OTP fallback (check logs): {otp_code}")
    finally:
        socket.getaddrinfo = _orig

# --- Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('surveillance'))
    error = None
    if request.method == 'POST':
        if request.form.get('password') == os.environ.get('APP_PASSWORD', 'pepweb-1'):
            # Password OK — generate OTP and redirect to 2FA
            otp = str(random.randint(100000, 999999))
            session['otp_code']    = otp
            session['otp_expires'] = time.time() + 300  # 5 minutes
            session.pop('authenticated', None)
            threading.Thread(target=send_otp_email, args=(otp,), daemon=True).start()
            return redirect(url_for('two_factor'))
        error = "Mot de passe incorrect."
    return render_template('login.html', error=error)

@app.route('/2fa', methods=['GET', 'POST'])
def two_factor():
    if session.get('authenticated'):
        return redirect(url_for('surveillance'))
    # If no OTP in session, go back to login
    if not session.get('otp_code'):
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()
        if time.time() > session.get('otp_expires', 0):
            session.pop('otp_code', None)
            session.pop('otp_expires', None)
            return redirect(url_for('login'))
        if entered == session.get('otp_code'):
            session.pop('otp_code', None)
            session.pop('otp_expires', None)
            session['authenticated'] = True
            return redirect(url_for('surveillance'))
        error = "Code incorrect ou expiré."
    return render_template('2fa.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@app.route('/surveillance')
@login_required
def surveillance():
    return render_template("index.html", employees=get_employees(),
                           access_logs=read_access_logs(), device_logs=get_device_logs(), kpis=get_kpis())

@app.route('/video_feed/1')
def video_feed_1():
    fb = camera_frames.get("Camera 1 (Entrance)")
    return Response(gen_feed("Camera 1 (Entrance)"), mimetype='multipart/x-mixed-replace; boundary=frame') if fb else ("Offline", 404)

@app.route('/video_feed/2')
def video_feed_2():
    fb = camera_frames.get("Camera 2 (Garage)")
    return Response(gen_feed("Camera 2 (Garage)"), mimetype='multipart/x-mixed-replace; boundary=frame') if fb else ("Offline", 404)

@app.route('/api/latest_scan')
@login_required
def api_latest_scan():
    return jsonify(latest_access_scan)

@app.route('/api/mqtt')
@login_required
def api_mqtt():
    with data_lock:
        return jsonify(list(latest_data.values()))

@app.route('/scan/<badge_id>')
def trigger_scan(badge_id):
    statut, emp_name = process_badge_scan(badge_id)
    return {"status": "triggered", "badge": badge_id, "employee": emp_name, "access_status": statut}


def get_or_create_csv(filename, headers):
    if not os.path.exists(filename):
        with open(filename, "w", encoding="utf-8") as f: f.write(headers + "\n")
    return send_file(filename, mimetype='text/csv', as_attachment=True, download_name=filename)

@app.route('/download/acces')
@login_required
def download_acces():
    return get_or_create_csv("historique_acces.csv", "horodatage,badge_id,statut")

@app.route('/download/intrusion')
@login_required
def download_intrusion():
    return get_or_create_csv("historique_intrusion.csv", "horodatage,statut")

# --- Start background threads ---
threading.Thread(target=run_badge_scanner,  daemon=True).start()
threading.Thread(target=run_intrusion_alarm, daemon=True).start()
mqtt_thread = threading.Thread(target=start_mqtt_listener, daemon=True)
mqtt_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
