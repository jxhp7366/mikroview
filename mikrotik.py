"""Cliente SSH multi-servidor para MikroTik RouterOS."""
import subprocess, os, time, json, sqlite3
from functools import lru_cache

DB_PATH = os.path.join(os.path.dirname(__file__), "servers.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Crea la tabla de servidores y agrega el default si no existe."""
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            host TEXT NOT NULL,
            port INTEGER DEFAULT 8822,
            username TEXT DEFAULT 'hermes',
            key_path TEXT,
            is_default INTEGER DEFAULT 0
        )
    """)
    # Insertar MK Olivo 1 como default si la tabla está vacía
    count = db.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    if count == 0:
        db.execute("""
            INSERT INTO servers (name, host, port, username, key_path, is_default)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (
            os.getenv("MIKROTIK_NAME", "MK Olivo 1"),
            os.getenv("MIKROTIK_HOST", "177.53.213.185"),
            int(os.getenv("MIKROTIK_PORT", "8822")),
            os.getenv("MIKROTIK_USER", "hermes"),
            os.getenv("MIKROTIK_KEY", os.path.expanduser("~/.ssh/hermes_mikrotik")),
        ))
    db.commit()
    db.close()

def get_servers() -> list:
    """Lista todos los servidores configurados."""
    db = get_db()
    rows = db.execute("SELECT * FROM servers ORDER BY is_default DESC, name").fetchall()
    db.close()
    return [dict(r) for r in rows]

def get_server(server_id: int = None) -> dict:
    """Obtiene un servidor por ID, o el default."""
    db = get_db()
    if server_id:
        row = db.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()
    else:
        row = db.execute("SELECT * FROM servers WHERE is_default=1").fetchone()
        if not row:
            row = db.execute("SELECT * FROM servers LIMIT 1").fetchone()
    db.close()
    if not row:
        raise ValueError("No hay servidores configurados")
    return dict(row)

def add_server(name: str, host: str, port: int = 8822, username: str = "hermes", key_path: str = None):
    """Agrega un nuevo servidor."""
    db = get_db()
    try:
        db.execute("""
            INSERT INTO servers (name, host, port, username, key_path)
            VALUES (?, ?, ?, ?, ?)
        """, (name, host, port, username, key_path))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        db.close()

def remove_server(server_id: int):
    """Elimina un servidor."""
    db = get_db()
    db.execute("DELETE FROM servers WHERE id=? AND is_default=0", (server_id,))
    db.commit()
    db.close()

def set_default(server_id: int):
    """Establece un servidor como default."""
    db = get_db()
    db.execute("UPDATE servers SET is_default=0")
    db.execute("UPDATE servers SET is_default=1 WHERE id=?", (server_id,))
    db.commit()
    db.close()


def _ssh(server: dict, cmd: str) -> str:
    """Ejecuta un comando SSH en un servidor específico."""
    key = server.get("key_path") or os.path.expanduser(f"~/.ssh/hermes_{server['name'].lower().replace(' ','_')}")
    ssh_cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        "-o", "PasswordAuthentication=no",
        "-i", key, "-p", str(server["port"]),
        f"{server['username']}@{server['host']}",
        cmd
    ]
    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=12)
    if result.returncode != 0:
        raise RuntimeError(f"SSH error ({server['name']}): {result.stderr.strip()}")
    return result.stdout


def get_system_status(server_id: int = None) -> dict:
    """CPU, RAM, uptime, versión."""
    srv = get_server(server_id)
    out = _ssh(srv, "/system resource print")
    data = {}
    for line in out.split('\n'):
        line = line.strip()
        if ':' in line:
            k, v = line.split(':', 1)
            data[k.strip()] = v.strip()
    cpu = int(data.get("cpu-load", "0").replace("%", ""))
    mem_total = _parse_bytes(data.get("total-memory", "0"))
    mem_free = _parse_bytes(data.get("free-memory", "0"))
    return {
        "server_name": srv["name"],
        "server_id": srv["id"],
        "cpu": cpu,
        "memory_total_mb": round(mem_total / 1024 / 1024, 1),
        "memory_free_mb": round(mem_free / 1024 / 1024, 1),
        "memory_used_pct": round((1 - mem_free/mem_total) * 100, 1) if mem_total else 0,
        "uptime": data.get("uptime", ""),
        "version": data.get("version", ""),
        "board": data.get("board-name", ""),
    }


def get_interfaces(server_id: int = None) -> list:
    """Lista de interfaces con estado."""
    srv = get_server(server_id)
    out = _ssh(srv, "/interface print detail without-paging")
    ifaces = []
    current = None
    for line in out.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0].isdigit() and len(stripped.split()) >= 2:
            if current:
                ifaces.append(current)
            parts = stripped.split()
            running = "R" in parts[1] if len(parts) > 1 else False
            comment = stripped.split(";;;")[1].strip() if ";;;" in stripped else ""
            current = {"name": parts[-1], "running": running, "comment": comment}
        elif current:
            if "name=" in stripped:
                current["name"] = stripped.split("name=")[1].split()[0].strip('"')
            if "type=" in stripped:
                current["type"] = stripped.split("type=")[1].split()[0].strip('"')
    if current:
        ifaces.append(current)
    return ifaces


def get_pppoe_clients(server_id: int = None) -> dict:
    """Clientes PPPoE activos."""
    srv = get_server(server_id)
    out = _ssh(srv, "/ppp active print without-paging")
    clients = []
    for line in out.split('\n'):
        if 'pppoe' in line.lower() and '<pppoe-' in line:
            name = line.split('<pppoe-')[1].split('>')[0] if '<pppoe-' in line else ""
            addr = line.split()[-1] if line.split() else ""
            clients.append({"name": name, "address": addr})
    return {"total": len(clients), "clients": clients[:100]}


def get_recent_logs(server_id: int = None, limit: int = 100) -> list:
    """Últimos logs."""
    srv = get_server(server_id)
    out = _ssh(srv, f"/log print without-paging last={limit}")
    return [l.strip() for l in out.split('\n') if l.strip() and not l.strip().startswith('#')][-50:]


def _parse_bytes(s: str) -> float:
    s = s.strip()
    multipliers = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
                   "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    for unit, mult in multipliers.items():
        if s.endswith(unit):
            return float(s.replace(unit, "")) * mult
    try: return float(s)
    except ValueError: return 0


# Cache simple
_cache = {}
_cache_time = {}

def cached_get(key: str, ttl: int = 15, fn=None, *args, **kwargs):
    now = time.time()
    if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
        return _cache[key]
    result = fn(*args, **kwargs)
    _cache[key] = result
    _cache_time[key] = now
    return result


# --- Historial PPPoE ---
def init_history_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS pppoe_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            total_clients INTEGER NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES servers(id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pppoe_time ON pppoe_history(server_id, recorded_at)")
    db.commit()
    db.close()

def snapshot_pppoe(server_id: int, total: int):
    """Guarda un snapshot de clientes PPPoE."""
    db = get_db()
    db.execute("INSERT INTO pppoe_history (server_id, total_clients) VALUES (?, ?)", (server_id, total))
    db.commit()
    db.close()
    # Limpiar datos viejos (más de 60 días)
    db = get_db()
    db.execute("DELETE FROM pppoe_history WHERE recorded_at < datetime('now', '-60 days')")
    db.commit()
    db.close()

def get_pppoe_history(server_id: int, period: str = "1h") -> list:
    """Obtiene historial de clientes PPPoE según período."""
    periods = {
        "1h": ("-1 hour", 60),     # cada 1 min
        "8h": ("-8 hours", 300),   # cada 5 min
        "24h": ("-24 hours", 900), # cada 15 min
        "1w": ("-7 days", 3600),   # cada 1 hora
        "1m": ("-30 days", 14400), # cada 4 horas
    }
    since, bucket_secs = periods.get(period, periods["1h"])
    
    db = get_db()
    rows = db.execute("""
        SELECT 
            (strftime('%s', recorded_at) / ?) * ? as bucket,
            AVG(total_clients) as avg_clients,
            MIN(recorded_at) as ts
        FROM pppoe_history
        WHERE server_id = ? AND recorded_at > datetime('now', ?)
        GROUP BY bucket
        ORDER BY bucket
    """, (bucket_secs, bucket_secs, server_id, since)).fetchall()
    db.close()
    
    return [{"time": r["ts"], "clients": round(r["avg_clients"])} for r in rows]


# Inicializar DB al importar
init_db()
init_history_db()
