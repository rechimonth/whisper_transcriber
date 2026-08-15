import os
import re
import threading
import queue
import logging
import io
import sys
from tkinter import filedialog, messagebox
import customtkinter as ctk

# Attempt to import TkinterDnD2 for drag-and-drop support
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
    DND_FILES = None

from core.transcriber import WhisperTranscriber, export_to_txt, export_to_srt

logger = logging.getLogger(__name__)

# Supported file extensions (including .m4a)
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"}

# ---------------------------------------------------------------------------
# Dynamic base window class: uses TkinterDnD wrapper when available
# ---------------------------------------------------------------------------
if _DND_AVAILABLE:
    class _BaseWindow(TkinterDnD.DnDWrapper, ctk.CTk):
        """Combines Drag‑&‑Drop wrapper with CustomTkinter CTk."""
        def __init__(self, *args, **kwargs):
            ctk.CTk.__init__(self, *args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class _BaseWindow(ctk.CTk):
        """Fallback when TkinterDnD2 is not installed."""
        pass

# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------
class MainWindow(_BaseWindow):
    """Primary UI for the Whisper transcriber with observability features."""

    def __init__(self):
        super().__init__()
        # Window configuration
        self.title("Transcriptor de Audio y Video")
        self.geometry("680x720")
        self.resizable(False, False)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # Internal state
        self.selected_file_path = None
        self.transcription_results = None
        self.transcriber = None
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False

        # Log capture (stdout & stderr)
        self._log_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self._create_stream_proxy(self._original_stdout)
        sys.stderr = self._create_stream_proxy(self._original_stderr)

        # Build UI
        self._create_widgets()
        if _DND_AVAILABLE:
            self._register_drop_zone()
        else:
            logger.warning("tkinterdnd2 not installed – drag & drop disabled.")

        # Start polling the queue for progress updates
        self.poll_queue()

    # -------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------
    def _create_widgets(self):
        """Create and layout all widgets used by the application."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)  # make preview expandable

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")
        ctk.CTkLabel(header, text="Transcriptor Whisper",
                     font=ctk.CTkFont(family="Helvetica", size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header,
                     text="Formatos soportados: mp4, mkv, avi, mov, mp3, wav, m4a",
                     font=ctk.CTkFont(family="Helvetica", size=12),
                     text_color="#85929e").pack(anchor="w", pady=(2, 0))

        # Drop zone (outer & inner frames)
        self.drop_zone_outer = ctk.CTkFrame(self, fg_color="#1f3a5f", corner_radius=12)
        self.drop_zone_outer.grid(row=1, column=0, padx=20, pady=(0, 6), sticky="ew")
        self.drop_zone_frame = ctk.CTkFrame(self.drop_zone_outer, fg_color="#1a2744", corner_radius=10)
        self.drop_zone_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.drop_icon_label = ctk.CTkLabel(self.drop_zone_frame, text="📂", font=ctk.CTkFont(size=28))
        self.drop_icon_label.pack(pady=(14, 0))
        self.drop_main_label = ctk.CTkLabel(self.drop_zone_frame,
                                            text="Arrastra y suelta tu archivo aquí",
                                            font=ctk.CTkFont(family="Helvetica", size=14, weight="bold"),
                                            text_color="#d0e8ff")
        self.drop_main_label.pack()
        self.drop_sub_label = ctk.CTkLabel(self.drop_zone_frame,
                                            text="o haz clic para buscar",
                                            font=ctk.CTkFont(family="Helvetica", size=11),
                                            text_color="#6d8eb4",
                                            cursor="hand2")
        self.drop_sub_label.pack(pady=(2, 0))
        self.lbl_file_name = ctk.CTkLabel(self.drop_zone_frame,
                                         text="Ningún archivo seleccionado",
                                         font=ctk.CTkFont(size=11, slant="italic"),
                                         text_color="#85929e")
        self.lbl_file_name.pack(pady=(6, 14))
        # Click anywhere in the drop zone to open file dialog
        for w in (self.drop_zone_frame, self.drop_zone_outer, self.drop_icon_label,
                  self.drop_main_label, self.drop_sub_label, self.lbl_file_name):
            w.bind("<Button-1>", lambda e: self._select_file())

        # Control button
        control = ctk.CTkFrame(self, fg_color="transparent")
        control.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        self.btn_transcribe = ctk.CTkButton(control,
                                            text="Iniciar Transcripción",
                                            fg_color="#2ecc71",
                                            hover_color="#27ae60",
                                            text_color="white",
                                            font=ctk.CTkFont(weight="bold"),
                                            command=self._start_transcription,
                                            state="disabled")
        self.btn_transcribe.pack(fill="x", pady=(0, 5))

        # Progress & status row
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=3, column=0, padx=20, pady=4, sticky="ew")
        self.status_indicator = ctk.CTkLabel(prog, text="", width=20, height=20, fg_color="#6c757d")
        self.status_indicator.pack(side="left", padx=(0, 10))
        self.status_label = ctk.CTkLabel(prog,
                                         text="Estado: Esperando archivo...",
                                         font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left")
        self.log_button = ctk.CTkButton(prog,
                                         text="Ver Log de Errores",
                                         fg_color="#6c757d",
                                         hover_color="#5a6268",
                                         width=120,
                                         command=self._show_log_window)
        self.log_button.pack(side="right", padx=(10, 0))
        self.progress_bar = ctk.CTkProgressBar(prog)
        self.progress_bar.pack(fill="x", pady=(5, 5))
        self.progress_bar.set(0.0)

        # Preview area
        preview = ctk.CTkFrame(self)
        preview.grid(row=5, column=0, padx=20, pady=8, sticky="nsew")
        ctk.CTkLabel(preview, text="Vista Previa de Transcripción",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(5, 2))
        self.text_preview = ctk.CTkTextbox(preview, font=("Consolas", 12), state="disabled")
        self.text_preview.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Export buttons
        export = ctk.CTkFrame(self, fg_color="transparent")
        export.grid(row=6, column=0, padx=20, pady=(4, 20), sticky="ew")
        self.btn_export_txt = ctk.CTkButton(export, text="Guardar en TXT",
                                            command=self._export_txt, state="disabled")
        self.btn_export_txt.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.btn_export_srt = ctk.CTkButton(export, text="Guardar en SRT",
                                            command=self._export_srt, state="disabled")
        self.btn_export_srt.pack(side="right", fill="x", expand=True, padx=(10, 0))

    # -------------------------------------------------------------------
    # Drag & Drop handling
    # -------------------------------------------------------------------
    def _register_drop_zone(self):
        """Register DND_FILES events on the outer and inner frames."""
        for widget in (self.drop_zone_outer, self.drop_zone_frame):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_file_dropped)

    def _parse_dnd_path(self, raw: str) -> str:
        """Clean the raw path string returned by tkinterdnd2."""
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
            messagebox.showerror("Formato no soportado",
                                 f"Extensión '{ext}' no es válida.\nFormatos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
            return
        if not os.path.isfile(file_path):
            messagebox.showerror("Archivo no encontrado", f"No se pudo acceder a: {file_path}")
            return
        self._load_file(file_path)

    # -------------------------------------------------------------------
    # File selection utilities
    # -------------------------------------------------------------------
    def _select_file(self):
        file_types = [("Archivos multimedia", "*.mp4 *.mkv *.avi *.mov *.mp3 *.wav *.m4a"),
                      ("Todos los archivos", "*.*")]
        path = filedialog.askopenfilename(title="Seleccionar archivo", filetypes=file_types)
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self.selected_file_path = path
        name = os.path.basename(path)
        self.lbl_file_name.configure(text=f"✅ {name}", text_color="#2ecc71")
        self.drop_main_label.configure(text="Archivo listo para transcribir", text_color="#2ecc71")
        self.drop_icon_label.configure(text="🎬")
        self.btn_transcribe.configure(state="normal")
        self.status_label.configure(text="Estado: Listo para transcribir")
        self.progress_bar.set(0.0)
        self._clear_textbox()
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")

    # -------------------------------------------------------------------
    # Transcription workflow
    # -------------------------------------------------------------------
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
        thread = threading.Thread(target=self._transcribe_worker,
                                  args=(self.selected_file_path,), daemon=True)
        thread.start()

    def _transcribe_worker(self, file_path: str):
        try:
            if self.transcriber is None:
                self.transcription_queue.put({"type": "status",
                                            "text": "Cargando modelo Whisper 'base' en CPU...",
                                            "level": "processing"})
                self.transcriber = WhisperTranscriber(model_size="base", device="cpu", compute_type="int8")
            results = self.transcriber.transcribe_file(file_path, queue=self.transcription_queue)
            self.transcription_queue.put({"type": "done", "results": results, "level": "ok"})
        except Exception as exc:
            logger.error(f"Error en worker de transcripción: {exc}")
            self.transcription_queue.put({"type": "error", "message": str(exc), "level": "error"})

    def poll_queue(self):
        try:
            while True:
                msg = self.transcription_queue.get_nowait()
                t = msg.get("type")
                if t == "status":
                    self.status_label.configure(text=f"Estado: {msg['text']}")
                    lvl = msg.get("level")
                    color = {"ok": "#28a745", "processing": "#ffc107", "error": "#dc3545"}.get(lvl, "#6c757d")
                    self.status_indicator.configure(fg_color=color)
                elif t == "segment":
                    self.progress_bar.set(msg.get("progress", 0))
                    self.status_indicator.configure(fg_color="#ffc107")
                    self._append_to_textbox(msg.get("text", ""))
                elif t == "done":
                    self.is_transcribing = False
                    self.transcription_results = msg.get("results", [])
                    self.progress_bar.set(1.0)
                    self.status_label.configure(text="Estado: Transcripción completada con éxito.")
                    self.status_indicator.configure(fg_color="#28a745")
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff")
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    self.btn_export_txt.configure(state="normal")
                    self.btn_export_srt.configure(state="normal")
                    messagebox.showinfo("Éxito", "La transcripción se completó correctamente.")
                elif t == "error":
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

    # -------------------------------------------------------------------
    # Textbox helpers
    # -------------------------------------------------------------------
    def _append_to_textbox(self, text: str):
        self.text_preview.configure(state="normal")
        self.text_preview.insert("end", text + "\n")
        self.text_preview.see("end")
        self.text_preview.configure(state="disabled")

    def _clear_textbox(self):
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.configure(state="disabled")

    # -------------------------------------------------------------------
    # Log capture utilities
    # -------------------------------------------------------------------
    def _create_stream_proxy(self, original):
        """Proxy that forwards writes to the original stream and also stores them in the buffer."""
        class StreamProxy:
            def write(self_inner, data):
                self._log_buffer.write(data)
                original.write(data)
            def flush(self_inner):
                original.flush()
        return StreamProxy()

    def _show_log_window(self):
        """Display a modal window with the captured stdout/stderr logs."""
        win = ctk.CTkToplevel(self)
        win.title("Log de Errores")
        win.geometry("600x400")
        txt = ctk.CTkTextbox(win, font=("Consolas", 10), state="normal")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self._log_buffer.getvalue())
        txt.configure(state="disabled")
        def copy():
            self.clipboard_clear()
            self.clipboard_append(self._log_buffer.getvalue())
        ctk.CTkButton(win, text="Copiar al Portapapeles", command=copy).pack(pady=(0, 10))
        win.grab_set()
        win.focus_set()

    # -------------------------------------------------------------------
    # Export functions
    # -------------------------------------------------------------------
    def _export_txt(self):
        if not self.transcription_results:
            return
        path = filedialog.asksaveasfilename(title="Guardar como TXT",
                                         defaultextension=".txt",
                                         filetypes=[("Archivo de texto", "*.txt")])
        if path:
            try:
                export_to_txt(self.transcription_results, path)
                messagebox.showinfo("Guardado", f"Archivo guardado en {path}")
            except Exception as exc:
                messagebox.showerror("Error", f"No se pudo guardar: {exc}")

    def _export_srt(self):
        if not self.transcription_results:
            return
        path = filedialog.asksaveasfilename(title="Guardar como SRT",
                                         defaultextension=".srt",
                                         filetypes=[("Subtítulos SRT", "*.srt")])
        if path:
            try:
                export_to_srt(self.transcription_results, path)
                messagebox.showinfo("Guardado", f"Subtítulos guardados en {path}")
            except Exception as exc:
                messagebox.showerror("Error", f"No se pudo guardar: {exc}")

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
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False
        # Captura de logs
        self._log_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self._create_stream_proxy(self._original_stdout)
        sys.stderr = self._create_stream_proxy(self._original_stderr)
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
            if self.transcriber is None:
                self.transcription_queue.put({"type": "status", "text": "Cargando modelo Whisper 'base' en CPU...", "level": "processing"})
                self.transcriber = WhisperTranscriber(model_size="base", device="cpu", compute_type="int8")
            results = self.transcriber.transcribe_file(file_path, queue=self.transcription_queue)
            # Ensure a final done message
            self.transcription_queue.put({"type": "done", "results": results, "level": "ok"})
        except Exception as exc:
            logger.error(f"Error en worker de transcripción: {exc}")
            self.transcription_queue.put({"type": "error", "message": str(exc), "level": "error"})

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
        """Proxy that writes to both original stream and internal buffer."""
        class StreamProxy:
            def write(self_inner, data):
                self._log_buffer.write(data)
                original.write(data)
            def flush(self_inner):
                original.flush()
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

import re
import threading
import queue
import logging
import io
import sys
from tkinter import filedialog, messagebox
import customtkinter as ctk
import re
import threading
import queue
import logging
from tkinter import filedialog, messagebox
import customtkinter as ctk

# ---------------------------------------------------------------------------
# Integración con TkinterDnD2 (drag & drop)
# Se intenta importar tkinterdnd2. Si no está instalado, la aplicación sigue
# funcionando sin la funcionalidad de arrastre pero sin lanzar un error fatal.
# ---------------------------------------------------------------------------
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
    DND_FILES = None

from core.transcriber import WhisperTranscriber, export_to_txt, export_to_srt

logger = logging.getLogger(__name__)

# Extensiones válidas (incluye m4a según el nuevo requerimiento)
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"}

# ---------------------------------------------------------------------------
# Clase base dinámica: si tkinterdnd2 está disponible, heredamos de su wrapper
# compatible con CustomTkinter. De lo contrario, usamos ctk.CTk directamente.
# ---------------------------------------------------------------------------
if _DND_AVAILABLE:
    class _BaseWindow(TkinterDnD.DnDWrapper, ctk.CTk):
        """
        Combinación de TkinterDnD.DnDWrapper con ctk.CTk.

        El orden del MRO es crítico: DnDWrapper debe ir primero para que su
        __init__ pueda inyectar las capacidades Drag & Drop en el widget Tk
        raíz ANTES de que CustomTkinter configure sus propios estilos.
        """
        def __init__(self, *args, **kwargs):
            # TkinterDnD.DnDWrapper necesita que TkinterDnD.Tk sea la clase raíz;
            # lo logramos inicializando ctk.CTk normalmente y luego registrando
            # las capacidades DnD sobre el mismo widget Tk subyacente.
            ctk.CTk.__init__(self, *args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class _BaseWindow(ctk.CTk):
        """Fallback cuando tkinterdnd2 no está instalado."""
        pass


class MainWindow(_BaseWindow):
    """Ventana principal de la aplicación de transcripción con soporte Drag & Drop."""

        def __init__(self):
        super().__init__()

        # --- Configuración de la ventana ---
        self.title("Transcriptor de Audio y Video")
        self.geometry("680x720")  # increased height for status indicator
        self.resizable(False, False)

        # --- Tema visual ---
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # --- Estado interno ---
        self.selected_file_path = None
        self.transcription_results = None
        self.transcriber = None
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False
+
        # --- Log capture ---
        self._log_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self._create_stream_proxy(self._original_stdout)
        sys.stderr = self._create_stream_proxy(self._original_stderr)

        # --- Crear widgets ---
        self._create_widgets()

        # --- Registrar Drop Zone si DnD está disponible ---
        if _DND_AVAILABLE:
            self._register_drop_zone()
        else:
            logger.warning(
                "tkinterdnd2 no está instalado. Funcionalidad de Drag & Drop deshabilitada."
            )

        # --- Verificación periódica de la cola de transcripción ---
        self.poll_queue()

        super().__init__()

        # --- Configuración de la ventana ---
        self.title("Transcriptor de Audio y Video")
        self.geometry("680x640")       # 40 px extra para acomodar la drop zone
        self.resizable(False, False)

        # --- Tema visual ---
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # --- Estado interno ---
        self.selected_file_path = None
        self.transcription_results = None
        self.transcriber = None
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False

        # --- Crear widgets ---
        self._create_widgets()

        # --- Registrar Drop Zone si DnD está disponible ---
        if _DND_AVAILABLE:
            self._register_drop_zone()
        else:
            logger.warning(
                "tkinterdnd2 no está instalado. "
                "Funcionalidad de Drag & Drop deshabilitada."
            )

        # --- Verificación periódica de la cola de transcripción ---
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

        # 2. DROP ZONE  ──────────────────────────────────────────────────────
        # Marco exterior con borde simulado mediante color de fondo diferente
        self.drop_zone_outer = ctk.CTkFrame(
            self,
            fg_color="#1f3a5f",          # Azul oscuro → apariencia de borde
            corner_radius=12,
        )
        self.drop_zone_outer.grid(row=1, column=0, padx=20, pady=(0, 6), sticky="ew")

        # Marco interior que actúa como la superficie real de la zona
        self.drop_zone_frame = ctk.CTkFrame(
            self.drop_zone_outer,
            fg_color="#1a2744",          # Fondo ligeramente más oscuro
            corner_radius=10,
        )
        self.drop_zone_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # Icono visual (emoji como sustituto sin recursos externos)
        self.drop_icon_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="📂",
            font=ctk.CTkFont(size=28),
        )
        self.drop_icon_label.pack(pady=(14, 0))

        # Texto principal de la zona
        self.drop_main_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="Arrastra y suelta tu archivo aquí",
            font=ctk.CTkFont(family="Helvetica", size=14, weight="bold"),
            text_color="#d0e8ff",
        )
        self.drop_main_label.pack()

        # Texto secundario / instrucción alternativa
        self.drop_sub_label = ctk.CTkLabel(
            self.drop_zone_frame,
            text="o haz clic para buscar",
            font=ctk.CTkFont(family="Helvetica", size=11),
            text_color="#6d8eb4",
            cursor="hand2",
        )
        self.drop_sub_label.pack(pady=(2, 0))

        # Etiqueta con el nombre del archivo seleccionado
        self.lbl_file_name = ctk.CTkLabel(
            self.drop_zone_frame,
            text="Ningún archivo seleccionado",
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="#85929e",
        )
        self.lbl_file_name.pack(pady=(6, 14))

        # Hacer clic en la zona → abrir diálogo
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

        # 4. BARRA DE PROGRESO Y ESTADO
        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=3, column=0, padx=20, pady=4, sticky="ew")

                # Status indicator: a small colored label acting as a LED
        self.status_indicator = ctk.CTkLabel(self.progress_frame, text="", width=20, height=20, fg_color="#6c757d")
        self.status_indicator.pack(side="left", padx=(0, 10))
        # Existing status text label
        self.status_label = ctk.CTkLabel(
            self.progress_frame,
            text="Estado: Esperando archivo...",
            font=ctk.CTkFont(size=12),
        )
        self.status_label.pack(anchor="w")
+
        # Button to view log window (non-blocking)
        self.log_button = ctk.CTkButton(
            self.progress_frame,
            text="Ver Log de Errores",
            fg_color="#6c757d",
            hover_color="#5a6268",
            width=120,
            command=self._show_log_window,
        )
        self.log_button.pack(side="right", padx=(10, 0))
        self.progress_bar.pack(fill="x", pady=(5, 5))
        self.progress_bar.set(0.0)

        self.status_label = ctk.CTkLabel(
            self.progress_frame,
            text="Estado: Esperando archivo...",
            font=ctk.CTkFont(size=12),
        )
        self.status_label.pack(anchor="w")

        # 5. ÁREA DE TEXTO DE VISTA PREVIA
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

        # 6. BOTONES INFERIORES DE EXPORTACIÓN
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

    # -----------------------------------------------------------------------
    # Drag & Drop
    # -----------------------------------------------------------------------
    def _register_drop_zone(self):
        """Registra el evento DND_FILES en la ventana y en todos los widgets visibles."""
        # Registrar en el frame exterior de la zona para máxima área de drop
        for widget in (self.drop_zone_outer, self.drop_zone_frame):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_file_dropped)

    def _parse_dnd_path(self, raw: str) -> str:
        """
        Limpia la cadena de ruta devuelta por tkinterdnd2.

        tkinterdnd2 puede envolver las rutas con llaves cuando contienen
        espacios u otros caracteres especiales, por ejemplo:
            '{C:/Users/usuario/Mi Video.mp4}'
        Este método elimina esas llaves y espacios sobrantes para obtener
        una ruta utilizable directamente por Python.

        Si se arrastran múltiples archivos, solo se procesa el primero.
        """
        raw = raw.strip()
        # Caso 1: ruta envuelta en llaves -> {/ruta/al/archivo.mp4}
        match = re.match(r"^\{(.+)\}$", raw)
        if match:
            return match.group(1).strip()
        # Caso 2: múltiples rutas separadas por espacios (tkinterdnd2 las separa así)
        # Tomamos solo la primera
        if raw.startswith("{"):
            # Extrae el primer elemento entre llaves
            m = re.match(r"\{([^}]+)\}", raw)
            if m:
                return m.group(1).strip()
        # Caso 3: ruta limpia o separada por espacios sin llaves → primer token
        parts = raw.split()
        return parts[0] if parts else raw

    def _on_file_dropped(self, event):
        """
        Callback invocado cuando el usuario suelta un archivo sobre la Drop Zone.

        Pasos:
        1. Parsear y limpiar la ruta recibida de tkinterdnd2.
        2. Validar que la extensión esté soportada.
        3. Actualizar el estado interno y la UI.
        """
        if self.is_transcribing:
            return  # Ignorar drops mientras hay una transcripción en curso

        raw_path = event.data
        file_path = self._parse_dnd_path(raw_path)

        _, ext = os.path.splitext(file_path)
        if ext.lower() not in SUPPORTED_EXTENSIONS:
            messagebox.showerror(
                "Formato no soportado",
                f"La extensión '{ext}' no es compatible.\n"
                f"Formatos válidos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
            return

        if not os.path.isfile(file_path):
            messagebox.showerror(
                "Archivo no encontrado",
                f"No se pudo acceder al archivo:\n{file_path}",
            )
            return

        self._load_file(file_path)

    # -----------------------------------------------------------------------
    # Selección de archivo (diálogo clásico)
    # -----------------------------------------------------------------------
    def _select_file(self):
        """Abre el diálogo nativo de selección de archivos y carga el resultado."""
        file_types = [
            ("Archivos multimedia", "*.mp4 *.mkv *.avi *.mov *.mp3 *.wav *.m4a"),
            ("Todos los archivos", "*.*"),
        ]
        file_path = filedialog.askopenfilename(
            title="Seleccionar archivo de audio o video",
            filetypes=file_types,
        )
        if file_path:
            self._load_file(file_path)

    def _load_file(self, file_path: str):
        """
        Centraliza la carga de un archivo ya validado (ya sea por diálogo o
        por drag & drop) y actualiza todos los elementos de la UI.
        """
        self.selected_file_path = file_path
        file_name = os.path.basename(file_path)

        self.lbl_file_name.configure(text=f"✅  {file_name}", text_color="#2ecc71")
        self.drop_main_label.configure(
            text="Archivo listo para transcribir",
            text_color="#2ecc71",
        )
        self.drop_icon_label.configure(text="🎬")
        self.btn_transcribe.configure(state="normal")
        self.status_label.configure(text="Estado: Listo para transcribir")
        self.progress_bar.set(0.0)

        # Limpiar vista previa y deshabilitar exportaciones anteriores
        self._clear_textbox()
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Transcripción (worker en hilo secundario)
    # -----------------------------------------------------------------------
    def _start_transcription(self):
        """Inicia la transcripción en un hilo de fondo para no bloquear la GUI."""
        if not self.selected_file_path:
            return

        # Deshabilitar interacción durante el proceso
        self.btn_transcribe.configure(state="disabled")
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")
        # La drop zone también se deshabilita durante la transcripción
        self.drop_zone_frame.configure(fg_color="#111a2a")
        self.drop_main_label.configure(text="Transcripción en curso...", text_color="#6d8eb4")
        self.drop_icon_label.configure(text="⏳")

        self._clear_textbox()
        self.progress_bar.set(0.0)
        self.is_transcribing = True

        thread = threading.Thread(
            target=self._transcribe_worker,
            args=(self.selected_file_path,),
            daemon=True,
        )
        thread.start()

    def _transcribe_worker(self, file_path: str):
        """Ejecuta la transcripción fuera del hilo principal."""
        try:
            if self.transcriber is None:
                self.transcription_queue.put(
                    {"type": "status", "text": "Cargando modelo Whisper 'base' en CPU..."}
                )
                self.transcriber = WhisperTranscriber(
                    model_size="base", device="cpu", compute_type="int8"
                )

            # Use the transcriber's method which will emit status and segment updates via the queue
            results = self.transcriber.transcribe_file(file_path, queue=self.transcription_queue)
            # The transcriber will have already put a 'done' message; we can optionally ensure completion state
            self.transcription_queue.put({"type": "done", "results": results, "level": "ok"})

        except Exception as exc:
            logger.error(f"Error en worker de transcripción: {exc}")
            # Propagate error via queue; transcriber may have already sent one
            self.transcription_queue.put({"type": "error", "message": str(exc), "level": "error"})

    def poll_queue(self):
        """Revisa periódicamente la cola de mensajes del worker y actualiza la UI."""
        try:
            while True:
                msg = self.transcription_queue.get_nowait()
                msg_type = msg.get("type")

                if msg_type == "status":
                                    self.status_label.configure(text=f"Estado: {msg['text']}")
+                # Update status indicator colour based on level if provided
+                level = msg.get("level")
+                if level == "ok":
+                    self.status_indicator.configure(fg_color="#28a745")  # green
+                elif level == "processing":
+                    self.status_indicator.configure(fg_color="#ffc107")  # yellow
+                elif level == "error":
+                    self.status_indicator.configure(fg_color="#dc3545")  # red
+                else:
+                    self.status_indicator.configure(fg_color="#6c757d")  # default grey


                elif msg_type == "segment":
                                    self.progress_bar.set(msg["progress"])
+                # Update indicator to processing colour
+                self.status_indicator.configure(fg_color="#ffc107")
                 self._append_to_textbox(msg["text"])

                    self._append_to_textbox(msg["text"])

                elif msg_type == "done":
                    self.is_transcribing = False
                    self.transcription_results = msg["results"]
                    self.progress_bar.set(1.0)
                    self.status_label.configure(
                        text="Estado: Transcripción completada con éxito."
                    )
                    # Restaurar drop zone
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(
                        text="Arrastra y suelta tu archivo aquí",
                        text_color="#d0e8ff",
                    )
                    self.drop_icon_label.configure(text="📂")
                    # Reactivar botones
                    self.btn_transcribe.configure(state="normal")
                    self.btn_export_txt.configure(state="normal")
                    self.btn_export_srt.configure(state="normal")
                    messagebox.showinfo(
                        "Éxito", "La transcripción se ha completado correctamente."
                    )

                elif msg_type == "error":
                    self.is_transcribing = False
                    self.progress_bar.set(0.0)
                    self.status_label.configure(text="Estado: Error")
                    # Restaurar drop zone
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(
                        text="Arrastra y suelta tu archivo aquí",
                        text_color="#d0e8ff",
                    )
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    messagebox.showerror(
                        "Error de Transcripción",
                        f"Ocurrió un error:\n{msg['message']}",
                    )

        except queue.Empty:
            pass

        self.after(100, self.poll_queue)

    # -----------------------------------------------------------------------
    # Helpers de textbox
    # -----------------------------------------------------------------------
        def _create_stream_proxy(self, original):
        """Return an object that writes to both original stream and internal buffer."""
        class StreamProxy:
            def write(self_inner, data):
                self._log_buffer.write(data)
                original.write(data)
            def flush(self_inner):
                original.flush()
        return StreamProxy()
        """Agrega una línea de texto a la caja de vista previa."""
        self.text_preview.configure(state="normal")
        self.text_preview.insert("end", text + "\n")
        self.text_preview.see("end")
        self.text_preview.configure(state="disabled")

        def _show_log_window(self):
        """Open a top‑level window displaying captured stdout/stderr with copy functionality."""
        log_win = ctk.CTkToplevel(self)
        log_win.title("Log de Errores")
        log_win.geometry("600x400")
        # Textbox (read‑only)
        txt = ctk.CTkTextbox(log_win, font=("Consolas", 10), state="normal")
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self._log_buffer.getvalue())
        txt.configure(state="disabled")
        # Copy button
        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(self._log_buffer.getvalue())
        copy_btn = ctk.CTkButton(log_win, text="Copiar al Portapapeles", command=copy_to_clipboard)
        copy_btn.pack(pady=(0, 10))
        # Ensure window stays on top
        log_win.grab_set()
        log_win.focus_set()
        """Limpia el contenido de la caja de vista previa."""
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.configure(state="disabled")

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
                messagebox.showinfo(
                    "Guardado exitoso",
                    f"Archivo guardado correctamente en:\n{file_path}",
                )
            except Exception as exc:
                messagebox.showerror(
                    "Error al guardar",
                    f"No se pudo guardar el archivo TXT:\n{exc}",
                )

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
                messagebox.showinfo(
                    "Guardado exitoso",
                    f"Subtítulos guardados correctamente en:\n{file_path}",
                )
            except Exception as exc:
                messagebox.showerror(
                    "Error al guardar",
                    f"No se pudo guardar el archivo SRT:\n{exc}",
                )
