# 🎙️ Whisper Transcriber — TranscriptorVideoIA

> **SaaS de transcripción de audio y video con IA.** Convierte cualquier archivo multimedia en texto en minutos: 100% privado en local con Whisper en CPU, o ultra-rápido en la nube con Whisper Large en Groq. Autenticación real, billetera de créditos, pagos con Mercado Pago y cliente de escritorio distribuible como `.exe`.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-F55036?style=for-the-badge&logoColor=white)
![Mercado Pago](https://img.shields.io/badge/Mercado_Pago-00B1EA?style=for-the-badge&logoColor=white)
![CustomTkinter](https://img.shields.io/badge/CustomTkinter-2CC5FF?style=for-the-badge&logoColor=white)

---

## 🏗️ Arquitectura y Stack Tecnológico

```text
┌─────────────────────────────────┐
│  Desktop · CustomTkinter        │  Threading + Queue (sin bloqueo UI)
│  Sidebar + Dashboard · CTkImage │  Fallback Online → Local
└───────────────┬─────────────────┘
                │ Bearer JWT (30 días)
                ▼
┌─────────────────────────────────┐      ┌──────────────┐
│  FastAPI · SQLAlchemy 2.0       ├─────►│  Groq API    │
│  auth · credits · proxy · pagos │      │  Whisper L.  │
│  SlowAPI · Webhooks MP          ├─────►│ Mercado Pago │
└───────────────┬─────────────────┘      └──────────────┘
                │ pooler :6543 + Alembic
                ▼
┌─────────────────────────────────┐
│  Supabase · PostgreSQL 17       │
│  users · wallets · transactions │
│  token_blocklist                │
└─────────────────────────────────┘
```

### 🖥️ Frontend — Desktop Client (`ui/`, `core/`)
- **CustomTkinter** con layout comercial: barra lateral (logo, tarjeta de créditos, estado) + dashboard principal responsivo (redimensionable, grid elástico).
- **Resiliencia de red:** todo HTTP corre en `threading.Thread` con cola de mensajes al Main Thread; ante caída del backend muestra `messagebox` y el modo Online hace **fallback automático a Local**.
- **QA geométrico:** `tests/test_ui_layout.py` valida bounding boxes reales (cero superposiciones, todo encuadrado) con triple render.
- **Distribución:** compilado con PyInstaller (`build_app.spec`, windowed, assets empaquetados) → `.exe` en `dist/`.

### ⚡ Backend — REST API (`backend/`)
- **FastAPI + SQLAlchemy 2.0** con sesiones por request (`Depends(get_db)`, cierre garantizado).
- **Despliegue optimizado:** `render.yaml` (Blueprint + health check) y `Procfile` (Railway/Heroku). Comando: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.

### 🗄️ Base de Datos — Supabase (PostgreSQL 17)
- Conexión vía **pooler Supavisor IPv4 `:6543`** (la directa es solo IPv6).
- **Migraciones Alembic** versionadas (`users`, `credit_wallets`, `transactions`, `token_blocklist`).
- Operaciones de saldo **atómicas** (`SELECT … FOR UPDATE` + commit único).

---

## 🛡️ Seguridad y Protección (DevSecOps)

| Capa | Implementación |
|---|---|
| **Auth** | Email + password con **Argon2**, JWT HS256 de 30 días con `jti` único |
| **Revocación** | **`token_blocklist`** en DB: `POST /auth/logout` revoca el `jti`; `get_current_user` lo verifica en cada request (401 si revocado) + purga de expirados |
| **Rate Limiting** | **SlowAPI** por IP: `5/minute` en login, `3/minute` en registro (frena DoS sobre Argon2 y fuerza bruta) → `429` |
| **Webhooks** | HMAC-SHA256 (`x-signature` + anti-replay), verificación de importe/moneda vs paquete, idempotencia por `UNIQUE(payment_id)` |
| **Secretos** | Cero hardcodeados: todo vía `os.getenv`; `.env` gitignored; `JWT_SECRET_KEY` ausente = warning al arrancar |
| **Errores** | Groq/MP mapeados a `502`/`503`/`402` con mensajes genéricos; trazas solo en logs del servidor |

---

## 💳 Motor de Monetización — Billetera de Créditos

1. Al registrarse, el usuario recibe **60 créditos de bienvenida** en la misma transacción que crea su cuenta.
2. El coste es `ceil(minutos × CREDITS_PER_MINUTE)` (mínimo 1).
3. **Compra:** `POST /payments/preference` crea el Checkout Pro con `external_reference = transcriber:{user_id}:{paquete}:{nonce}`.
4. **Acreditación en tiempo real:** el webhook valida firma → confirma pago aprobado en la API de MP → inserta `Transaction('purchase')` y suma al `CreditWallet` **atómicamente**. Reenvíos duplicados devuelven el saldo sin doble cobro.
5. **Consumo:** `POST /transcribe` verifica saldo → transcribe en Groq → débito atómico con `Transaction('usage')`. Sin saldo → `402`; si Groq falla, **no se descuenta nada**.

---

## 🚀 Guía de Uso Rápido

### Opción A — Ejecutable (recomendado)
Descarga el instalador listo para usar desde la pestaña de [**Releases**](https://github.com/rechimonth/whisper_transcriber/releases): `TranscriptorVideoIA.exe`. No requiere Python. El modo Local funciona sin cuenta; el modo Online pide registro + créditos.

### Opción B — Backend en desarrollo local

```bash
# 1. Entorno y dependencias
py -m venv backend\.venv
backend\.venv\Scripts\activate
pip install -r backend\requirements.txt

# 2. Configuración (ver backend/.env.example)
copy backend\.env.example backend\.env   # completa DATABASE_URL y JWT_SECRET_KEY

# 3. Migraciones y arranque
cd backend
python -m alembic upgrade head
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Verifica con `GET http://127.0.0.1:8000/health` → `{"status":"ok",...}` y la doc interactiva en `/docs`.

### Endpoints principales

| Método | Endpoint | Auth | Uso |
|---|---|---|---|
| POST | `/auth/register` | No (3/min) | Crea cuenta + 60 créditos |
| POST | `/auth/login` | No (5/min, form OAuth2) | Devuelve JWT 30 días |
| POST | `/auth/logout` | Bearer | Revoca el token actual |
| GET | `/auth/me` · `/credits` | Bearer | Perfil y saldo |
| POST | `/transcribe` | Bearer | Transcribe vía Groq, descuenta créditos |
| POST | `/payments/preference` | Bearer | Checkout Pro de Mercado Pago |
| POST | `/webhooks/mercadopago` | Firma HMAC | Acreditación idempotente |

### Tests

```bash
python -m pytest tests/ backend/tests/ -q   # 13 tests: seguridad, HMAC, layout UI, E2E API
python test_e2e_api.py                       # register → login → assert saldo == 60
```

---

## 📁 Estructura

```text
├── main.py                 # Entrada del cliente desktop
├── core/                   # backend_client, transcripción local/Groq, audio
├── ui/                     # MainWindow (sidebar + dashboard CustomTkinter)
├── backend/                # FastAPI: auth, credits, proxy_groq, payments, webhooks
│   ├── database/           # SQLAlchemy + modelos + get_db
│   ├── alembic/            # Migraciones versionadas
│   └── tests/              # Tests de seguridad (429, logout/401)
├── assets/                 # Logos y fondos (CTkImage)
├── build_app.spec          # Release PyInstaller → dist/
├── render.yaml / Procfile  # Despliegue Render / Railway
└── AUDIT_REPORT.md         # Auditoría de seguridad y UX
```

**La API key de Groq vive solo en el servidor (`GROQ_API_KEY_SERVER`). El cliente jamás la recibe.** 🔒
