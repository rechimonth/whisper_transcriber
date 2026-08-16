import os
import re
import threading
import queue
import logging
import io
import sys
from tkinter import filedialog, messagebox
import customtkinter as ctk

# ---------------------------------------------------------------------------
# Integración con TkinterDnD2 (drag & drop)
# ---------------------------------------------------------------------------
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
    DND_FILES = None

from core.transcriber import WhisperTranscriber, export_to_txt, export_to_srt
from core.groq_transcriber import GroqTranscriber, is_groq_available, GroqNotConfiguredError

logger = logging.getLogger(__name__)

# Extensiones válidas (incluye m4a)
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"}

# ---------------------------------------------------------------------------
# Clase base dinámica para soporte DND
# ---------------------------------------------------------------------------
if _DND_AVAILABLE:
    class _BaseWindow(TkinterDnD.DnDWrapper, ctk.CTk):
        """Combina DnDWrapper y CustomTkinter CTk."""
        def __init__(self, *args, **kwargs):
            ctk.CTk.__init__(self, *args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class _BaseWindow(ctk.CTk):
        """Fallback sin Drag & Drop."""
        pass

class _BufferLogHandler(logging.Handler):
    """Handler de logging thread-safe que vuelca los registros a un buffer de
    texto (io.StringIO) para mostrarlos en la ventana de logs de la GUI.
    logging ya serializa emit() con un lock por handler, por lo que es seguro
    usarlo desde el hilo worker de transcripcion."""

    def __init__(self, buffer):
        super().__init__()
        self._buffer = buffer

    def emit(self, record):
        try:
            msg = self.format(record) + "\n"
            self._buffer.write(msg)
        except Exception:
            # Nunca dejar que un fallo de logging rompa el hilo worker.
            self.handleError(record)


class MainWindow(_BaseWindow):
    """Ventana principal con observabilidad y captura de logs."""

    def __init__(self):
        super().__init__()
        # Configuración ventana
        self.title("Transcriptor de Audio y Video")
        self.geometry("680x720")
        self.resizable(False, False)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        # Estado interno
        self.selected_file_path = None
        self.transcription_results = None
        self.transcriber = None
        self.groq_transcriber = None
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False
        self.transcription_mode = "local"  # "local" o "groq"
        # Captura de logs
        self._log_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self._create_stream_proxy(self._original_stdout)
        sys.stderr = self._create_stream_proxy(self._original_stderr)
        # Handler de logging thread-safe que captura los registros (incluidos
        # los de faster_whisper) en el buffer de la ventana de logs.
        self._log_handler = _BufferLogHandler(self._log_buffer)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
        logging.getLogger().addHandler(self._log_handler)
        # UI
        self._create_widgets()
        if _DND_AVAILABLE:
            self._register_drop_zone()
        else:
            logger.warning("tkinterdnd2 no está instalado. Drag & Drop deshabilitado.")
        # Polling de la cola
        self.poll_queue()

    # -----------------------------------------------------------------------
    # Construcción de widgets
    # -----------------------------------------------------------------------
    def _create_widgets(self):
        """Inicializa y distribuye todos los componentes en la interfaz."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)   # Caja de vista previa expandible

        # 1. ENCABEZADO
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")
        self.title_label = ctk.CTkLabel(
            self.header_frame,
            text="Transcriptor Whisper",
            font=ctk.CTkFont(family="Helvetica", size=24, weight="bold"),
        )
        self.title_label.pack(anchor="w")
        self.subtitle_label = ctk.CTkLabel(
            self.header_frame,
            text="Formatos soportados: mp4, mkv, avi, mov, mp3, wav, m4a",
            font=ctk.CTkFont(family="Helvetica", size=12),
            text_color="#85929e",
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # 2. DROP ZONE
        self.drop_zone_outer = ctk.CTkFrame(
            self,
            fg_color="#1f3a5f",
            corner_radius=12,
        )
        self.drop_zone_outer.grid(row=1, column=0, padx=20, pady=(0, 6), sticky="ew")
        self.drop_zone_frame = ctk.CTkFrame(
            self.drop_zone_outer,
            fg_color="#1a2744",
            corner_radius=10,
        )
        self.drop_zone_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.drop_icon_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="📂",
            font=ctk.CTkFont(size=28),
        )
        self.drop_icon_label.pack(pady=(14, 0))
        self.drop_main_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="Arrastra y suelta tu archivo aquí",
            font=ctk.CTkFont(family="Helvetica", size=14, weight="bold"),
            text_color="#d0e8ff",
        )
        self.drop_main_label.pack()
        self.drop_sub_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="o haz clic para buscar",
            font=ctk.CTkFont(family="Helvetica", size=11),
            text_color="#6d8eb4",
            cursor="hand2",
        )
        self.drop_sub_label.pack(pady=(2, 0))
        self.lbl_file_name = ctk.CTkLabel(
            self.drop_zone_frame,
            text="Ningún archivo seleccionado",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="#85929e",
        )
        self.lbl_file_name.pack(pady=(6, 14))
        # Bind click on all drop zone related widgets
        for widget in (
            self.drop_zone_frame,
            self.drop_zone_outer,
            self.drop_icon_label,
            self.drop_main_label,
            self.drop_sub_label,
            self.lbl_file_name,
        ):
            widget.bind("<Button-1>", lambda e: self._select_file())

        # 3. CONTROL DE TRANSCRIPCIÓN
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        self.btn_transcribe = ctk.CTkButton(
            self.control_frame,
            text="Iniciar Transcripción",
            fg_color="#2ecc71",
            hover_color="#27ae60",
            text_color="white",
            font=ctk.CTkFont(weight="bold"),
            command=self._start_transcription,
            state="disabled",
        )
        self.btn_transcribe.pack(fill="x", pady=(0, 5))

        # 3b. SELECTOR DE MODO (Local vs Groq)
        self.mode_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        self.mode_frame.pack(fill="x", pady=(0, 5))
        self.mode_label = ctk.CTkLabel(
            self.mode_frame,
            text="Motor:",
            font=ctk.CTkFont(size=12),
        )
        self.mode_label.pack(side="left", padx=(0, 8))
        groq_ok = is_groq_available()
        self.mode_switch = ctk.CTkSegmentedButton(
            self.mode_frame,
            values=["Local (CPU)", "Online (Groq)"] if groq_ok else ["Local (CPU)"],
            command=self._on_mode_change,
        )
        self.mode_switch.set("Local (CPU)")
        self.mode_switch.pack(side="left", fill="x", expand=True)
        if groq_ok:
            logger.info("Modo Groq disponible (API key configurada y paquete groq instalado).")
        else:
            logger.info("Modo Groq no disponible: falta API key o el paquete groq. "
                        "Modo local por defecto.")

        # 4. BARRA DE PROGRESO Y ESTADO
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=3, column=0, padx=20, pady=4, sticky="ew")
        # Indicador de color (LED)
        self.status_indicator = ctk.CTkLabel(
            self.progress_frame, text="", width=20, height=20, fg_color="#6c757d"
        )
        self.status_indicator.pack(side="left", padx=(0, 10))
        # Label de texto de estado
        self.status_label = ctk.CTkLabel(
            self.progress_frame,
            text="Estado: Esperando archivo...",
            font=ctk.CTkFont(size=12),
        )
        self.status_label.pack(anchor="w")
        # Botón de log
        self.log_button = ctk.CTkButton(
            self.progress_frame,
            text="Ver Log de Errores",
            fg_color="#6c757d",
            hover_color="#5a6268",
            width=120,
            command=self._show_log_window,
        )
        self.log_button.pack(side="right", padx=(10, 0))
        # Barra de progreso
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.pack(fill="x", pady=(5, 5))
        self.progress_bar.set(0.0)

        # 5. ÁREA DE VISTA PREVIA
        self.preview_frame = ctk.CTkFrame(self)
        self.preview_frame.grid(row=5, column=0, padx=20, pady=8, sticky="nsew")
        self.preview_title = ctk.CTkLabel(
            self.preview_frame,
            text="Vista Previa de Transcripción",
            font=ctk.CTkFont(weight="bold"),
        )
        self.preview_title.pack(anchor="w", padx=10, pady=(5, 2))
        self.text_preview = ctk.CTkTextbox(
            self.preview_frame,
            font=("Consolas", 12),
            state="disabled",
        )
        self.text_preview.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 6. BOTONES DE EXPORTACIÓN
        self.export_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.export_frame.grid(row=6, column=0, padx=20, pady=(4, 20), sticky="ew")
        self.btn_export_txt = ctk.CTkButton(
            self.export_frame,
            text="Guardar en TXT",
            command=self._export_txt,
            state="disabled",
        )
        self.btn_export_txt.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.btn_export_srt = ctk.CTkButton(
            self.export_frame,
            text="Guardar en SRT",
            command=self._export_srt,
            state="disabled",
        )
        self.btn_export_srt.pack(side="right", fill="x", expand=True, padx=(10, 0))

    def _on_mode_change(self, choice: str):
        """Cambia el motor de transcripción entre local y Groq."""
        if choice == "Online (Groq)":
            if not is_groq_available():
                messagebox.showwarning(
                    "Groq no disponible",
                    "Falta la API key de Groq o el paquete 'groq'.\n"
                    "Crea un archivo .env con GROQ_API_KEY=tu_key e instala "
                    "el paquete con: pip install groq\n"
                    "Se mantiene el modo Local.")
                self.mode_switch.set("Local (CPU)")
                return
            self.transcription_mode = "groq"
            logger.info("Motor seleccionado: Online (Groq).")
        else:
            self.transcription_mode = "local"
            logger.info("Motor seleccionado: Local (CPU).")

    # -----------------------------------------------------------------------
    # Drag & Drop
    # -----------------------------------------------------------------------
    def _register_drop_zone(self):
        """Registra el evento DND_FILES en la ventana y en todos los widgets visibles."""
        for widget in (self.drop_zone_outer, self.drop_zone_frame):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_file_dropped)

    def _parse_dnd_path(self, raw: str) -> str:
        """Limpia la cadena de ruta devuelta por tkinterdnd2."""
        raw = raw.strip()
        match = re.match(r"^\{(.+)\}$", raw)
        if match:
            return match.group(1).strip()
        if raw.startswith("{"):
            m = re.match(r"\{([^}]+)\}", raw)
            if m:
                return m.group(1).strip()
        parts = raw.split()
        return parts[0] if parts else raw

    def _on_file_dropped(self, event):
        if self.is_transcribing:
            return
        file_path = self._parse_dnd_path(event.data)
        _, ext = os.path.splitext(file_path)
        if ext.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                "Formato no soportado",
                f"La extensión '{ext}' no es compatible.\nFormatos válidos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
            return
        if not os.path.isfile(file_path):
            messagebox.showerror("Archivo no encontrado", f"No se pudo acceder al archivo:\n{file_path}")
            return
        self._load_file(file_path)

    # -----------------------------------------------------------------------
    # Selección de archivo (diálogo clásico)
    # -----------------------------------------------------------------------
    def _select_file(self):
        file_types = [
            ("Archivos multimedia", "*.mp4 *.mkv *.avi *.mov *.mp3 *.wav *.m4a"),
            ("Todos los archivos", "*.*"),
        ]
        file_path = filedialog.askopenfilename(title="Seleccionar archivo de audio o video", filetypes=file_types)
        if file_path:
            self._load_file(file_path)

    def _load_file(self, file_path: str):
        self.selected_file_path = file_path
        file_name = os.path.basename(file_path)
        self.lbl_file_name.configure(text=f"✅  {file_name}", text_color="#2ecc71")
        self.drop_main_label.configure(text="Archivo listo para transcribir", text_color="#2ecc71")
        self.drop_icon_label.configure(text="🎬")
        self.btn_transcribe.configure(state="normal")
        self.status_label.configure(text="Estado: Listo para transcribir")
        self.progress_bar.set(0.0)
        self._clear_textbox()
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Transcripción (worker en hilo secundario)
    # -----------------------------------------------------------------------
    def _start_transcription(self):
        if not self.selected_file_path:
            return
        self.btn_transcribe.configure(state="disabled")
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")
        self.drop_zone_frame.configure(fg_color="#111a2a")
        self.drop_main_label.configure(text="Transcripción en curso...", text_color="#6d8eb4")
        self.drop_icon_label.configure(text="⏳")
        self._clear_textbox()
        self.progress_bar.set(0.0)
        self.is_transcribing = True
        thread = threading.Thread(target=self._transcribe_worker, args=(self.selected_file_path,), daemon=True)
        thread.start()

    def _transcribe_worker(self, file_path: str):
        try:
            if self.transcription_mode == "groq":
                results = self._transcribe_with_groq(file_path)
            else:
                results = self._transcribe_with_local(file_path)
            # Ensure a final done message
            self.transcription_queue.put({"type": "done", "results": results, "level": "ok"})
        except Exception as exc:
            # Loguear con traceback completo para app.log (diagnostico) y enviar
            # el mensaje al usuario por la cola. Si logging falla, no rompe el hilo.
            try:
                logger.error("Error en worker de transcripción", exc_info=True)
            except Exception:
                pass
            self.transcription_queue.put({"type": "error", "message": str(exc), "level": "error"})

    def _transcribe_with_local(self, file_path: str):
        """Transcripción local con faster-whisper (CPU)."""
        if self.transcriber is None:
            self.transcription_queue.put({"type": "status", "text": "Cargando modelo Whisper 'base' en CPU...", "level": "processing"})
            self.transcriber = WhisperTranscriber(model_size="base", device="cpu", compute_type="int8")
        return self.transcriber.transcribe_file(file_path, queue=self.transcription_queue)

    def _transcribe_with_groq(self, file_path: str):
        """Transcripción online con Groq. Si falla (sin internet, key invalida,
        limite 429), hace fallback automatico al modo local avisando al usuario."""
        if self.groq_transcriber is None:
            self.groq_transcriber = GroqTranscriber()
        try:
            return self.groq_transcriber.transcribe_file(file_path, queue=self.transcription_queue)
        except Exception as exc:
            logger.warning(f"Groq falló ({exc}); fallback a modo local.")
            self.transcription_queue.put({
                "type": "status",
                "text": "Groq falló. Cambiando a modo local (CPU)...",
                "level": "processing",
            })
            # Fallback al transcriptor local.
            if self.transcriber is None:
                self.transcription_queue.put({"type": "status", "text": "Cargando modelo Whisper 'base' en CPU...", "level": "processing"})
                self.transcriber = WhisperTranscriber(model_size="base", device="cpu", compute_type="int8")
            return self.transcriber.transcribe_file(file_path, queue=self.transcription_queue)

    def poll_queue(self):
        try:
            while True:
                msg = self.transcription_queue.get_nowait()
                msg_type = msg.get("type")
                if msg_type == "status":
                    self.status_label.configure(text=f"Estado: {msg['text']}")
                    level = msg.get("level")
                    if level == "ok":
                        self.status_indicator.configure(fg_color="#28a745")
                    elif level == "processing":
                        self.status_indicator.configure(fg_color="#ffc107")
                    elif level == "error":
                        self.status_indicator.configure(fg_color="#dc3545")
                    else:
                        self.status_indicator.configure(fg_color="#6c757d")
                elif msg_type == "segment":
                    self.progress_bar.set(msg.get("progress", 0))
                    self.status_indicator.configure(fg_color="#ffc107")
                    self._append_to_textbox(msg.get("text", ""))
                elif msg_type == "done":
                    self.is_transcribing = False
                    self.transcription_results = msg.get("results", [])
                    self.progress_bar.set(1.0)
                    self.status_label.configure(text="Estado: Transcripción completada con éxito.")
                    self.status_indicator.configure(fg_color="#28a745")
                    # Restaurar drop zone visual
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff")
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    self.btn_export_txt.configure(state="normal")
                    self.btn_export_srt.configure(state="normal")
                    messagebox.showinfo("Éxito", "La transcripción se ha completado correctamente.")
                elif msg_type == "error":
                    self.is_transcribing = False
                    self.progress_bar.set(0.0)
                    self.status_label.configure(text="Estado: Error")
                    self.status_indicator.configure(fg_color="#dc3545")
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff")
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    messagebox.showerror("Error de Transcripción", f"Ocurrió un error:\n{msg.get('message', '')}")
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    # -----------------------------------------------------------------------
    # Helpers de textbox
    # -----------------------------------------------------------------------
    def _append_to_textbox(self, text: str):
        self.text_preview.configure(state="normal")
        self.text_preview.insert("end", text + "\n")
        self.text_preview.see("end")
        self.text_preview.configure(state="disabled")

    def _clear_textbox(self):
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Log capture utilities
    # -----------------------------------------------------------------------
    def _create_stream_proxy(self, original):
        """Proxy that writes to both original stream and internal buffer.
        Null-safe: bajo pythonw.exe los streams originales pueden ser None."""
        class StreamProxy:
            def write(self_inner, data):
                try:
                    self._log_buffer.write(data)
                except Exception:
                    pass
                if original is not None:
                    try:
                        original.write(data)
                    except Exception:
                        pass
            def flush(self_inner):
                if original is not None:
                    try:
                        original.flush()
                    except Exception:
                        pass
        return StreamProxy()

    def _show_log_window(self):
        """Muestra una ventana con los logs capturados y opción de copiar al portapapeles."""
        log_win = ctk.CTkToplevel(self)
        log_win.title("Log de Errores")
        log_win.geometry("600x400")
        txt = ctk.CTkTextbox(log_win, font=("Consolas", 10), state="normal")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self._log_buffer.getvalue())
        txt.configure(state="disabled")
        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(self._log_buffer.getvalue())
        copy_btn = ctk.CTkButton(log_win, text="Copiar al Portapapeles", command=copy_to_clipboard)
        copy_btn.pack(pady=(0, 10))
        log_win.grab_set()
        log_win.focus_set()

    # -----------------------------------------------------------------------
    # Exportación
    # -----------------------------------------------------------------------
    def _export_txt(self):
        """Diálogo para guardar la transcripción en formato TXT."""
        if not self.transcription_results:
            return
        file_path = filedialog.asksaveasfilename(
            title="Guardar como archivo de texto plano",
            defaultextension=".txt",
            filetypes=[("Archivos de texto", "*.txt")],
        )
        if file_path:
            try:
                export_to_txt(self.transcription_results, file_path)
                messagebox.showinfo("Guardado exitoso", f"Archivo guardado correctamente en:\n{file_path}")
            except Exception as exc:
                messagebox.showerror("Error al guardar", f"No se pudo guardar el archivo TXT:\n{exc}")

    def _export_srt(self):
        """Diálogo para guardar la transcripción en formato SRT."""
        if not self.transcription_results:
            return
        file_path = filedialog.asksaveasfilename(
            title="Guardar como archivo de subtítulos SRT",
            defaultextension=".srt",
            filetypes=[("Archivos SRT", "*.srt")],
        )
        if file_path:
            try:
                export_to_srt(self.transcription_results, file_path)
                messagebox.showinfo("Guardado exitoso", f"Subtítulos guardados correctamente en:\n{file_path}")
            except Exception as exc:
                messagebox.showerror("Error al guardar", f"No se pudo guardar el archivo SRT:\n{exc}")
