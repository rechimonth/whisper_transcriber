# AGENTS.md — Memoria del proyecto whisper_transcriber

## Especificaciones del hardware del usuario (Windows)
- SO: Windows 10 Pro (build 19045), PC basado en x64.
- Equipo: TOSHIBA Satellite L655 (móvil, ~2010).
- CPU: Intel Core i7 M 640 @ 2.80GHz — 2 núcleos / 4 hilos lógicos. Arquitectura
  Arrandale (1ª gen Core i7). Sin AVX2 (solo hasta SSE4.2). MUY limitado para
  inferencia Whisper local.
- RAM: 8 GB total, ~1 GB disponible en uso normal. Justo para modelos Whisper.
- GPU: Intel HD Graphics integrada — **sin CUDA**. La inferencia debe ser CPU-only.
- Usuario Windows: `wingz` (C:\Users\wingz). Ruta del proyecto histórica:
  `C:\Users\wingz\.gemini\antigravity-ide\scratch\whisper_transcriber`.

## Implicaciones para el rendimiento
- `device="cpu"` es obligatorio (no hay CUDA).
- `compute_type="int8"` es la opción más rápida para CPU (ya usada).
- Modelos: "tiny" (rápido, peor calidad), "base" (balance, actual), "small"
  (mejor calidad pero ~3x más lento — inviable en esta CPU).
- La lentitud extrema proviene de: CPU de 2010 con 4 hilos + poca RAM libre +
  beam_size=5 + sin VAD.

## Configuración óptima para esta CPU (aplicada en core/transcriber.py)
- `compute_type="int8"`, `device="cpu"`, `cpu_threads=4`, `num_workers=1`.
- `vad_filter=True` (descarta silencios, no pierde calidad de lo hablado).
- `beam_size=1` (greedy; ~2-3x más rápido que beam_size=5, pérdida de calidad
  <1% WER). Dejar beam_size configurable por el usuario si quiere máxima calidad.

## Alternativa de aceleración recomendada (plataforma externa gratuita)
- **Groq Whisper API** (console.groq.com): ejecuta Whisper-large-v3 en hardware
  LPU, mucho más rápido que tiempo real y con tier gratuito generoso. Calidad
  SUPERIOR al modelo "base" local. Requiere API key gratuita. Es la mejor
  opción "cerebro terciarizado gratuito" para este hardware limitado.
- OpenAI Whisper API: $0.006/min (no gratis, pero barato).
- Cuando se implemente: modo "online" (Groq) con fallback a modo "local" sin internet.

## Estructura del proyecto
- `main.py` — entrada; configura logging (FileHandler app.log + StreamHandler) y
  bootstrap de streams (pythonw tiene stdout/stderr=None).
- `core/transcriber.py` — `WhisperTranscriber` (faster-whisper), export TXT/SRT.
- `ui/main_window.py` — GUI CustomTkinter + DnD (tkinterdnd2 con fallback),
  hilo worker, cola de progreso, `_BufferLogHandler` para captura de logs.
- `build/create_icon.py` — genera `assets/app_icon.ico`/`.png` con Pillow.
- `build/create_desktop_shortcut.py` — crea .lnk (Win) / .desktop (Linux) con icono.
- `ejecutar_app.bat` — lanzador portable con venv auto-creado (pythonw.exe).
- `ejecutar_app_debug.bat` — lanzador en consola (python.exe) para diagnóstico.

## Notas de git
- Remoto: github.com/rechimonth/whisper_transcriber (token fine-grained con
  contents:write configurado en el remote URL).
- `app.log` y `__pycache__` están en `.gitignore`.
