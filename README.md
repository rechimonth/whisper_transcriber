# TranscriptorVideoIA

TranscriptorVideoIA es una aplicación de escritorio para convertir audio y vídeo en texto rápidamente combinando dos motores:

- **Online:** Groq + Whisper, procesado por un backend seguro.
- **Local:** faster-whisper en CPU, **100% gratuito y privado**, sin enviar el archivo a un servicio externo.

El modo Online está preparado para evolucionar a un SaaS basado en **créditos de transcripción**, autenticación de usuarios y pagos mediante **Mercado Pago Checkout Pro**.

## Arquitectura

```text
┌──────────────────────────────┐
│ Desktop · CustomTkinter      │
│ Local Whisper / Online HTTP  │
└──────────────┬───────────────┘
               │ Bearer token
               ▼
┌──────────────────────────────┐
│ FastAPI                      │
│ auth · credits · proxy       │
│ Mercado Pago · webhooks      │
└───────────┬───────────┬──────┘
            │           │
            ▼           ▼
         Groq API   Mercado Pago
```

La **API key de Groq nunca debe estar en el cliente de escritorio**. El servidor utiliza `GROQ_API_KEY_SERVER`.

## Requisitos previos

- Python 3.10 o superior.
- Windows recomendado para los scripts `.bat`.
- **FFmpeg y ffprobe** instalados y disponibles en el `PATH`.
- Una cuenta de Groq para el modo Online.
- Una cuenta de Mercado Pago para probar Checkout Pro.

## Instalación del cliente de escritorio

Desde la raíz del repositorio:

```bat
py -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

El `requirements.txt` de la raíz contiene únicamente las dependencias necesarias para el **cliente de escritorio** y la compilación local: faster-whisper, CustomTkinter, drag & drop, PyInstaller, python-dotenv y el cliente HTTP.

No necesitas configurar ninguna API key de Groq para usar el modo Local.

Copia `.env.example` a `.env` y, como mínimo, configura:

```env
TRANSCRIBER_BACKEND_URL=http://localhost:8000
TRANSCRIBER_AUTH_TOKEN=
```

La variable `TRANSCRIBER_AUTH_TOKEN` es opcional. Para el MVP puedes iniciar sesión desde la propia interfaz usando el token configurado en el backend.

## Instalación del backend

Crea un entorno separado para el servidor:

```bat
py -m venv backend\.venv
backend\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

El archivo `backend/requirements.txt` instala las dependencias exclusivas del servidor: FastAPI, Uvicorn, multipart uploads, python-dotenv, Groq y el SDK oficial de Mercado Pago.

Copia:

```bat
copy backend\.env.example backend\.env
```

y completa los secretos del servidor:

```env
GROQ_API_KEY_SERVER=tu_key_de_groq
MP_ACCESS_TOKEN=tu_access_token_de_mercado_pago
MP_WEBHOOK_SECRET=tu_secreto_de_webhook
```

El resto de las variables de `backend/.env.example` permite ajustar créditos, paquetes, límites de archivos y CORS.

### Arrancar el backend

Desde la raíz del repositorio, con el entorno del backend activado:

```bat
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Comprueba:

```text
GET http://127.0.0.1:8000/health
```

La respuesta esperada es:

```json
{"status":"ok","service":"transcriptorvideoia-backend"}
```

## Uso de la aplicación

### `ejecutar_app.bat`

Es el lanzador normal de la aplicación de escritorio.

- Crea `venv` si todavía no existe.
- Instala las dependencias desde el `requirements.txt` raíz.
- Ejecuta `main.py` con `pythonw.exe`, por lo que la aplicación se abre sin una ventana de consola.
- Los logs se conservan en `app.log`.

### `ejecutar_app_debug.bat`

Es el lanzador de diagnóstico.

- Requiere que el entorno `venv` ya exista.
- Ejecuta `main.py` con `python.exe`.
- Mantiene visible la consola para inspeccionar errores y mensajes de arranque.
- Al cerrar la aplicación, deja la consola abierta para revisar el diagnóstico.

## Flujo Online

1. Arranca el backend FastAPI.
2. Abre la aplicación de escritorio.
3. Introduce el token de acceso y pulsa **Iniciar Sesión**.
4. El cliente consulta y muestra el saldo de créditos.
5. Selecciona **Online (Servidor)**.
6. Pulsa **Comprar Créditos** para generar y abrir el Checkout Pro de Mercado Pago.
7. Después del pago aprobado, el webhook del backend acredita los créditos.
8. Una transcripción Online reserva los créditos necesarios según la duración.
9. Si Groq termina correctamente, los créditos quedan consumidos.
10. Si Groq falla, el backend reintegra la reserva y el cliente puede continuar con el fallback Local.

El modo Local no depende de créditos y no necesita conexión con el backend.

## Endpoints del backend

| Método | Endpoint | Autenticación | Uso |
|---|---|---|---|
| GET | `/health` | No | Health check |
| POST | `/auth/login` | No | Valida token mock y devuelve sesión |
| GET | `/auth/me` | Bearer | Consulta el usuario actual |
| GET | `/credits` | Bearer | Consulta saldo |
| POST | `/transcribe` | Bearer | Sube audio/vídeo, valida saldo y transcribe mediante Groq |
| POST | `/payments/preference` | Bearer | Crea una preferencia de Checkout Pro |
| POST | `/webhooks/mercadopago` | Firma MP | Recibe y valida notificaciones de pagos |

## Créditos

El MVP calcula el coste con:

```text
credits = ceil(duración_en_minutos × CREDITS_PER_MINUTE)
```

Por defecto:

- `CREDITS_PER_MINUTE=1`
- saldo inicial mock: `60` créditos
- paquete `starter`: 60 créditos
- paquete `pro`: 180 créditos

Estos valores están en memoria para pruebas locales. **La persistencia real todavía debe migrarse a PostgreSQL/Supabase** antes de un lanzamiento comercial.

## Seguridad

La arquitectura separa secretos del cliente:

- El desktop **no usa** `GROQ_API_KEY`.
- El backend utiliza `GROQ_API_KEY_SERVER`.
- El webhook de Mercado Pago valida `x-signature` mediante HMAC-SHA256.
- Los `payment_id` ya procesados se ignoran de forma idempotente.
- El webhook también comprueba que importe y moneda coincidan con el paquete esperado.
- Los archivos subidos al proxy se almacenan temporalmente y se eliminan al terminar.
- El checkpoint de Groq conserva fragmentos procesados para reanudar una transcripción interrumpida.

## Pruebas rápidas

Las pruebas añadidas para esta arquitectura pueden ejecutarse con la librería estándar de Python:

```bat
python -m unittest tests.test_groq_transcriber tests.test_backend_security
```

La prueba de `test_transcriber.py` existente del proyecto valida el motor local y puede descargar/cargar el modelo Whisper; no es la prueba rápida recomendada para comprobar únicamente la arquitectura Online.

## Mercado Pago en local

Para que Mercado Pago pueda llamar al webhook, el backend debe disponer de una URL accesible desde Internet. En desarrollo local puedes utilizar un túnel HTTPS y configurar esa URL en:

```env
MP_WEBHOOK_URL=https://tu-url-publica/webhooks/mercadopago
```

En producción utiliza una URL HTTPS estable y protege los secretos exclusivamente mediante variables de entorno o un gestor de secretos.

## Roadmap

### 1. Transición a SaaS

- Migrar autenticación mock a cuentas reales.
- Sustituir `CreditStore` en memoria por PostgreSQL/Supabase.
- Registrar consumos, compras, reembolsos e historial de transcripciones.
- Añadir observabilidad, rate limiting y controles de abuso.
- Separar configuración de desarrollo, staging y producción.

### 2. Portal web de Mercado Pago

- Crear el portal web definitivo para compra y gestión de créditos.
- Integrar Checkout Pro y estados de pago en una interfaz web.
- Mostrar paquetes, saldo e historial.
- Completar el ciclo de retorno de compra y recuperación de sesión.

### 3. Sistema de cuentas de usuario

- Registro, login y recuperación de cuenta.
- Perfil y gestión de sesión.
- Sincronización de créditos entre escritorio y portal web.
- Historial de transcripciones y consumo.
- Soporte para planes gratuitos y de pago.

## Estado actual

El repositorio ya contiene la separación Desktop/Backend, proxy de Groq, créditos, Checkout Pro, webhook seguro, reintentos, paralelismo y checkpointing.

El siguiente salto para producción comercial es **persistencia real + cuentas reales + portal web + despliegue seguro del backend**.