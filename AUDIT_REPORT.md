# Informe de Auditoría — whisper_transcriber SaaS
**Fecha:** 2026-09-26 · **Alcance:** `backend/` (FastAPI + PostgreSQL/Supabase) y
cliente (`ui/main_window.py`, `core/backend_client.py`) · **Contexto:** post-migración
SaaS, DB en Supabase (`whisper-transcriber`, sa-east-1), JWT argon2 30 días.

## 1. Resumen del estado actual — APTO con observaciones

| Área | Veredicto |
|---|---|
| Auth JWT (`get_current_user`) | ✅ Sólida. Protege `/credits`, `/transcribe`, `/payments/preference`. 401 con `WWW-Authenticate`, expiración e inexistencia de usuario manejadas. |
| Webhook Mercado Pago | ✅ HMAC-SHA256 + anti-replay + `compare_digest` intactos. Idempotencia por `UNIQUE(payment_id)`. No tocar. |
| Sesiones SQLAlchemy | ✅ `get_db()` con `yield` + `close()` en `finally`. Pool default (5) sano para el pooler Supavisor `:6543`. Sin leaks detectados. |
| Errores Groq/MP | ✅ 503 sin key, 402 sin saldo, 502 ante fallo externo. Sanitizados en esta auditoría (ya no exponen `str(exc)`). |
| Hilos UI | ✅ Todo HTTP (login, registro, créditos, checkout, transcripción) corre en `threading.Thread` + cola `poll_queue` vía `after(100)`. Sin bloqueo del main thread. |
| Resiliencia de red | ✅ `BackendUnavailableError` → `messagebox`, sin crashes. Fallback Online→Local intacto. Todos los `requests` tienen `timeout`. |

Quick-wins aplicados en `chore: apply quick-wins from security and ui audit`:
`proxy_groq` (lectura única de saldo, 500/502 genéricos + log interno,
parseo seguro de `BACKEND_MAX_UPLOAD_BYTES`), `auth` (warning si falta
`JWT_SECRET_KEY`), `credits` (removido parámetro muerto `checkpoint`),
`alembic/env.py` (import muerto), UI (limpieza del password en memoria tras
auth, guarda `isinstance` en `poll_queue`).

## 2. Vulnerabilidades y deuda crítica (requieren refactorización mayor)

1. **Sin rate-limit en `/auth/login` y `/auth/register` (ALTA).** argon2 es
   costoso a propósito (~100 ms); un atacante puede amplificar DoS y forzar
   credenciales. Acción: `slowapi` (5 intentos/min por IP) o rate-limit en el
   proxy/exposición pública. Sin esto, no exponer el backend a internet abierta.
2. **JWT de 30 días sin revocación (MEDIA).** No hay refresh rotation ni
   blacklist: un token robado vale 30 días y "cerrar sesión" no existe ni en
   servidor ni en cliente. Acción: access corto + refresh rotativo, o tabla
   `revoked_tokens` consultada en `get_current_user`.
3. **JWT secret con fallback conocido (MEDIA, mitigada).** Si `JWT_SECRET_KEY`
   no está definida, el servidor firmaba con un secreto visible en el repo.
   Ahora emite `WARNING` al arrancar; el paso final es fallar (`RuntimeError`)
   cuando el backend corre en modo producción.
4. **Enumeración de usuarios por timing (BAJA).** Login devuelve el mismo 401,
   pero `verify_password` solo corre si el email existe (diferencia medible).
   Acción futura: hash dummy siempre.
5. **`TRANSCRIBER_AUTH_TOKEN` en disco plano (BAJA).** El `.env` raíz guarda el
   JWT en texto claro. Acción futura: keyring del SO.
6. **`apply_payment_once_db` trata todo `IntegrityError` como duplicado
   (BAJA).** Una violación FK (usuario borrado en vuelo) se reporta igual que
   un reintento. Acción: inspeccionar `exc.orig` (código 23505 vs 23503).

## 3. Mejoras de UX pendientes (no bloqueantes)

- **Sin botón "Cerrar sesión"**: imposible cambiar de cuenta sin reiniciar.
  Añadir logout (limpiar token + etiquetas) es el mayor quick-win de UX restante.
- **Saldo no se refresca tras comprar**: el checkout abre el navegador y la UI
  no re-consulta; el usuario debe re-loguear. Añadir botón "Actualizar saldo".
- **Modo Online sin progreso en vivo**: un solo salto a 0.95 al terminar.
  Mostrar estado indeterminado (`progress_bar` pulsante o texto "Subiendo…").
- **Strings y colores hardcodeados** en `main_window.py` (textos ES, `#hex`).
  Centralizar en `ui/theme.py` + `ui/strings.py` antes de i18n o rebrand.
- **Ventana fija 720x780 no redimensionable**: en pantallas pequeñas corta la
  vista previa; hacerla redimensionable con `grid` elástico.
- **`_detail()` muestra `response.text` crudo**: ante un 500 del backend
  (HTML), el messagebox es ilegible. Truncar a ~300 caracteres.

## 4. Espacio para Logo

**Ubicación exacta:** `ui/main_window.py`, bloque `header_frame`, **líneas 108–115**.
Hoy la cabecera es solo texto (`title_label` 24pt + `subtitle_label`). El punto
de inserción es inmediatamente después de la línea 109
(`self.header_frame.grid(...)`), antes de `title_label`:

```python
from PIL import Image
self.logo_image = ctk.CTkImage(
    light_image=Image.open("assets/logo.png"),
    dark_image=Image.open("assets/logo.png"),
    size=(64, 64),
)
self.logo_label = ctk.CTkLabel(self.header_frame, text="", image=self.logo_image)
self.logo_label.pack(side="left", padx=(0, 12))
```

**Dimensiones recomendadas:** `size=(64, 64)` px en UI (fuente PNG de 256 px para
nitidez HiDPI). Layout: logo a la izquierda (`side="left"`), título+subtítulo a
su derecha (moverlos a un sub-frame si se quiere alineación vertical perfecta).
Existe precedente: `assets/app_icon.png` ya vive en el repo como base del icono.
Requiere `Pillow` (ya es dependencia transitiva de customtkinter).
