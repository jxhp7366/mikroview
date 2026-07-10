"""MikroView — Dashboard de monitoreo MikroTik multi-servidor."""
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

def get_server_id():
    """Obtiene server_id del query param o usa el default."""
    sid = request.args.get("server", "")
    if sid and sid.isdigit():
        return int(sid)
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

# --- API Servidores ---
@app.route("/api/servers")
@login_required
def api_servers():
    servers = mk.get_servers()
    return jsonify({"success": True, "data": servers})

@app.route("/api/servers/add", methods=["POST"])
@login_required
def api_servers_add():
    data = request.get_json() or {}
    ok = mk.add_server(
        name=data.get("name", ""),
        host=data.get("host", ""),
        port=int(data.get("port", 8822)),
        username=data.get("username", "hermes"),
        key_path=data.get("key_path") or None,
    )
    return jsonify({"success": ok, "error": "" if ok else "Nombre duplicado"})

@app.route("/api/servers/<int:server_id>/remove", methods=["POST"])
@login_required
def api_servers_remove(server_id):
    mk.remove_server(server_id)
    return jsonify({"success": True})

@app.route("/api/servers/<int:server_id>/default", methods=["POST"])
@login_required
def api_servers_default(server_id):
    mk.set_default(server_id)
    return jsonify({"success": True})

# --- API Endpoints ---
@app.route("/api/status")
@login_required
def api_status():
    try:
        data = mk.get_system_status(get_server_id())
        return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/interfaces")
@login_required
def api_interfaces():
    try:
        sid = get_server_id()
        ifaces = mk.get_interfaces(sid)
        wan_names = ("ether1", "ether5", "sfp-sfpplus1")
        wan = [i for i in ifaces if i.get("name") in wan_names]
        return jsonify({"success": True, "data": {"wan": wan}, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/clients")
@login_required
def api_clients():
    try:
        sid = get_server_id()
        data = mk.get_pppoe_clients(sid)
        # Guardar snapshot para historial
        mk.snapshot_pppoe(mk.get_server(sid)["id"], data["total"])
        return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/clients/history")
@login_required
def api_clients_history():
    try:
        sid = get_server_id()
        server = mk.get_server(sid)
        period = request.args.get("period", "1h")
        data = mk.get_pppoe_history(server["id"], period)
        return jsonify({"success": True, "data": data, "server": server["name"], "period": period})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/logs")
@login_required
def api_logs():
    try:
        logs = mk.get_recent_logs(get_server_id(), 100)
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
        return jsonify({"success": True, "data": {"total_logs": len(logs), "threats": threats[-20:]}, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/all")
@login_required
def api_all():
    try:
        sid = get_server_id()
        status = mk.get_system_status(sid)
        ifaces = mk.get_interfaces(sid)
        clients = mk.get_pppoe_clients(sid)
        wan_names = ("ether1", "ether5", "sfp-sfpplus1")
        wan = []
        for name in wan_names:
            match = next((i for i in ifaces if i.get("name") == name), None)
            wan.append({"name": name, "running": match.get("running", False) if match else False, "comment": match.get("comment", "") if match else ""})
        return jsonify({"success": True, "data": {"status": status, "wan": wan, "clients": clients["total"]}, "ts": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --- Certificados ---
CERTS_DIR = os.path.join(os.path.dirname(__file__), "certs")

@app.route("/certs/download")
@login_required
def certs_download():
    import io, zipfile
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    key_pem = os.path.join(CERTS_DIR, "key.pem")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for fname in [cert_pem, key_pem]:
            if os.path.exists(fname):
                zf.write(fname, os.path.basename(fname))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="mikroview-certs.zip")

@app.route("/certs/ca")
@login_required
def certs_ca():
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    if os.path.exists(cert_pem):
        return send_file(cert_pem, mimetype="application/x-pem-file", as_attachment=True, download_name="mikroview-ca.pem")
    return "Certificado no encontrado", 404

# --- Health ---
@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now().isoformat()})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

def ensure_certs():
    cert_pem = os.path.join(CERTS_DIR, "cert.pem")
    key_pem = os.path.join(CERTS_DIR, "key.pem")
    if not os.path.exists(cert_pem) or not os.path.exists(key_pem):
        os.makedirs(CERTS_DIR, exist_ok=True)
        from subprocess import run
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key_pem, "-out", cert_pem, "-days", "3650", "-subj", "/C=EC/ST=Imbabura/L=Ibarra/O=MikroView/CN=mikroview.local"], capture_output=True)

if __name__ == "__main__":
    ensure_certs()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
