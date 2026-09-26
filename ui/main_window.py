import os
import re
import threading
import queue
import logging
import io
import sys
import webbrowser
from tkinter import filedialog, messagebox
import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False
    DND_FILES = None

from core.transcriber import (
    WhisperTranscriber,
    export_to_txt,
    export_to_srt,
    ModelLoadError,
    TranscriptionProcessError,
)
from core.backend_client import (
    AuthenticationError,
    BackendClient,
    BackendClientError,
    BackendUnavailableError,
    InsufficientCreditsError,
)

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a"
}

if _DND_AVAILABLE:
    class _BaseWindow(TkinterDnD.DnDWrapper, ctk.CTk):
        def __init__(self, *args, **kwargs):
            ctk.CTk.__init__(self, *args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class _BaseWindow(ctk.CTk):
        pass


class _BufferLogHandler(logging.Handler):
    def __init__(self, buffer):
        super().__init__()
        self._buffer = buffer

    def emit(self, record):
        try:
            self._buffer.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


class MainWindow(_BaseWindow):
    def __init__(self):
        super().__init__()
        self.title("Transcriptor de Audio y Video")
        self.geometry("720x780")
        self.resizable(False, False)
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.selected_file_path = None
        self.transcription_results = None
        self.transcriber = None
        self.transcription_queue = queue.Queue()
        self.is_transcribing = False
        self.transcription_mode = "local"
        self.backend_client = BackendClient()
        self.credits = None
        self.auth_token = os.getenv("TRANSCRIBER_AUTH_TOKEN", "").strip()

        self._log_buffer = io.StringIO()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = self._create_stream_proxy(self._original_stdout)
        sys.stderr = self._create_stream_proxy(self._original_stderr)

        self._log_handler = _BufferLogHandler(self._log_buffer)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        )
        logging.getLogger().addHandler(self._log_handler)

        self._create_widgets()
        if _DND_AVAILABLE:
            self._register_drop_zone()
        else:
            logger.warning("tkinterdnd2 no está instalado. Drag & Drop deshabilitado.")

        self.poll_queue()

        if self.auth_token:
            self.after(250, self._login_with_token)

    def _create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)

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
            text="Local 100% privado · Online mediante servidor seguro",
            font=ctk.CTkFont(family="Helvetica", size=12),
            text_color="#85929e",
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        self.auth_frame = ctk.CTkFrame(self)
        self.auth_frame.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")
        self.auth_frame.grid_columnconfigure(1, weight=1)
        self.auth_frame.grid_columnconfigure(2, weight=1)

        self.auth_title = ctk.CTkLabel(
            self.auth_frame,
            text="Cuenta",
            font=ctk.CTkFont(weight="bold"),
        )
        self.auth_title.grid(row=0, column=0, padx=(12, 8), pady=10)

        self.email_entry = ctk.CTkEntry(
            self.auth_frame,
            placeholder_text="Email",
        )
        self.email_entry.grid(row=0, column=1, padx=6, pady=10, sticky="ew")

        self.password_entry = ctk.CTkEntry(
            self.auth_frame,
            placeholder_text="Contraseña",
            show="•",
        )
        self.password_entry.grid(row=0, column=2, padx=6, pady=10, sticky="ew")

        self.login_button = ctk.CTkButton(
            self.auth_frame,
            text="Iniciar Sesión",
            width=120,
            command=self._login,
        )
        self.login_button.grid(row=0, column=3, padx=6, pady=10)

        self.register_button = ctk.CTkButton(
            self.auth_frame,
            text="Crear Cuenta",
            width=120,
            fg_color="#6c757d",
            hover_color="#5a6268",
            command=self._register,
        )
        self.register_button.grid(row=0, column=4, padx=6, pady=10)

        self.credits_label = ctk.CTkLabel(
            self.auth_frame,
            text="Créditos: —",
            width=110,
        )
        self.credits_label.grid(row=0, column=5, padx=6, pady=10)

        self.buy_button = ctk.CTkButton(
            self.auth_frame,
            text="Comprar Créditos",
            width=140,
            command=self._buy_credits,
        )
        self.buy_button.grid(row=1, column=4, columnspan=2, padx=(6, 12), pady=(0, 10), sticky="ew")

        self.account_status = ctk.CTkLabel(
            self.auth_frame,
            text="No autenticado",
            text_color="#85929e",
        )
        self.account_status.grid(row=1, column=0, columnspan=4, padx=12, pady=(0, 8), sticky="w")

        self.drop_zone_outer = ctk.CTkFrame(
            self, fg_color="#1f3a5f", corner_radius=12
        )
        self.drop_zone_outer.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="ew")
        self.drop_zone_frame = ctk.CTkFrame(
            self.drop_zone_outer, fg_color="#1a2744", corner_radius=10
        )
        self.drop_zone_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.drop_icon_label = ctk.CTkLabel(
            self.drop_zone_frame, text="📂", font=ctk.CTkFont(size=28)
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

        for widget in (
            self.drop_zone_frame, self.drop_zone_outer, self.drop_icon_label,
            self.drop_main_label, self.drop_sub_label, self.lbl_file_name,
        ):
            widget.bind("<Button-1>", lambda e: self._select_file())

        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=3, column=0, padx=20, pady=6, sticky="ew")

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

        self.mode_frame = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        self.mode_frame.pack(fill="x", pady=(0, 5))
        self.mode_label = ctk.CTkLabel(
            self.mode_frame, text="Motor:", font=ctk.CTkFont(size=12)
        )
        self.mode_label.pack(side="left", padx=(0, 8))
        self.mode_switch = ctk.CTkSegmentedButton(
            self.mode_frame,
            values=["Local (CPU)", "Online (Servidor)"],
            command=self._on_mode_change,
        )
        self.mode_switch.set("Local (CPU)")
        self.mode_switch.pack(side="left", fill="x", expand=True)

        self.progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.progress_frame.grid(row=4, column=0, padx=20, pady=4, sticky="ew")
        self.status_indicator = ctk.CTkLabel(
            self.progress_frame, text="", width=20, height=20, fg_color="#6c757d"
        )
        self.status_indicator.pack(side="left", padx=(0, 10))
        self.status_label = ctk.CTkLabel(
            self.progress_frame, text="Estado: Esperando archivo...",
            font=ctk.CTkFont(size=12)
        )
        self.status_label.pack(anchor="w")
        self.log_button = ctk.CTkButton(
            self.progress_frame,
            text="Ver Log de Errores",
            fg_color="#6c757d",
            hover_color="#5a6268",
            width=120,
            command=self._show_log_window,
        )
        self.log_button.pack(side="right", padx=(10, 0))
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.pack(fill="x", pady=(5, 5))
        self.progress_bar.set(0.0)

        self.preview_frame = ctk.CTkFrame(self)
        self.preview_frame.grid(row=6, column=0, padx=20, pady=8, sticky="nsew")
        self.preview_title = ctk.CTkLabel(
            self.preview_frame,
            text="Vista Previa de Transcripción",
            font=ctk.CTkFont(weight="bold"),
        )
        self.preview_title.pack(anchor="w", padx=10, pady=(5, 2))
        self.text_preview = ctk.CTkTextbox(
            self.preview_frame, font=("Consolas", 12), state="disabled"
        )
        self.text_preview.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.export_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.export_frame.grid(row=7, column=0, padx=20, pady=(4, 20), sticky="ew")
        self.btn_export_txt = ctk.CTkButton(
            self.export_frame, text="Guardar en TXT",
            command=self._export_txt, state="disabled"
        )
        self.btn_export_txt.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.btn_export_srt = ctk.CTkButton(
            self.export_frame, text="Guardar en SRT",
            command=self._export_srt, state="disabled"
        )
        self.btn_export_srt.pack(side="right", fill="x", expand=True, padx=(10, 0))

    def _login(self):
        email = self.email_entry.get().strip()
        password = self.password_entry.get()
        if not email or not password:
            messagebox.showwarning("Iniciar Sesión", "Introduce tu email y contraseña.")
            return

        self.login_button.configure(state="disabled")
        self.register_button.configure(state="disabled")
        self.account_status.configure(text="Autenticando...", text_color="#ffc107")

        def worker():
            try:
                user = self.backend_client.login(email, password)
                credits = self.backend_client.get_credits()
                self.transcription_queue.put({
                    "type": "auth_ok",
                    "user": user,
                    "credits": credits,
                })
            except Exception as exc:
                self.transcription_queue.put({
                    "type": "auth_error",
                    "message": str(exc),
                })

        threading.Thread(target=worker, daemon=True).start()

    def _register(self):
        email = self.email_entry.get().strip()
        password = self.password_entry.get()
        if not email or not password:
            messagebox.showwarning("Crear Cuenta", "Introduce tu email y contraseña.")
            return
        if len(password) < 8:
            messagebox.showwarning(
                "Crear Cuenta", "La contraseña debe tener al menos 8 caracteres."
            )
            return

        self.login_button.configure(state="disabled")
        self.register_button.configure(state="disabled")
        self.account_status.configure(text="Creando cuenta...", text_color="#ffc107")

        def worker():
            try:
                user = self.backend_client.register(email, password)
                credits = self.backend_client.get_credits()
                self.transcription_queue.put({
                    "type": "auth_ok",
                    "user": user,
                    "credits": credits,
                })
            except Exception as exc:
                self.transcription_queue.put({
                    "type": "auth_error",
                    "message": str(exc),
                })

        threading.Thread(target=worker, daemon=True).start()

    def _login_with_token(self):
        """Auto-login con un JWT preexistente (TRANSCRIBER_AUTH_TOKEN)."""
        self.account_status.configure(text="Autenticando...", text_color="#ffc107")

        def worker():
            try:
                user = self.backend_client.login_with_token(self.auth_token)
                credits = self.backend_client.get_credits()
                self.transcription_queue.put({
                    "type": "auth_ok",
                    "user": user,
                    "credits": credits,
                })
            except Exception as exc:
                self.transcription_queue.put({
                    "type": "auth_error",
                    "message": str(exc),
                })

        threading.Thread(target=worker, daemon=True).start()

    def _buy_credits(self):
        if not self.backend_client.is_authenticated:
            messagebox.showwarning(
                "Iniciar sesión",
                "Inicia sesión antes de comprar créditos.",
            )
            return

        self.buy_button.configure(state="disabled")
        self.account_status.configure(text="Creando checkout...", text_color="#ffc107")

        def worker():
            try:
                url = self.backend_client.create_checkout("starter")
                self.transcription_queue.put({
                    "type": "checkout_ok",
                    "url": url,
                })
            except Exception as exc:
                self.transcription_queue.put({
                    "type": "checkout_error",
                    "message": str(exc),
                })

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_credits_async(self):
        if not self.backend_client.is_authenticated:
            return

        def worker():
            try:
                credits = self.backend_client.get_credits()
                self.transcription_queue.put({
                    "type": "credits_ok",
                    "credits": credits,
                })
            except Exception as exc:
                logger.warning("No se pudo actualizar el saldo: %s", exc)

        threading.Thread(target=worker, daemon=True).start()

    def _on_mode_change(self, choice: str):
        self.transcription_mode = (
            "online" if choice == "Online (Servidor)" else "local"
        )
        if self.transcription_mode == "online":
            if not self.backend_client.is_authenticated:
                self.status_label.configure(
                    text="Estado: Inicia sesión para usar el modo Online."
                )
            elif self.credits == 0:
                self.status_label.configure(
                    text="Estado: No tienes créditos disponibles."
                )
            else:
                self.status_label.configure(
                    text=f"Estado: Online listo · Créditos: {self.credits}"
                )
        else:
            self.status_label.configure(text="Estado: Modo Local (CPU).")
        logger.info("Motor seleccionado: %s.", self.transcription_mode)

    def _register_drop_zone(self):
        for widget in (self.drop_zone_outer, self.drop_zone_frame):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_file_dropped)

    def _parse_dnd_path(self, raw: str) -> str:
        raw = raw.strip()
        match = re.match(r"^{(.+)}$", raw)
        if match:
            return match.group(1).strip()
        if raw.startswith("{"):
            match = re.match(r"^{([^}]+)}", raw)
            if match:
                return match.group(1).strip()
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
                f"La extensión '{ext}' no es compatible.\n"
                f"Formatos válidos: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
            return
        if not os.path.isfile(file_path):
            messagebox.showerror(
                "Archivo no encontrado", f"No se pudo acceder al archivo:\n{file_path}"
            )
            return
        self._load_file(file_path)

    def _select_file(self):
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
        self.selected_file_path = file_path
        self.lbl_file_name.configure(
            text=f"✅  {os.path.basename(file_path)}",
            text_color="#2ecc71",
        )
        self.drop_main_label.configure(
            text="Archivo listo para transcribir", text_color="#2ecc71"
        )
        self.drop_icon_label.configure(text="🎬")
        self.btn_transcribe.configure(state="normal")
        self.status_label.configure(text="Estado: Listo para transcribir")
        self.progress_bar.set(0.0)
        self._clear_textbox()
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")

    def _start_transcription(self):
        if not self.selected_file_path or self.is_transcribing:
            return

        if self.transcription_mode == "online":
            if not self.backend_client.is_authenticated:
                messagebox.showwarning(
                    "Iniciar sesión",
                    "El modo Online requiere una sesión activa.",
                )
                return
            if self.credits is None:
                self._refresh_credits_async()
                messagebox.showwarning(
                    "Saldo no disponible",
                    "No se pudo confirmar tu saldo. Inténtalo nuevamente.",
                )
                return
            if self.credits <= 0:
                self.status_label.configure(
                    text="Estado: Sin créditos. La petición Online fue bloqueada."
                )
                messagebox.showwarning(
                    "Sin créditos",
                    "No tienes créditos disponibles. Puedes comprar créditos o usar el modo Local.",
                )
                return

        self.btn_transcribe.configure(state="disabled")
        self.btn_export_txt.configure(state="disabled")
        self.btn_export_srt.configure(state="disabled")
        self.drop_zone_frame.configure(fg_color="#111a2a")
        self.drop_main_label.configure(
            text="Transcripción en curso...", text_color="#6d8eb4"
        )
        self.drop_icon_label.configure(text="⏳")
        self._clear_textbox()
        self.progress_bar.set(0.0)
        self.is_transcribing = True
        threading.Thread(
            target=self._transcribe_worker,
            args=(self.selected_file_path, self.transcription_mode),
            daemon=True,
        ).start()

    def _transcribe_worker(self, file_path: str, mode: str):
        if mode == "local":
            try:
                self._transcribe_with_local(file_path)
            except TranscriptionProcessError:
                pass
            except Exception as exc:
                logger.error("Error en transcripcion local", exc_info=True)
                self.transcription_queue.put({
                    "type": "error",
                    "message": str(exc),
                })
            return

        try:
            result = self.backend_client.transcribe(file_path)
            segments = result.get("segments", [])
            self.transcription_queue.put({
                "type": "segment",
                "text": result.get("text", ""),
                "progress": 0.95,
            })
            self.transcription_queue.put({
                "type": "done",
                "results": segments,
                "level": "ok",
                "credits_remaining": result.get("credits_remaining"),
            })
        except InsufficientCreditsError as exc:
            self.transcription_queue.put({
                "type": "credits_error",
                "message": str(exc),
            })
        except (AuthenticationError, BackendUnavailableError, BackendClientError) as exc:
            logger.warning(
                "Modo Online fallo (%s); iniciando fallback Local.",
                exc,
            )
            self.transcription_queue.put({
                "type": "status",
                "text": "Online fallo. Cambiando automaticamente a modo Local (CPU)...",
                "level": "processing",
            })
            try:
                self._transcribe_with_local(file_path)
            except TranscriptionProcessError:
                pass
            except Exception as local_exc:
                logger.error("Tambien fallo el fallback local", exc_info=True)
                self.transcription_queue.put({
                    "type": "error",
                    "message": str(local_exc),
                })

    def _transcribe_with_local(self, file_path: str):
        if self.transcriber is None:
            self.transcription_queue.put({
                "type": "status",
                "text": "Cargando modelo Whisper 'base' en CPU...",
                "level": "processing",
            })
            self.transcriber = WhisperTranscriber(
                model_size="base",
                device="cpu",
                compute_type="int8",
            )
        return self.transcriber.transcribe_file(
            file_path, queue=self.transcription_queue
        )

    def poll_queue(self):
        try:
            while True:
                msg = self.transcription_queue.get_nowait()
                msg_type = msg.get("type")

                if msg_type == "auth_ok":
                    self.credits = int(msg["credits"])
                    self.credits_label.configure(text=f"Créditos: {self.credits}")
                    self.account_status.configure(
                        text=f"Sesión activa: {msg['user'].get('email', msg['user'].get('user_id', 'usuario'))}",
                        text_color="#2ecc71",
                    )
                    self.login_button.configure(state="normal")
                    self.register_button.configure(state="normal")
                elif msg_type == "auth_error":
                    self.login_button.configure(state="normal")
                    self.register_button.configure(state="normal")
                    self.account_status.configure(
                        text="No autenticado", text_color="#dc3545"
                    )
                    messagebox.showerror(
                        "Error de autenticación",
                        msg.get("message", "No se pudo iniciar sesión."),
                    )
                elif msg_type == "credits_ok":
                    self.credits = int(msg["credits"])
                    self.credits_label.configure(text=f"Créditos: {self.credits}")
                elif msg_type == "checkout_ok":
                    self.buy_button.configure(state="normal")
                    self.account_status.configure(
                        text="Checkout listo. Se abrió Mercado Pago.",
                        text_color="#2ecc71",
                    )
                    webbrowser.open(msg["url"])
                elif msg_type == "checkout_error":
                    self.buy_button.configure(state="normal")
                    self.account_status.configure(
                        text="No se pudo crear el checkout.", text_color="#dc3545"
                    )
                    messagebox.showerror(
                        "Mercado Pago",
                        msg.get("message", "No se pudo crear el checkout."),
                    )
                elif msg_type == "status":
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
                    text_value = msg.get("text", "").strip()
                    if text_value:
                        self._append_to_textbox(text_value)
                elif msg_type == "done":
                    if not self.is_transcribing:
                        continue
                    self.is_transcribing = False
                    self.transcription_results = msg.get("results", [])
                    if msg.get("credits_remaining") is not None:
                        self.credits = int(msg["credits_remaining"])
                        self.credits_label.configure(text=f"Créditos: {self.credits}")
                    self.progress_bar.set(1.0)
                    self.status_label.configure(
                        text="Estado: Transcripción completada con éxito."
                    )
                    self.status_indicator.configure(fg_color="#28a745")
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(
                        text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff"
                    )
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    self.btn_export_txt.configure(state="normal")
                    self.btn_export_srt.configure(state="normal")
                    if self.transcription_mode == "online":
                        self._refresh_credits_async()
                    messagebox.showinfo(
                        "Éxito", "La transcripción se ha completado correctamente."
                    )
                elif msg_type == "credits_error":
                    self.is_transcribing = False
                    self.progress_bar.set(0.0)
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(
                        text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff"
                    )
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    messagebox.showwarning(
                        "Sin créditos",
                        msg.get("message", "No tienes créditos suficientes."),
                    )
                elif msg_type == "error":
                    self.is_transcribing = False
                    self.progress_bar.set(0.0)
                    self.status_label.configure(text="Estado: Error")
                    self.status_indicator.configure(fg_color="#dc3545")
                    self.drop_zone_frame.configure(fg_color="#1a2744")
                    self.drop_main_label.configure(
                        text="Arrastra y suelta tu archivo aquí", text_color="#d0e8ff"
                    )
                    self.drop_icon_label.configure(text="📂")
                    self.btn_transcribe.configure(state="normal")
                    messagebox.showerror(
                        "Error de Transcripción",
                        f"Ocurrió un error:\n{msg.get('message', '')}",
                    )
        except queue.Empty:
            pass

        self.after(100, self.poll_queue)

    def _append_to_textbox(self, text: str):
        self.text_preview.configure(state="normal")
        self.text_preview.insert("end", text + "\n")
        self.text_preview.see("end")
        self.text_preview.configure(state="disabled")

    def _clear_textbox(self):
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.configure(state="disabled")

    def _create_stream_proxy(self, original):
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
        log_win = ctk.CTkToplevel(self)
        log_win.title("Log de Errores")
        log_win.geometry("600x400")
        txt = ctk.CTkTextbox(
            log_win, font=("Consolas", 10), state="normal"
        )
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self._log_buffer.getvalue())
        txt.configure(state="disabled")

        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(self._log_buffer.getvalue())

        copy_btn = ctk.CTkButton(
            log_win, text="Copiar al Portapapeles", command=copy_to_clipboard
        )
        copy_btn.pack(pady=(0, 10))
        log_win.grab_set()
        log_win.focus_set()

    def _export_txt(self):
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
