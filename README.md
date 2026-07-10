# MikroView

Dashboard de monitoreo para MikroTik RouterOS.

## Características

- 📊 Métricas en tiempo real (CPU, RAM, clientes PPPoE)
- 🔗 Estado de interfaces WAN (UP/DOWN)
- 🔒 Autenticación con login
- 📥 Descarga de certificados SSL para importar al MikroTik
- 📡 Alertas de seguridad (loops, sync attacks, port scans)
- 🌐 Acceso público con HTTPS

## Instalación

```bash
git clone https://github.com/andres/mikroview.git
cd mikroview
pip install -r requirements.txt
cp .env.example .env
# Editar .env con datos del MikroTik
nano .env
```

## Configuración

```env
MIKROTIK_HOST=177.53.213.185
MIKROTIK_PORT=8822
MIKROTIK_USER=hermes
MIKROTIK_KEY=/home/user/.ssh/hermes_mikrotik
ADMIN_USER=admin
ADMIN_PASSWORD=tuc...>
PORT=5050
```

## Uso

```bash
python3 app.py
# Dashboard en http://localhost:5050
```

## Importar certificado en MikroTik

1. Acceder al dashboard → botón "SSL" → descargar CA
2. En el MikroTik: `/certificate import file-name=mikroview-ca.pem`
3. El MikroTik confiará en el dashboard por HTTPS

## Stack

- Python 3 + Flask
- Flask-Login
- Paramiko (SSH)
- Bootstrap 5 (dark theme)
- Chart.js
