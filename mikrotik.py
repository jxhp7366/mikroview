"""Cliente SSH para MikroTik RouterOS."""
import subprocess, os, time, json
from functools import lru_cache

HOST = os.getenv("MIKROTIK_HOST", "177.53.213.185")
PORT = os.getenv("MIKROTIK_PORT", "8822")
USER = os.getenv("MIKROTIK_USER", "hermes")
KEY = os.getenv("MIKROTIK_KEY", os.path.expanduser("~/.ssh/hermes_mikrotik"))

BASE_SSH = [
    "ssh", "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
    "-o", "PasswordAuthentication=no",
    "-i", KEY, "-p", str(PORT),
    f"{USER}@{HOST}"
]

def _ssh(cmd: str) -> str:
    """Ejecuta un comando en el MikroTik y devuelve stdout."""
    result = subprocess.run(
        BASE_SSH + [cmd],
        capture_output=True, text=True, timeout=12
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH error: {result.stderr.strip()}")
    return result.stdout


def parse_running(line: str) -> bool:
    """Detecta si una interfaz está running desde su línea de flags."""
    flags = line.split()[1] if len(line.split()) > 1 else ""
    return "R" in flags.replace("RS", "").replace("DR", "")


def get_system_status() -> dict:
    """CPU, RAM, uptime, versión."""
    out = _ssh("/system resource print")
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
        "cpu": cpu,
        "memory_total_mb": round(mem_total / 1024 / 1024, 1),
        "memory_free_mb": round(mem_free / 1024 / 1024, 1),
        "memory_used_pct": round((1 - mem_free/mem_total) * 100, 1) if mem_total else 0,
        "uptime": data.get("uptime", ""),
        "version": data.get("version", ""),
        "board": data.get("board-name", ""),
    }


def get_interfaces() -> list:
    """Lista de interfaces con estado."""
    out = _ssh("/interface print detail without-paging")
    ifaces = []
    current = None
    for line in out.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        # Nueva interfaz (línea con número)
        if stripped[0].isdigit() and len(stripped.split()) >= 2:
            if current:
                ifaces.append(current)
            parts = stripped.split()
            running = "R" in parts[1] if len(parts) > 1 else False
            comment = stripped.split(";;;")[1].strip() if ";;;" in stripped else ""
            current = {
                "name": parts[-1] if parts[-1] != "" else parts[-2],
                "running": running,
                "comment": comment,
            }
        elif current:
            if "name=" in stripped:
                name = stripped.split("name=")[1].split()[0].strip('"')
                current["name"] = name
            if "type=" in stripped:
                current["type"] = stripped.split("type=")[1].split()[0].strip('"')
            if "actual-mtu=" in stripped:
                current["mtu"] = stripped.split("actual-mtu=")[1].split()[0]
    if current:
        ifaces.append(current)
    return ifaces


def get_pppoe_clients() -> dict:
    """Clientes PPPoE activos."""
    out = _ssh("/ppp active print without-paging")
    clients = []
    for line in out.split('\n'):
        if 'pppoe' in line.lower() and '<pppoe-' in line:
            name = line.split('<pppoe-')[1].split('>')[0] if '<pppoe-' in line else ""
            addr = line.split()[-1] if line.split() else ""
            clients.append({"name": name, "address": addr})
    return {"total": len(clients), "clients": clients[:100]}


def get_recent_logs(limit: int = 100) -> list:
    """Últimos logs del MikroTik."""
    out = _ssh(f"/log print without-paging last={limit}")
    logs = []
    for line in out.split('\n'):
        if line.strip() and not line.strip().startswith('#'):
            logs.append(line.strip())
    return logs[-50:]


def _parse_bytes(s: str) -> float:
    """Convierte string como '1024.0MiB' a bytes."""
    s = s.strip()
    multipliers = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
                   "KB": 1000, "MB": 1000**2, "GB": 1000**3}
    for unit, mult in multipliers.items():
        if s.endswith(unit):
            return float(s.replace(unit, "")) * mult
    try:
        return float(s)
    except ValueError:
        return 0


# Cache simple (TTL 15s)
_cache = {}
_cache_time = {}

def cached(key: str, ttl: int = 15):
    """Decorador de cache simple con TTL."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            now = time.time()
            if key in _cache and (now - _cache_time.get(key, 0)) < ttl:
                return _cache[key]
            result = fn(*args, **kwargs)
            _cache[key] = result
            _cache_time[key] = now
            return result
        return wrapper
    return decorator
