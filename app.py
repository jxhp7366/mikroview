"""MikroView — Dashboard de monitoreo MikroTik."""
import os, secrets, json
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
import mikrotik as mk

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# --- Auth Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Usuario único (admin)
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "mikroview2029")

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    if user_id == "1":
        return User("1", ADMIN_USER)
    return None

# --- Rutas de Autenticación ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USER and password == ADMIN_PASSWORD:
            user = User("1", ADMIN_USER)
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        flash("Usuario o contraseña incorrectos", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- Dashboard ---
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

# --- API Endpoints ---
@app.route("/api/status")
@login_required
def api_status():
    try:
        data = mk.get_system_status()
        return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/interfaces")
@login_required
def api_interfaces():
    try:
        ifaces = mk.get_interfaces()
        # Clasificar
        wan = [i for i in ifaces if i.get("name") in ("ether1", "ether5", "sfp-sfpplus1")]
        pppoe = [i for i in ifaces if "pppoe" in i.get("type", "").lower()]
        return jsonify({
            "success": True,
            "data": {
                "wan": wan,
                "pppoe_count": len(pppoe),
                "total_running": sum(1 for i in ifaces if i.get("running")),
            },
            "ts": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/clients")
@login_required
def api_clients():
    try:
        data = mk.get_pppoe_clients()
        return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/logs")
@login_required
def api_logs():
    try:
        logs = mk.get_recent_logs(100)
        # Clasificar alertas
        threats = []
        keywords = {
            "critical": ["loop", "flood", "sync attack", "ddos"],
            "high": ["port scan", "link down", "interface down", "ospf"],
            "medium": ["login failure", "brute force", "failed"],
        }
        for line in logs:
            ll = line.lower()
            for severity, kws in keywords.items():
                for kw in kws:
                    if kw in ll:
                        threats.append({"severity": severity, "message": line[:200]})
                        break
        return jsonify({
            "success": True,
            "data": {"total_logs": len(logs), "threats": threats[-20:]},
            "ts": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/all")
@login_required
def api_all():
    """Endpoint unificado para el dashboard (una sola llamada)."""
    try:
        status = mk.get_system_status()
        ifaces = mk.get_interfaces()
        clients = mk.get_pppoe_clients()
        
        wan = []
        for name in ("ether1", "ether5", "sfp-sfpplus1"):
            match = next((i for i in ifaces if i.get("name") == name), None)
            wan.append({
                "name": name,
                "running": match.get("running", False) if match else False,
                "comment": match.get("comment", "") if match else "",
            })
        
        return jsonify({
            "success": True,
            "data": {
                "status": status,
                "wan": wan,
                "clients": clients["total"],
            },
            "ts": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- Certificados ---
CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")

@app.route("/certs/download")
@login_required
def certs_download():
    """Descarga zip con certificados."""
    import io, zipfile
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    key_pem = os.path.join(CERTS_DIR, "key.pem")
    
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for fname in [cert_pem, key_pem]:
            if os.path.exists(fname):
                zf.write(fname, os.path.basename(fname))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="mikroview-certs.zip")

@app.route("/certs/ca")
@login_required
def certs_ca():
    """Descarga solo el certificado CA (para importar en MikroTik)."""
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    if os.path.exists(cert_pem):
        return send_file(cert_pem, mimetype="application/x-pem-file",
                         as_attachment=True, download_name="mikroview-ca.pem")
    return "Certificado no encontrado", 404

# --- Health Check (público) ---
@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now().isoformat()})

# --- Error handlers ---
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

# --- Generar certificados al iniciar ---
def ensure_certs():
    """Genera certificado auto-firmado si no existe."""
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    key_pem = os.path.join(CERTS_DIR, "key.pem")
    if not os.path.exists(cert_pem) or not os.path.exists(key_pem):
        os.makedirs(CERTS_DIR, exist_ok=True)
        from subprocess import run
        run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", key_pem, "-out", cert_pem, "-days", "3650",
            "-subj", "/C=EC/ST=Imbabura/L=Ibarra/O=MikroView/CN=mikroview.local"
        ], capture_output=True)

if __name__ == "__main__":
    ensure_certs()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
