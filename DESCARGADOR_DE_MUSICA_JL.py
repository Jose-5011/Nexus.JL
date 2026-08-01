import os
import sys
import subprocess
import shutil
import tempfile
import threading
import queue
import time
import webbrowser
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox

try:
    import requests
    REQUESTS_DISPONIBLE = True
    REQUESTS_ERROR = None
except ImportError as _e:
    REQUESTS_DISPONIBLE = False
    REQUESTS_ERROR = str(_e)

ANIME_API_BASE = "https://animeflv.ahmedrangel.com/api"

# ---------------------------------------------------------------------------
# Versión / actualizaciones (vía GitHub Releases)
# ---------------------------------------------------------------------------
APP_VERSION = "1.0.0"
GITHUB_REPO = "Jose-5011/Nexus.JL"


def _version_a_tupla(v):
    v = (v or "").lstrip("vV")
    partes = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        partes.append(int(num) if num else 0)
    return tuple(partes) or (0,)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EXTENSIONES_AUDIO_VALIDAS = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".aac", ".wav", ".flac"}
FLAGS_SUBPROCESO = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Nombre del archivo de texto con una copia del código fuente, empacado
# junto al .exe (ver pyinstaller --add-data en las notas de build).
CODE_SOURCE_FILENAME = "codigo_fuente.txt"
CREDITOS_TEXTO = (
    "Nexus JL\n"
    "Hecho por JL\n\n"
    "Usa yt-dlp + ffmpeg como motores de descarga/conversión.\n"
    "Soporta YouTube y X (Twitter).\n\n"
    "----------------------------------------\n"
    "CÓDIGO FUENTE\n"
    "----------------------------------------\n\n"
)


def resource_path(relative_path):
    """Resuelve rutas tanto en modo script como empaquetado con PyInstaller."""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


def ruta_binario(nombre):
    """Ubica un binario externo (yt-dlp/ffmpeg): primero busca la copia
    empacada junto al .exe (carpeta bin/), y si no existe cae de vuelta
    al PATH del sistema. Así el instalador funciona en una PC limpia sin
    que el usuario tenga que instalar nada aparte."""
    empacado = resource_path(os.path.join("bin", nombre + (".exe" if os.name == "nt" else "")))
    if os.path.isfile(empacado):
        return empacado
    return shutil.which(nombre) or nombre


# Se resuelven una sola vez al iniciar: evita repetir la búsqueda en cada descarga.
BIN_YTDLP = ruta_binario("yt-dlp")
BIN_FFMPEG = ruta_binario("ffmpeg")

# Formatos de salida disponibles: tipo (audio/video), extensión final y opciones de calidad
FORMATOS = {
    "WAV": {"tipo": "audio", "ext": "wav", "calidades": ["Sin pérdida"]},
    "MP3": {"tipo": "audio", "ext": "mp3", "calidades": ["128 kbps", "192 kbps", "256 kbps", "320 kbps"]},
    "M4A": {"tipo": "audio", "ext": "m4a", "calidades": ["128 kbps", "192 kbps", "256 kbps"]},
    "FLAC": {"tipo": "audio", "ext": "flac", "calidades": ["Sin pérdida"]},
    "MP4 (video)": {"tipo": "video", "ext": "mp4", "calidades": ["480p", "720p", "1080p", "Mejor disponible"]},
}
MODOS = ["Solo este video", "Playlist completa"]

# Paleta violeta oscura
BG = "#141018"
BG_CARD = "#1e1826"
BG_INPUT = "#241c30"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#7c3aed"
ACCENT_SOFT = "#2a1f3d"
TEXT_MAIN = "#f0eefc"
TEXT_DIM = "#8d84a8"
GREEN = "#4ade80"
RED = "#f87171"
RED_HOVER = "#dc2626"
YELLOW = "#facc15"

ctk.set_appearance_mode("dark")


class YouTubeDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Nexus JL")
        self.root.geometry("720x660")
        self.root.configure(fg_color=BG)
        self.root.minsize(620, 560)

        self.message_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.proc_actual = None
        self.archivo_en_progreso = None

        # Estado de la pestaña Anime
        self.anime_resultados = []       # lista de dicts devueltos por api.search()
        self.anime_id_actual = None      # id del anime seleccionado (para pedir episodios/links)
        self.anime_info_actual = None    # info completa del anime seleccionado (con episodios)
        self.anime_episodio_actual = None
        self.anime_cancel_event = threading.Event()
        self.anime_proc_actual = None

        self.setup_ui()
        self.process_messages()
        self.log_message("Aplicación iniciada.", "dim")
        self.verificar_dependencias()
        self.verificar_actualizaciones()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def setup_ui(self):
        outer = ctk.CTkFrame(self.root, fg_color=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        # Header
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", pady=(0, 20))

        ctk.CTkLabel(header, text="🌐", font=ctk.CTkFont(size=28)).pack(side="left", padx=(0, 10))
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(title_box, text="Nexus JL", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=TEXT_MAIN).pack(anchor="w")
        ctk.CTkLabel(title_box, text="YouTube / X + Anime  ·  by JL", font=ctk.CTkFont(size=12),
                     text_color=TEXT_DIM).pack(anchor="w")

        self.status_dot = ctk.CTkLabel(header, text="●", font=ctk.CTkFont(size=16), text_color=TEXT_DIM)
        self.status_dot.pack(side="right", padx=(0, 4))
        self.status_text = ctk.CTkLabel(header, text="listo", font=ctk.CTkFont(size=12), text_color=TEXT_DIM)
        self.status_text.pack(side="right", padx=(0, 6))

        self.credits_btn = ctk.CTkButton(
            header, text="ℹ", width=28, height=28, corner_radius=14,
            fg_color=BG_INPUT, hover_color=ACCENT_SOFT, text_color=TEXT_DIM,
            font=ctk.CTkFont(size=14, weight="bold"), command=self.abrir_creditos
        )
        self.credits_btn.pack(side="right", padx=(0, 12))

        self.anime_btn = ctk.CTkButton(
            header, text="🎬 Anime", width=90, height=28, corner_radius=14,
            fg_color=BG_INPUT, hover_color=ACCENT_SOFT, text_color=TEXT_DIM,
            font=ctk.CTkFont(size=12, weight="bold"), command=self.abrir_anime
        )
        self.anime_btn.pack(side="right", padx=(0, 8))

        # Card: input
        card = ctk.CTkFrame(outer, fg_color=BG_CARD, corner_radius=14)
        card.pack(fill="x", pady=(0, 16))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=18)

        ctk.CTkLabel(inner, text="URL DE YOUTUBE / X", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))

        self.url_entry = ctk.CTkEntry(
            inner, placeholder_text="https://youtube.com/watch?v=...  o  https://x.com/usuario/status/...",
            fg_color=BG_INPUT, border_color=ACCENT_SOFT, border_width=1.5,
            text_color=TEXT_MAIN, font=ctk.CTkFont(size=13), height=42, corner_radius=10
        )
        self.url_entry.pack(fill="x", pady=(0, 14))
        self.url_entry.bind("<Return>", lambda e: self.iniciar_descarga())

        # Fila: formato + calidad
        opciones_row = ctk.CTkFrame(inner, fg_color="transparent")
        opciones_row.pack(fill="x", pady=(0, 12))
        opciones_row.grid_columnconfigure(0, weight=1)
        opciones_row.grid_columnconfigure(1, weight=1)

        formato_box = ctk.CTkFrame(opciones_row, fg_color="transparent")
        formato_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(formato_box, text="FORMATO", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.formato_var = ctk.StringVar(value="WAV")
        self.formato_menu = ctk.CTkOptionMenu(
            formato_box, values=list(FORMATOS.keys()), variable=self.formato_var,
            command=self.actualizar_calidades, fg_color=BG_INPUT, button_color=ACCENT_SOFT,
            button_hover_color=ACCENT, dropdown_fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=38, corner_radius=10
        )
        self.formato_menu.pack(fill="x")

        calidad_box = ctk.CTkFrame(opciones_row, fg_color="transparent")
        calidad_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(calidad_box, text="CALIDAD", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.calidad_var = ctk.StringVar(value="Sin pérdida")
        self.calidad_menu = ctk.CTkOptionMenu(
            calidad_box, values=FORMATOS["WAV"]["calidades"], variable=self.calidad_var,
            fg_color=BG_INPUT, button_color=ACCENT_SOFT, button_hover_color=ACCENT,
            dropdown_fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=38, corner_radius=10
        )
        self.calidad_menu.pack(fill="x")

        # Fila: modo (video individual / playlist)
        modo_box = ctk.CTkFrame(inner, fg_color="transparent")
        modo_box.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(modo_box, text="MODO", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.modo_var = ctk.StringVar(value=MODOS[0])
        self.modo_menu = ctk.CTkOptionMenu(
            modo_box, values=MODOS, variable=self.modo_var,
            fg_color=BG_INPUT, button_color=ACCENT_SOFT, button_hover_color=ACCENT,
            dropdown_fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=38, corner_radius=10
        )
        self.modo_menu.pack(fill="x")

        self.download_btn = ctk.CTkButton(
            inner, text="⬇  INICIAR DESCARGA", command=self.iniciar_descarga,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"), height=44, corner_radius=10
        )
        self.download_btn.pack(fill="x")

        self.progress = ctk.CTkProgressBar(
            inner, mode="indeterminate", fg_color=BG_INPUT, progress_color=ACCENT, height=6, corner_radius=3
        )
        self.progress.pack(fill="x", pady=(12, 0))
        self.progress.set(0)

        # Footer: versión + botón de actualización (oculto hasta que se detecte una nueva).
        # Se empaqueta con side="bottom" ANTES que el log_card de abajo, para que
        # reserve su espacio en vez de que el log (fill=both, expand=True) se lo coma.
        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(8, 0))

        self.version_label = ctk.CTkLabel(
            footer, text=f"v{APP_VERSION}", font=ctk.CTkFont(size=10), text_color=TEXT_DIM
        )
        self.version_label.pack(side="right")

        self.update_btn = ctk.CTkButton(
            footer, text="🔔 Actualización disponible", command=self.descargar_actualizacion,
            fg_color=BG_INPUT, hover_color=ACCENT_SOFT, text_color=ACCENT,
            font=ctk.CTkFont(size=11, weight="bold"), height=26, corner_radius=8
        )
        # No se muestra (no se hace .pack todavía) hasta detectar una versión nueva.

        # Log
        log_header = ctk.CTkFrame(outer, fg_color="transparent")
        log_header.pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(log_header, text="REGISTRO DE ACTIVIDAD", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(side="left")

        log_card = ctk.CTkFrame(outer, fg_color=BG_CARD, corner_radius=14)
        log_card.pack(fill="both", expand=True)

        self.log_area = ctk.CTkTextbox(
            log_card, fg_color="transparent", text_color=TEXT_MAIN,
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=14, wrap="word"
        )
        self.log_area.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_area.configure(state="disabled")

        self.log_area.tag_config("ok", foreground=GREEN)
        self.log_area.tag_config("err", foreground=RED)
        self.log_area.tag_config("warn", foreground=YELLOW)
        self.log_area.tag_config("dim", foreground=TEXT_DIM)

    def abrir_anime(self):
        """Abre el buscador de Anime en una ventana aparte, para no competir
        por espacio vertical con el registro de actividad de descargas."""
        if getattr(self, "_anime_window", None) is not None and self._anime_window.winfo_exists():
            self._anime_window.lift()
            self._anime_window.focus()
            return
        ventana = ctk.CTkToplevel(self.root)
        ventana.title("Buscador de Anime")
        ventana.geometry("640x680")
        ventana.minsize(560, 560)
        ventana.configure(fg_color=BG)
        ventana.transient(self.root)
        self._anime_window = ventana
        self.setup_anime_tab(ventana)

    def actualizar_calidades(self, formato_seleccionado):
        calidades = FORMATOS[formato_seleccionado]["calidades"]
        self.calidad_menu.configure(values=calidades)
        self.calidad_var.set(calidades[0])

    # ------------------------------------------------------------------
    # Estado / dependencias
    # ------------------------------------------------------------------
    def set_estado(self, estado, texto):
        """Actualiza el punto de color y el texto de estado del header.
        estado: 'idle' | 'working' | 'ok' | 'error'."""
        colores = {
            "idle": TEXT_DIM,
            "working": YELLOW,
            "ok": GREEN,
            "error": RED,
        }
        color = colores.get(estado, TEXT_DIM)
        self.status_dot.configure(text_color=color)
        self.status_text.configure(text=texto, text_color=color)

    def verificar_dependencias(self):
        """Revisa que yt-dlp y ffmpeg estén disponibles (empacados o en el PATH)."""
        faltantes = []
        if not os.path.isfile(BIN_YTDLP):
            faltantes.append("yt-dlp")
        if not os.path.isfile(BIN_FFMPEG):
            faltantes.append("ffmpeg")
        if faltantes:
            self.log_message(
                "Faltan dependencias en el PATH: " + ", ".join(faltantes) +
                ". Instálalas para poder descargar/convertir.", "err"
            )
            self.set_estado("error", "faltan dependencias")
        else:
            self.log_message("yt-dlp y ffmpeg detectados correctamente.", "ok")
            self.set_estado("idle", "listo")

    # ------------------------------------------------------------------
    # Actualizaciones (vía GitHub Releases)
    # ------------------------------------------------------------------
    def verificar_actualizaciones(self):
        if not REQUESTS_DISPONIBLE:
            return
        threading.Thread(target=self._verificar_actualizaciones_worker, daemon=True).start()

    def _verificar_actualizaciones_worker(self):
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json"}, timeout=10
            )
            if resp.status_code != 200:
                return  # sin releases publicados todavía, sin internet, etc. — se ignora en silencio
            data = resp.json()
            version_remota = data.get("tag_name", "")
            if _version_a_tupla(version_remota) <= _version_a_tupla(APP_VERSION):
                return  # ya estamos en la última versión

            url_instalador = next(
                (a.get("browser_download_url") for a in data.get("assets", [])
                 if a.get("name", "").lower().endswith(".exe")),
                None
            )
            if not url_instalador:
                return  # el release no tiene un .exe adjunto

            self._actualizacion_disponible = {"version": version_remota, "url": url_instalador}
            self.log_message(f"Hay una nueva versión disponible: {version_remota}", "ok")
            self.root.after(0, self._mostrar_boton_actualizacion)
        except Exception:
            pass  # revisar actualizaciones nunca debe interrumpir el uso normal de la app

    def _mostrar_boton_actualizacion(self):
        version = self._actualizacion_disponible["version"]
        self.update_btn.configure(text=f"🔔 Actualizar a {version}")
        self.update_btn.pack(side="right", padx=(0, 10))

    def descargar_actualizacion(self):
        info = getattr(self, "_actualizacion_disponible", None)
        if not info:
            return
        self.update_btn.configure(state="disabled", text="Descargando...")
        threading.Thread(target=self._descargar_actualizacion_worker, args=(info["url"],), daemon=True).start()

    def _descargar_actualizacion_worker(self, url):
        try:
            destino = os.path.join(tempfile.gettempdir(), "NexusJL_actualizacion.exe")
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(destino, "wb") as f:
                    for fragmento in resp.iter_content(chunk_size=262144):
                        f.write(fragmento)
            self.log_message("Actualización descargada. Abriendo instalador...", "ok")
            subprocess.Popen([destino])  # sin CREATE_NO_WINDOW: el instalador necesita mostrarse
            self.root.after(800, self._cerrar_para_actualizar)
        except Exception as e:
            self.log_message(f"No se pudo descargar la actualización: {e}", "err")
            self.root.after(0, lambda: self.update_btn.configure(
                state="normal", text="🔔 Reintentar actualización"))

    def _cerrar_para_actualizar(self):
        self.root.destroy()

    # ------------------------------------------------------------------
    # Pestaña Anime (vía la API REST no oficial de AnimeFLV)
    # ------------------------------------------------------------------
    def setup_anime_tab(self, parent):
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        if not REQUESTS_DISPONIBLE:
            ctk.CTkLabel(
                inner,
                text="Falta el paquete 'requests'.\n\nInstálalo con:\npip install requests\n\ny vuelve a abrir el programa.",
                font=ctk.CTkFont(size=13), text_color=RED, justify="left"
            ).pack(anchor="w", pady=20, padx=10)
            return

        ctk.CTkLabel(inner, text="BUSCAR ANIME", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(6, 6))

        buscador_row = ctk.CTkFrame(inner, fg_color="transparent")
        buscador_row.pack(fill="x", pady=(0, 12))

        self.anime_busqueda_entry = ctk.CTkEntry(
            buscador_row, placeholder_text="Nombre del anime (ej. Overlord)",
            fg_color=BG_INPUT, border_color=ACCENT_SOFT, border_width=1.5,
            text_color=TEXT_MAIN, font=ctk.CTkFont(size=13), height=42, corner_radius=10
        )
        self.anime_busqueda_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.anime_busqueda_entry.bind("<Return>", lambda e: self.anime_buscar())

        self.anime_buscar_btn = ctk.CTkButton(
            buscador_row, text="🔍 Buscar", command=self.anime_buscar, width=110,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"), height=42, corner_radius=10
        )
        self.anime_buscar_btn.pack(side="left")

        fila_selects = ctk.CTkFrame(inner, fg_color="transparent")
        fila_selects.pack(fill="x", pady=(0, 12))
        fila_selects.grid_columnconfigure(0, weight=1)
        fila_selects.grid_columnconfigure(1, weight=1)

        resultado_box = ctk.CTkFrame(fila_selects, fg_color="transparent")
        resultado_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(resultado_box, text="RESULTADO", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.anime_resultado_var = ctk.StringVar(value="Busca un anime primero...")
        self.anime_resultado_menu = ctk.CTkOptionMenu(
            resultado_box, values=["Busca un anime primero..."], variable=self.anime_resultado_var,
            command=self.anime_seleccionar_resultado, fg_color=BG_INPUT, button_color=ACCENT_SOFT,
            button_hover_color=ACCENT, dropdown_fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=38, corner_radius=10, state="disabled"
        )
        self.anime_resultado_menu.pack(fill="x")

        episodio_box = ctk.CTkFrame(fila_selects, fg_color="transparent")
        episodio_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(episodio_box, text="EPISODIO", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.anime_episodio_var = ctk.StringVar(value="—")
        self.anime_episodio_menu = ctk.CTkOptionMenu(
            episodio_box, values=["—"], variable=self.anime_episodio_var,
            command=self.anime_seleccionar_episodio, fg_color=BG_INPUT, button_color=ACCENT_SOFT,
            button_hover_color=ACCENT, dropdown_fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=38, corner_radius=10, state="disabled"
        )
        self.anime_episodio_menu.pack(fill="x")

        carpeta_row = ctk.CTkFrame(inner, fg_color="transparent")
        carpeta_row.pack(fill="x", pady=(0, 8))
        self.anime_carpeta = None
        self.anime_carpeta_label = ctk.CTkLabel(
            carpeta_row, text="Carpeta destino: (sin elegir — se preguntará al descargar)",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM
        )
        self.anime_carpeta_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            carpeta_row, text="Elegir carpeta", command=self.anime_elegir_carpeta, width=130,
            fg_color=BG_INPUT, hover_color=ACCENT_SOFT, text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12), height=32, corner_radius=8
        ).pack(side="right")

        # Respaldo: siempre disponible una vez elegido el episodio, por si
        # la API no trae servidores (pasa seguido con estos scrapers).
        self.anime_abrir_web_btn = ctk.CTkButton(
            inner, text="🌐 Abrir episodio en AnimeFLV.net (respaldo manual)",
            command=self.anime_abrir_en_web, fg_color=BG_INPUT, hover_color=ACCENT_SOFT,
            text_color=TEXT_MAIN, font=ctk.CTkFont(size=12), height=34, corner_radius=8, state="disabled"
        )
        self.anime_abrir_web_btn.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(inner, text="SERVIDORES DISPONIBLES", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_DIM).pack(anchor="w", pady=(0, 6))
        self.anime_servers_frame = ctk.CTkScrollableFrame(
            inner, fg_color=BG_CARD, corner_radius=12, height=160
        )
        self.anime_servers_frame.pack(fill="both", expand=True)
        self._anime_placeholder_servers()

    def _anime_placeholder_servers(self, texto="Elige un anime y un episodio para ver los servidores disponibles."):
        for w in self.anime_servers_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.anime_servers_frame, text=texto, font=ctk.CTkFont(size=12),
                     text_color=TEXT_DIM, wraplength=480, justify="left").pack(anchor="w", padx=10, pady=10)

    def anime_elegir_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta de destino para episodios de anime")
        if carpeta:
            self.anime_carpeta = carpeta
            self.anime_carpeta_label.configure(text=f"Carpeta destino: {carpeta}")

    def anime_abrir_en_web(self):
        url = getattr(self, "anime_episodio_url_actual", None)
        if url:
            webbrowser.open(url)

    # --- Buscar anime ---
    def anime_buscar(self):
        consulta = self.anime_busqueda_entry.get().strip()
        if not consulta:
            messagebox.showwarning("Falta texto", "Escribe el nombre de un anime para buscar.")
            return
        self.anime_buscar_btn.configure(state="disabled", text="Buscando...")
        self.log_message(f"Buscando anime: {consulta}", "dim")
        threading.Thread(target=self._anime_buscar_worker, args=(consulta,), daemon=True).start()

    def _anime_buscar_worker(self, consulta):
        try:
            resp = requests.get(f"{ANIME_API_BASE}/search", params={"query": consulta}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            resultados = (data.get("data") or {}).get("media") or []
            self.anime_resultados = resultados
            if not resultados:
                self.log_message("No se encontraron resultados.", "warn")
                self.root.after(0, lambda: self.anime_resultado_menu.configure(
                    values=["Sin resultados"], state="disabled"))
            else:
                # Se incluye el tipo (Anime/Película/OVA) para distinguir entradas con el mismo nombre.
                etiquetas = [f"{r.get('title', '(sin título)')} [{r.get('type', '?')}]" for r in resultados]
                self.root.after(0, lambda: self._anime_actualizar_resultados(etiquetas))
                self.log_message(f"Se encontraron {len(resultados)} resultado(s).", "ok")
        except Exception as e:
            self.log_message(f"Error buscando anime: {e}", "err")
        finally:
            self.root.after(0, lambda: self.anime_buscar_btn.configure(state="normal", text="🔍 Buscar"))

    def _anime_actualizar_resultados(self, etiquetas):
        self.anime_resultado_menu.configure(values=etiquetas, state="normal")
        self.anime_resultado_var.set(etiquetas[0])
        self.anime_seleccionar_resultado(etiquetas[0])

    # --- Seleccionar anime -> cargar episodios ---
    def anime_seleccionar_resultado(self, etiqueta_seleccionada):
        valores = list(self.anime_resultado_menu.cget("values"))
        if etiqueta_seleccionada not in valores:
            return
        indice = valores.index(etiqueta_seleccionada)
        if indice >= len(self.anime_resultados):
            return
        slug = self.anime_resultados[indice].get("slug")
        self.anime_slug_actual = slug
        self.anime_episodio_menu.configure(values=["Cargando..."], state="disabled")
        self.anime_episodio_var.set("Cargando...")
        self.anime_abrir_web_btn.configure(state="disabled")
        self._anime_placeholder_servers("Cargando episodios...")
        threading.Thread(target=self._anime_cargar_info_worker, args=(slug,), daemon=True).start()

    def _anime_cargar_info_worker(self, slug):
        try:
            resp = requests.get(f"{ANIME_API_BASE}/anime/{slug}", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            episodios = (data.get("data") or {}).get("episodes") or []
            episodios = sorted(episodios, key=lambda e: e.get("number", 0), reverse=True)
            self.anime_episodios_ordenados = episodios
            etiquetas = [f"Episodio {ep.get('number')}" for ep in episodios]
            if not etiquetas:
                self.log_message("Este anime no tiene episodios listados.", "warn")
                return
            self.root.after(0, lambda: self._anime_actualizar_episodios(etiquetas))
            self.log_message(f"Cargados {len(episodios)} episodio(s).", "ok")
        except Exception as e:
            self.log_message(f"Error cargando episodios: {e}", "err")

    def _anime_actualizar_episodios(self, etiquetas):
        self.anime_episodio_menu.configure(values=etiquetas, state="normal")
        self.anime_episodio_var.set(etiquetas[0])
        self.anime_seleccionar_episodio(etiquetas[0])

    # --- Seleccionar episodio -> cargar servidores ---
    def anime_seleccionar_episodio(self, etiqueta_seleccionada):
        valores = list(self.anime_episodio_menu.cget("values"))
        if etiqueta_seleccionada not in valores or not hasattr(self, "anime_episodios_ordenados"):
            return
        indice = valores.index(etiqueta_seleccionada)
        if indice >= len(self.anime_episodios_ordenados):
            return
        episodio = self.anime_episodios_ordenados[indice]
        episodio_slug = episodio.get("slug")
        self.anime_episodio_url_actual = episodio.get("url")
        self.anime_abrir_web_btn.configure(state="normal" if self.anime_episodio_url_actual else "disabled")
        self._anime_placeholder_servers("Buscando servidores...")
        threading.Thread(target=self._anime_buscar_servers_worker, args=(episodio_slug,), daemon=True).start()

    def _anime_buscar_servers_worker(self, episodio_slug):
        try:
            resp = requests.get(f"{ANIME_API_BASE}/anime/episode/{episodio_slug}", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            servidores = (data.get("data") or {}).get("servers") or []
            self.root.after(0, lambda: self._anime_mostrar_servers(servidores))
            if servidores:
                self.log_message(f"Se encontraron {len(servidores)} servidor(es).", "ok")
            else:
                self.log_message(
                    "La API no devolvió servidores para este episodio (pasa seguido con este servicio). "
                    "Usa el botón de respaldo para verlo directo en AnimeFLV.net.", "warn"
                )
        except Exception as e:
            self.log_message(f"Error obteniendo servidores: {e}", "err")
            self.root.after(0, lambda: self._anime_placeholder_servers(
                "No se pudieron cargar los servidores. Usa el botón de respaldo de arriba."))

    def _anime_mostrar_servers(self, servidores):
        for w in self.anime_servers_frame.winfo_children():
            w.destroy()
        if not servidores:
            self._anime_placeholder_servers(
                "No hay servidores disponibles para este episodio ahora mismo.\n"
                "Usa el botón de respaldo para verlo directo en AnimeFLV.net."
            )
            return
        for s in servidores:
            nombre = s.get("name") or "Desconocido"
            url_embed = s.get("embed")
            url_descarga = s.get("download")
            fila = ctk.CTkFrame(self.anime_servers_frame, fg_color=BG_INPUT, corner_radius=10)
            fila.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(fila, text=nombre, font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=TEXT_MAIN).pack(side="left", padx=12, pady=10)
            if url_embed:
                ctk.CTkButton(
                    fila, text="Ver online", width=90, height=30, fg_color=ACCENT_SOFT, hover_color=ACCENT,
                    text_color=TEXT_MAIN, font=ctk.CTkFont(size=12),
                    command=lambda u=url_embed: webbrowser.open(u)
                ).pack(side="right", padx=(4, 12), pady=6)
                ctk.CTkButton(
                    fila, text="Descargar", width=90, height=30, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                    text_color="#ffffff", font=ctk.CTkFont(size=12),
                    command=lambda u=url_embed, s=nombre: self.anime_intentar_descarga(u, s)
                ).pack(side="right", padx=4, pady=6)
            elif url_descarga:
                ctk.CTkButton(
                    fila, text="Abrir descarga", width=110, height=30, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                    text_color="#ffffff", font=ctk.CTkFont(size=12),
                    command=lambda u=url_descarga: webbrowser.open(u)
                ).pack(side="right", padx=(4, 12), pady=6)

    # --- Intentar descarga directa con yt-dlp (funciona solo en algunos servidores) ---
    def anime_intentar_descarga(self, url, servidor):
        carpeta = self.anime_carpeta
        if not carpeta:
            carpeta = filedialog.askdirectory(title="Carpeta de destino para el episodio")
            if not carpeta:
                return
            self.anime_carpeta = carpeta
            self.anime_carpeta_label.configure(text=f"Carpeta destino: {carpeta}")

        self.log_message(f"Intentando descargar desde {servidor} con yt-dlp...", "dim")
        threading.Thread(target=self._anime_descarga_worker, args=(url, carpeta, servidor), daemon=True).start()

    def _anime_descarga_worker(self, url, carpeta, servidor):
        comando = [BIN_YTDLP, "--newline", "--restrict-filenames",
                   "-o", os.path.join(carpeta, "%(title)s.%(ext)s"), url]
        try:
            proc = subprocess.run(comando, capture_output=True, text=True, creationflags=FLAGS_SUBPROCESO)
            if proc.returncode == 0:
                self.log_message(f"Episodio descargado desde {servidor}.", "ok")
            else:
                self.log_message(
                    f"yt-dlp no pudo descargar desde {servidor} (ese servidor no está soportado). "
                    "Usa el botón 'Ver online' y descárgalo manualmente desde el navegador.", "warn"
                )
        except Exception as e:
            self.log_message(f"Error al intentar descargar: {e}", "err")

    # ------------------------------------------------------------------
    # Créditos / código fuente
    # ------------------------------------------------------------------
    def abrir_creditos(self):
        ventana = ctk.CTkToplevel(self.root)
        ventana.title("Créditos y código fuente")
        ventana.geometry("700x600")
        ventana.configure(fg_color=BG)
        ventana.transient(self.root)

        ctk.CTkLabel(ventana, text="Nexus JL", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=TEXT_MAIN).pack(pady=(18, 2))
        ctk.CTkLabel(ventana, text="Hecho por JL  ·  usa yt-dlp + ffmpeg", font=ctk.CTkFont(size=12),
                     text_color=TEXT_DIM).pack(pady=(0, 14))

        textbox = ctk.CTkTextbox(
            ventana, fg_color=BG_CARD, text_color=TEXT_MAIN,
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=12, wrap="none"
        )
        textbox.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        textbox.insert("1.0", self._cargar_codigo_fuente())
        textbox.configure(state="disabled")

    def _cargar_codigo_fuente(self):
        """Busca primero el .txt empacado (modo PyInstaller); si no existe,
        cae de nuevo a leer este mismo script (modo desarrollo)."""
        try:
            with open(resource_path(CODE_SOURCE_FILENAME), "r", encoding="utf-8") as f:
                return CREDITOS_TEXTO + f.read()
        except Exception:
            pass
        try:
            with open(__file__, "r", encoding="utf-8") as f:
                return CREDITOS_TEXTO + f.read()
        except Exception as e:
            return CREDITOS_TEXTO + f"(No se pudo cargar el código fuente: {e})"

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------
    def log_message(self, message, tag="dim"):
        self.message_queue.put((message, tag))

    def process_messages(self):
        try:
            while True:
                msg, tag = self.message_queue.get_nowait()
                hora = datetime.now().strftime("%H:%M:%S")
                self.log_area.configure(state="normal")
                self.log_area.insert("end", f"[{hora}] ", "dim")
                self.log_area.insert("end", f"{msg}\n", tag)
                self.log_area.see("end")
                self.log_area.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.process_messages)

    # ------------------------------------------------------------------
    # Controles de inicio / cancelación
    # ------------------------------------------------------------------
    def _set_controles_activos(self, activos):
        estado = "normal" if activos else "disabled"
        self.formato_menu.configure(state=estado)
        self.calidad_menu.configure(state=estado)
        self.modo_menu.configure(state=estado)
        self.url_entry.configure(state=estado)

    def iniciar_descarga(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Falta URL", "Pega una URL de YouTube antes de descargar.")
            return
        carpeta = filedialog.askdirectory(title="Seleccionar carpeta de destino")
        if not carpeta:
            return

        formato = self.formato_var.get()
        calidad = self.calidad_var.get()
        modo = self.modo_var.get()

        self.cancel_event = threading.Event()
        self.proc_actual = None
        self.archivo_en_progreso = None

        self._set_controles_activos(False)
        self.download_btn.configure(
            text="✕  CANCELAR", fg_color=RED, hover_color=RED_HOVER,
            command=self.cancelar_descarga, state="normal"
        )
        self.set_estado("working", "descargando")
        self.progress.start()
        threading.Thread(target=self.proceso_total, args=(url, carpeta, formato, calidad, modo), daemon=True).start()

    def cancelar_descarga(self):
        self.log_message("Cancelando y limpiando archivos temporales...", "warn")
        self.cancel_event.set()
        self.download_btn.configure(state="disabled", text="CANCELANDO...")
        if self.proc_actual and self.proc_actual.poll() is None:
            try:
                self.proc_actual.terminate()
            except Exception:
                pass

    def _resetear_boton(self):
        self.progress.stop()
        self.progress.set(0)
        self._set_controles_activos(True)
        self.download_btn.configure(
            state="normal", text="⬇  INICIAR DESCARGA",
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.iniciar_descarga
        )

    # ------------------------------------------------------------------
    # Ejecutar un subproceso de forma cancelable
    # ------------------------------------------------------------------
    def _ejecutar_cancelable(self, comando, salida_stdout=None):
        """Corre un comando y permite terminarlo si se activa cancel_event.
        Devuelve (codigo_salida, cancelado: bool).

        IMPORTANTE: stdout/stderr se descartan con DEVNULL. ffmpeg escribe
        mucho texto de progreso en stderr; si se captura con PIPE y nadie lo
        lee, el buffer se llena y el proceso se congela esperando a que lo
        vacíen (aunque el archivo de salida ya haya quedado completo en
        disco). Eso hacía que la app pensara que seguía convirtiendo para
        siempre."""
        self.proc_actual = subprocess.Popen(
            comando,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True, creationflags=FLAGS_SUBPROCESO
        )
        while self.proc_actual.poll() is None:
            if self.cancel_event.is_set():
                try:
                    self.proc_actual.terminate()
                except Exception:
                    pass
                self.proc_actual.wait()
                return self.proc_actual.returncode, True
            time.sleep(0.15)
        return self.proc_actual.returncode, False

    # ------------------------------------------------------------------
    # Conversión de audio (ffmpeg) — cancelable
    # ------------------------------------------------------------------
    def convertir_archivo(self, archivo_origen, archivo_salida, formato_info, calidad):
        self.archivo_en_progreso = archivo_salida
        comando = [BIN_FFMPEG, "-i", archivo_origen]

        if formato_info["ext"] == "wav":
            comando += ["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2"]
        elif formato_info["ext"] == "flac":
            comando += ["-codec:a", "flac"]
        elif formato_info["ext"] == "mp3":
            bitrate = calidad.split()[0] + "k"
            comando += ["-codec:a", "libmp3lame", "-b:a", bitrate]
        elif formato_info["ext"] == "m4a":
            bitrate = calidad.split()[0] + "k"
            comando += ["-codec:a", "aac", "-b:a", bitrate]

        comando += ["-y", archivo_salida]

        codigo, cancelado = self._ejecutar_cancelable(comando)
        self.archivo_en_progreso = None

        if cancelado:
            self._borrar_si_existe(archivo_salida)
            return "cancelado"
        if codigo != 0:
            self.log_message(f"Falló conversión de {os.path.basename(archivo_origen)}", "err")
            self._borrar_si_existe(archivo_salida)
            return "error"
        return "ok"

    @staticmethod
    def _borrar_si_existe(ruta):
        try:
            if ruta and os.path.exists(ruta):
                os.remove(ruta)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Proceso principal
    # ------------------------------------------------------------------
    def proceso_total(self, url, carpeta, formato, calidad, modo):
        formato_info = FORMATOS[formato]
        es_video = formato_info["tipo"] == "video"
        sin_playlist = (modo == "Solo este video")

        try:
            self.log_message("Preparando entorno...", "dim")
            with tempfile.TemporaryDirectory() as temp_dir:

                if es_video:
                    altura = {"480p": 480, "720p": 720, "1080p": 1080}.get(calidad)
                    selector = f"bestvideo[height<={altura}]+bestaudio/best" if altura else "bestvideo+bestaudio/best"
                    comando = [BIN_YTDLP, "-f", selector, "--merge-output-format", "mp4",
                               "--newline", "--restrict-filenames", "--concurrent-fragments", "4"]
                else:
                    comando = [BIN_YTDLP, "-f", "bestaudio/best", "--newline", "--restrict-filenames",
                               "--concurrent-fragments", "4"]

                if sin_playlist:
                    comando.append("--no-playlist")

                comando += ["-o", os.path.join(temp_dir, "%(title)s.%(ext)s"), url]

                codigo, cancelado = self._ejecutar_descarga_con_log(comando)

                if cancelado:
                    self.log_message("Descarga cancelada. Archivos temporales eliminados.", "warn")
                    self.set_estado("idle", "cancelado")
                    return

                if codigo != 0:
                    self.log_message(f"yt-dlp terminó con error (código {codigo}). Revisa la URL.", "err")
                    self.set_estado("error", "error")
                    return

                if es_video:
                    archivos = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.lower().endswith(".mp4")]
                    if not archivos:
                        self.log_message("No se generó ningún archivo de video.", "warn")
                        self.set_estado("error", "sin resultados")
                        return

                    self.log_message(f"Se descargaron {len(archivos)} video(s). Copiando a destino...", "dim")
                    copiados = 0
                    for arch in archivos:
                        if self.cancel_event.is_set():
                            self.log_message("Cancelado antes de terminar de copiar todos los archivos.", "warn")
                            break
                        destino = os.path.join(carpeta, os.path.basename(arch))
                        shutil.copy2(arch, destino)
                        self.log_message(f"Listo: {os.path.basename(destino)}", "ok")
                        copiados += 1

                    if self.cancel_event.is_set():
                        self.set_estado("idle", "cancelado")
                    elif copiados == len(archivos):
                        self.log_message(f"PROCESO COMPLETADO: {copiados}/{len(archivos)} video(s)", "ok")
                        self.set_estado("ok", "completado")
                    return

                # --- Audio: convertir cada archivo descargado ---
                archivos = [
                    os.path.join(temp_dir, f) for f in os.listdir(temp_dir)
                    if os.path.splitext(f)[1].lower() in EXTENSIONES_AUDIO_VALIDAS
                ]

                if not archivos:
                    self.log_message("No se descargó ningún archivo de audio válido.", "warn")
                    self.set_estado("error", "sin resultados")
                    return

                self.log_message(f"Se descargaron {len(archivos)} archivo(s). Convirtiendo a {formato}...", "dim")

                exitosos = 0
                for arch in archivos:
                    if self.cancel_event.is_set():
                        break
                    nombre_base = os.path.splitext(os.path.basename(arch))[0]
                    salida = os.path.join(carpeta, f"{nombre_base}.{formato_info['ext']}")
                    resultado = self.convertir_archivo(arch, salida, formato_info, calidad)
                    if resultado == "ok":
                        self.log_message(f"Listo: {os.path.basename(salida)}", "ok")
                        exitosos += 1
                    elif resultado == "cancelado":
                        break

                if self.cancel_event.is_set():
                    self.log_message(f"Cancelado. {exitosos} archivo(s) ya convertido(s) se conservan; el resto no se generó.", "warn")
                    self.set_estado("idle", "cancelado")
                elif exitosos == len(archivos):
                    self.log_message(f"PROCESO COMPLETADO: {exitosos}/{len(archivos)} convertidos", "ok")
                    self.set_estado("ok", "completado")
                else:
                    self.log_message(f"PROCESO TERMINADO CON ERRORES: {exitosos}/{len(archivos)} convertidos", "warn")
                    self.set_estado("error", "con errores")

        except Exception as e:
            self.log_message(f"Error inesperado: {e}", "err")
            self.set_estado("error", "error")
        finally:
            if self.archivo_en_progreso:
                self._borrar_si_existe(self.archivo_en_progreso)
            self.root.after(0, self._resetear_boton)

    def _ejecutar_descarga_con_log(self, comando):
        """Corre yt-dlp mostrando el progreso en el log, cancelable."""
        self.proc_actual = subprocess.Popen(
            comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, creationflags=FLAGS_SUBPROCESO
        )
        while True:
            if self.cancel_event.is_set():
                try:
                    self.proc_actual.terminate()
                except Exception:
                    pass
                self.proc_actual.wait()
                return self.proc_actual.returncode, True

            linea = self.proc_actual.stdout.readline()
            if not linea:
                if self.proc_actual.poll() is not None:
                    break
                continue
            if "[download]" in linea and "%" in linea:
                self.log_message(linea.strip(), "dim")
            elif "ERROR" in linea:
                self.log_message(linea.strip(), "err")

        codigo = self.proc_actual.wait()
        return codigo, False


if __name__ == "__main__":
    try:
        root = ctk.CTk()
        app = YouTubeDownloaderGUI(root)
        root.mainloop()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nOcurrió un error arriba ↑ — presiona Enter para cerrar...")