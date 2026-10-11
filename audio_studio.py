#!/usr/bin/env python3
"""
Audio Studio: transcribe y comprime audios paso a paso.

Para probarla sin instalador:
    pip install customtkinter faster-whisper imageio-ffmpeg
    python audio_studio.py
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

# En modo --noconsole (exe sin ventana negra) stdout/stderr son None y algunas librerías fallan.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

ctk.set_default_color_theme("blue")

TIPOS = [("Audio y video", "*.mp3 *.wav *.m4a *.aac *.ogg *.opus *.flac *.wma *.mp4 *.mkv *.mov *.webm"),
         ("Todos los archivos", "*.*")]
IDIOMAS = {"Detectar automáticamente": None, "Español": "es", "English": "en",
           "Português": "pt", "Français": "fr", "Deutsch": "de", "Italiano": "it"}
MODELOS = {"Rápida (menos precisa)": "base", "Equilibrada (recomendada)": "small",
           "Precisa (más lenta)": "medium", "Máxima (muy lenta)": "large-v3"}
PRESETS = {"Voz · muy liviano": ("32k", True), "Voz · equilibrado": ("64k", True),
           "Música · buena calidad": ("128k", False), "Música · alta calidad": ("192k", False)}
CODECS = {"mp3": ["-c:a", "libmp3lame"], "m4a": ["-c:a", "aac"],
          "opus": ["-c:a", "libopus"], "ogg": ["-c:a", "libvorbis"]}

ANCHO_COLUMNA = 640        # ancho fijo del contenido, centrado en la ventana
MS_FOTOGRAMA = 14

# ---------- ajustes configurables ----------
PALETAS = {   # main = color de botones, hover = al pasar el mouse, flash = destello y barra de transición
    "Azul":    {"main": "#1F6AA5", "hover": "#144870", "flash": "#6cb6f0"},
    "Verde":   {"main": "#2E8B57", "hover": "#1F6B41", "flash": "#7fe0a6"},
    "Violeta": {"main": "#7B4FC9", "hover": "#5C37A0", "flash": "#b79af0"},
    "Naranja": {"main": "#D9822B", "hover": "#A8631C", "flash": "#f5b877"},
    "Rosa":    {"main": "#C2447A", "hover": "#963260", "flash": "#f09ac0"},
    "Grafito": {"main": "#4A5568", "hover": "#2D3748", "flash": "#a0aec0"},
}
APARIENCIAS = {"Sistema": "system", "Claro": "light", "Oscuro": "dark"}
TRANSICIONES = {   # pasos = fotogramas del deslizamiento; destello = al pulsar; barra = línea de color
    "Ninguna":  {"pasos": 0,  "destello": False, "barra": False,
                 "desc": "Sin animaciones. Lo más liviano para equipos lentos."},
    "Ligera":   {"pasos": 6,  "destello": True,  "barra": True,
                 "desc": "Deslizamiento rápido, destello en los botones y barra de color."},
    "Completa": {"pasos": 14, "destello": True,  "barra": True,
                 "desc": "Deslizamiento suave con frenado, destello y barra de color."},
}
PERFILES = {   # modelo = calidad sugerida · threads = núcleos a usar (0 = automático) · beam = precisión de búsqueda
    "Básico · PC antiguo o con poca memoria": {
        "modelo": "Rápida (menos precisa)", "threads": 2, "beam": 1, "gpu": False,
        "desc": "Usa pocos recursos y sugiere calidad Rápida. Deja el PC usable mientras trabaja."},
    "Intermedio · notebook o PC común": {
        "modelo": "Equilibrada (recomendada)", "threads": 4, "beam": 3, "gpu": False,
        "desc": "Equilibrio entre velocidad y calidad. Recomendado para la mayoría."},
    "Potente · PC moderno (16 GB o más)": {
        "modelo": "Equilibrada (recomendada)", "threads": 0, "beam": 5, "gpu": False,
        "desc": "Usa todos los núcleos y la máxima precisión de búsqueda."},
    "Con tarjeta NVIDIA · GPU dedicada": {
        "modelo": "Precisa (más lenta)", "threads": 0, "beam": 5, "gpu": True,
        "desc": "Intenta usar la tarjeta gráfica (mucho más rápido). Si no está disponible, usa el procesador solo."},
}
AJUSTES_BASE = {"transiciones": "Completa", "color": "Azul", "apariencia": "Sistema",
                "perfil": "Intermedio · notebook o PC común"}
VALIDOS = {"transiciones": TRANSICIONES, "color": PALETAS, "apariencia": APARIENCIAS, "perfil": PERFILES}


def ruta_ajustes():
    base = os.getenv("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "AudioStudio" / "ajustes.json"


def cargar_ajustes():
    ajustes = dict(AJUSTES_BASE)
    try:
        guardado = json.loads(ruta_ajustes().read_text(encoding="utf-8"))
        for k, v in guardado.items():
            if k in VALIDOS and v in VALIDOS[k]:
                ajustes[k] = v
    except Exception:
        pass
    return ajustes


def guardar_ajustes(ajustes):
    try:
        ruta = ruta_ajustes()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(ajustes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def ffmpeg_exe():
    """ffmpeg incluido en el instalador (imageio-ffmpeg) o, si no, el del sistema."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def fmt_srt(s):
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def fmt_corto(s):
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{sec:02}"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.ajustes = cargar_ajustes()
        ctk.set_appearance_mode(APARIENCIAS[self.ajustes["apariencia"]])

        self.title("Audio Studio")
        self.geometry("760x720")
        self.minsize(700, 660)

        self.archivos: list[Path] = []
        self.accion = None
        self.carpeta: Path | None = None
        self.modelo = None
        self.modelo_clave = None
        self.animando = False
        self.pulsando = False
        self.p = None                  # página (pantalla) actual
        self.constructor_actual = None
        self.volver_a = None
        self.en_config = False
        self.cola = queue.Queue()   # mensajes del hilo de trabajo hacia la interfaz

        ctk.CTkLabel(self, text="🎙️ Audio Studio",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(16, 0))
        self.btn_cfg = ctk.CTkButton(self, text="⚙", width=38, height=38, fg_color="transparent",
                                     hover_color=("gray85", "gray25"), text_color=("gray20", "gray90"),
                                     font=ctk.CTkFont(size=22), command=self.abrir_config)
        self.btn_cfg.place(relx=1, x=-14, y=14, anchor="ne")

        # Columna central de ancho fijo: el contenido queda siempre alineado y centrado.
        self.c = ctk.CTkFrame(self, width=ANCHO_COLUMNA, fg_color="transparent")
        self.c.pack(fill="y", expand=True, pady=(6, 18))
        self.c.pack_propagate(False)

        self.ir(self.pantalla_inicio, 0)
        self.after(100, self._sondear)

    # ---------- atajos a los ajustes ----------
    @property
    def tema(self):
        return PALETAS[self.ajustes["color"]]

    def nivel(self):
        return TRANSICIONES[self.ajustes["transiciones"]]

    def perfil(self):
        return PERFILES[self.ajustes["perfil"]]

    # ---------- navegación y animaciones ----------
    def ir(self, constructor, direccion=1):
        """Construye una pantalla nueva y la desliza. direccion: 1 = avanzar, -1 = volver, 0 = sin animar."""
        self.en_config = False
        viejo = self.p
        nuevo = ctk.CTkFrame(self.c, fg_color="transparent")
        self.p = nuevo
        constructor()
        self.constructor_actual = constructor
        nivel = self.nivel()
        if viejo is None or direccion == 0 or nivel["pasos"] == 0:
            nuevo.place(relx=0, rely=0, relwidth=1, relheight=1)
            if viejo is not None:
                viejo.destroy()
            return
        self.animando = True
        nuevo.place(relx=direccion, rely=0, relwidth=1, relheight=1)
        linea = None
        if nivel["barra"]:   # barra de color en el borde de la pantalla que entra
            linea = ctk.CTkFrame(self.c, width=5, corner_radius=0, fg_color=self.tema["flash"])
        self._animar(viejo, nuevo, direccion, 0, nivel["pasos"], linea)

    def _animar(self, viejo, nuevo, d, paso, total, linea):
        t = paso / total
        e = 1 - (1 - t) ** 3  # suavizado: arranca rápido y frena al final
        rel = d * (1 - e)
        nuevo.place_configure(relx=rel)
        viejo.place_configure(relx=-d * e)
        if linea is not None:
            if d > 0:
                linea.place(relx=rel, x=0, rely=0, relheight=1)
            else:
                linea.place(relx=rel + 1, x=-5, rely=0, relheight=1)
        if paso < total:
            self.after(MS_FOTOGRAMA, lambda: self._animar(viejo, nuevo, d, paso + 1, total, linea))
        else:
            if linea is not None:
                linea.destroy()
            viejo.destroy()
            nuevo.place_configure(relx=0)
            self.animando = False

    # ---------- widgets con el color del tema ----------
    def boton(self, master, text, comando, **kw):
        """Botón con el color del tema y destello breve al pulsarlo."""
        kw.setdefault("fg_color", self.tema["main"])
        kw.setdefault("hover_color", self.tema["hover"])
        b = ctk.CTkButton(master, text=text, **kw)
        b.configure(command=lambda: self.pulsar(b, comando))
        return b

    def pulsar(self, b, comando):
        if self.animando or self.pulsando:
            return
        if not self.nivel()["destello"]:
            comando()
            return
        self.pulsando = True
        original = b.cget("fg_color")
        b.configure(fg_color=self.tema["flash"])

        def seguir():
            self.pulsando = False
            try:
                b.configure(fg_color=original)
            except Exception:
                pass
            comando()
        self.after(110, seguir)

    def menu(self, master, valores, variable, ancho=260, comando=None):
        return ctk.CTkOptionMenu(master, values=list(valores), variable=variable, width=ancho,
                                 fg_color=self.tema["main"], button_color=self.tema["hover"],
                                 button_hover_color=self.tema["hover"], command=comando)

    def casilla(self, master, texto, variable):
        return ctk.CTkCheckBox(master, text=texto, variable=variable,
                               fg_color=self.tema["main"], hover_color=self.tema["hover"])

    def segmentado(self, master, valores, actual, comando):
        s = ctk.CTkSegmentedButton(master, values=list(valores), command=comando,
                                   selected_color=self.tema["main"], selected_hover_color=self.tema["hover"])
        s.set(actual)
        return s

    # ---------- helpers de diseño ----------
    def titulo(self, paso, texto, sub=None):
        ctk.CTkLabel(self.p, text=paso, text_color="gray").pack(pady=(14, 0))
        ctk.CTkLabel(self.p, text=texto, font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 2))
        if sub:
            ctk.CTkLabel(self.p, text=sub, text_color="gray", justify="center",
                         wraplength=ANCHO_COLUMNA - 40).pack(pady=(0, 6))

    def resumen_archivos(self):
        n = [p.name for p in self.archivos]
        txt = ", ".join(n[:3]) + (f" y {len(n) - 3} más" if len(n) > 3 else "")
        return f"{len(n)} archivo{'s' if len(n) > 1 else ''}: {txt}"

    def _fila(self, padre, etiqueta, crear):
        """Fila 'etiqueta + control' alineada. `crear` recibe la fila y devuelve el control."""
        f = ctk.CTkFrame(padre, fg_color="transparent")
        f.pack(fill="x", padx=16, pady=6)
        ctk.CTkLabel(f, text=etiqueta, width=130, anchor="w").pack(side="left")
        crear(f).pack(side="left")

    # ---------- configuración ----------
    def abrir_config(self):
        if self.en_config or self.animando or self.pulsando:
            return
        self.volver_a = self.pantalla_inicio if self.constructor_actual == self.pantalla_progreso \
            else self.constructor_actual
        self.ir(self.pantalla_config, 1)

    def set_ajuste(self, clave, valor):
        if self.animando or self.ajustes.get(clave) == valor:
            return
        self.ajustes[clave] = valor
        guardar_ajustes(self.ajustes)
        if clave == "apariencia":
            ctk.set_appearance_mode(APARIENCIAS[valor])
        self.ir(self.pantalla_config, 0)   # se redibuja al instante con el cambio aplicado

    def _tarjeta(self, titulo):
        card = ctk.CTkFrame(self.p)
        card.pack(fill="x", pady=6, ipady=4)
        ctk.CTkLabel(card, text=titulo, font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(fill="x", padx=16, pady=(10, 2))
        return card

    def pantalla_config(self):
        self.en_config = True
        a = self.ajustes
        ctk.CTkLabel(self.p, text="Configuración", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(14, 6))

        inferior = ctk.CTkFrame(self.p, fg_color="transparent")
        inferior.pack(side="bottom", fill="x", pady=6)
        self.boton(inferior, "← Volver", lambda: self.ir(self.volver_a or self.pantalla_inicio, -1),
                   width=120, fg_color="gray40", hover_color="gray30").pack(side="left")

        # Color y apariencia
        card = self._tarjeta(f"Color de la aplicación · {a['color']}")
        muestras = ctk.CTkFrame(card, fg_color="transparent")
        muestras.pack(fill="x", padx=12, pady=(2, 6))
        for nombre, pal in PALETAS.items():
            elegido = nombre == a["color"]
            ctk.CTkButton(muestras, text="", width=40, height=40, corner_radius=20,
                          fg_color=pal["main"], hover_color=pal["hover"],
                          border_width=4 if elegido else 0, border_color=("gray10", "gray95"),
                          command=lambda n=nombre: self.set_ajuste("color", n)).pack(side="left", padx=4)
        self._fila(card, "Apariencia", lambda f: self.segmentado(
            f, APARIENCIAS, a["apariencia"], lambda v: self.set_ajuste("apariencia", v)))

        # Transiciones
        card = self._tarjeta("Transiciones y animaciones")
        self._fila(card, "Nivel", lambda f: self.segmentado(
            f, TRANSICIONES, a["transiciones"], lambda v: self.set_ajuste("transiciones", v)))
        ctk.CTkLabel(card, text=self.nivel()["desc"], text_color="gray", anchor="w", justify="left",
                     wraplength=ANCHO_COLUMNA - 60).pack(fill="x", padx=16)
        self.boton(card, "▶ Probar transición", lambda: self.ir(self.pantalla_config, 1),
                   width=170, height=30).pack(anchor="w", padx=16, pady=(8, 4))

        # Nivel del PC
        card = self._tarjeta("Nivel de tu computador")
        var = ctk.StringVar(value=a["perfil"])
        self._fila(card, "Perfil", lambda f: self.menu(
            f, PERFILES, var, ancho=380, comando=lambda v: self.set_ajuste("perfil", v)))
        ctk.CTkLabel(card, text=self.perfil()["desc"], text_color="gray", anchor="w", justify="left",
                     wraplength=ANCHO_COLUMNA - 60).pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(card, text="Afecta a la transcripción (calidad sugerida, núcleos y precisión) "
                                "y a la compresión (núcleos).",
                     text_color="gray", font=ctk.CTkFont(size=11), anchor="w", justify="left",
                     wraplength=ANCHO_COLUMNA - 60).pack(fill="x", padx=16)

    # ---------- paso 1: subir archivo ----------
    def pantalla_inicio(self):
        self.titulo("Paso 1 de 3", "Elige tu archivo", "Puedes seleccionar uno o varios a la vez")
        self.boton(self.p, "📂\nElegir audio o video", self.elegir_archivos, height=200,
                   font=ctk.CTkFont(size=22, weight="bold"), corner_radius=18).pack(fill="x", pady=24)
        ctk.CTkLabel(self.p, text="MP3 · WAV · M4A · AAC · OGG · FLAC · MP4 · MKV y más",
                     text_color="gray").pack()

    def elegir_archivos(self):
        sel = filedialog.askopenfilenames(title="Elige tus audios", filetypes=TIPOS)
        if sel:
            self.archivos = [Path(f) for f in sel]
            self.ir(self.pantalla_accion, 1)

    # ---------- paso 2: qué hacer ----------
    def pantalla_accion(self):
        self.titulo("Paso 2 de 3", "¿Qué quieres hacer?", self.resumen_archivos())
        fila = ctk.CTkFrame(self.p, fg_color="transparent")
        fila.pack(fill="x", pady=20)
        fila.columnconfigure((0, 1), weight=1, uniform="a")
        estilo = dict(height=180, corner_radius=18, font=ctk.CTkFont(size=16, weight="bold"))
        self.boton(fila, "📝\nTranscribir\n\nConvierte el audio en texto",
                   lambda: self.ir(lambda: self.pantalla_opciones("transcribir"), 1), **estilo
                   ).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.boton(fila, "🗜️\nComprimir\n\nReduce el peso del archivo",
                   lambda: self.ir(lambda: self.pantalla_opciones("comprimir"), 1), **estilo
                   ).grid(row=0, column=1, padx=(8, 0), sticky="ew")
        self.boton(self.p, "← Cambiar archivo(s)", lambda: self.ir(self.pantalla_inicio, -1),
                   fg_color="transparent", text_color=("gray30", "gray70"),
                   hover_color=("gray85", "gray25")).pack()

    # ---------- paso 3: opciones ----------
    def pantalla_opciones(self, accion):
        self.accion = accion
        nombre = "Transcribir" if accion == "transcribir" else "Comprimir"
        self.titulo("Paso 3 de 3", f"Opciones para {nombre.lower()}", self.resumen_archivos())

        # Botones inferiores: se empaquetan primero para que siempre queden visibles.
        botones = ctk.CTkFrame(self.p, fg_color="transparent")
        botones.pack(side="bottom", fill="x", pady=6)
        self.boton(botones, "← Atrás", lambda: self.ir(self.pantalla_accion, -1), width=100,
                   fg_color="gray40", hover_color="gray30").pack(side="left")
        self.boton(botones, f"{nombre} ▶", self.comenzar, height=42,
                   font=ctk.CTkFont(size=15, weight="bold")).pack(side="right", fill="x", expand=True, padx=(12, 0))

        op = ctk.CTkFrame(self.p)
        op.pack(fill="x", pady=10, ipady=8)

        perfil_corto = self.ajustes["perfil"].split(" · ")[0]
        if accion == "transcribir":
            self.v_modelo = ctk.StringVar(value=self.perfil()["modelo"])
            self.v_idioma = ctk.StringVar(value="Español")
            self.v_ts, self.v_srt = ctk.BooleanVar(), ctk.BooleanVar()
            self._fila(op, "Calidad", lambda f: self.menu(f, MODELOS, self.v_modelo))
            self._fila(op, "Idioma", lambda f: self.menu(f, IDIOMAS, self.v_idioma))
            self.casilla(op, "Incluir marcas de tiempo", self.v_ts).pack(anchor="w", padx=16, pady=4)
            self.casilla(op, "Generar también subtítulos (.srt)", self.v_srt).pack(anchor="w", padx=16, pady=4)
            nota = (f"Perfil de PC: {perfil_corto} (se cambia en ⚙). La primera vez se descarga el "
                    "modelo (requiere internet); después funciona sin conexión.")
        else:
            self.v_preset = ctk.StringVar(value="Voz · equilibrado")
            self.v_fmt = ctk.StringVar(value="mp3")
            self._fila(op, "Tipo de audio", lambda f: self.menu(f, PRESETS, self.v_preset))
            self._fila(op, "Formato", lambda f: self.menu(f, CODECS, self.v_fmt))
            nota = "Para clases y entrevistas, 'Voz · equilibrado' suele ser lo mejor."

        ctk.CTkLabel(self.p, text=nota, text_color="gray", font=ctk.CTkFont(size=11),
                     wraplength=ANCHO_COLUMNA - 40).pack(pady=(0, 6))

        # carpeta de salida
        fila = ctk.CTkFrame(self.p, fg_color="transparent")
        fila.pack(fill="x", pady=(4, 0))
        self.boton(fila, "📁 Carpeta de salida", self.elegir_carpeta, width=160,
                   fg_color="gray40", hover_color="gray30").pack(side="left")
        self.lbl_carpeta = ctk.CTkLabel(fila, text=str(self.carpeta) if self.carpeta else "Junto al archivo original",
                                        text_color="gray", wraplength=ANCHO_COLUMNA - 200, justify="left")
        self.lbl_carpeta.pack(side="left", padx=10)

    def elegir_carpeta(self):
        d = filedialog.askdirectory(title="Carpeta de salida")
        if d:
            self.carpeta = Path(d)
            self.lbl_carpeta.configure(text=str(self.carpeta))

    # ---------- paso 4: progreso ----------
    def comenzar(self):
        pf = self.perfil()
        if self.accion == "transcribir":
            self.cfg = dict(modelo=MODELOS[self.v_modelo.get()], idioma=IDIOMAS[self.v_idioma.get()],
                            ts=self.v_ts.get(), srt=self.v_srt.get(),
                            gpu=pf["gpu"], threads=pf["threads"], beam=pf["beam"])
            trabajo = self.trabajo_transcribir
        else:
            br, mono = PRESETS[self.v_preset.get()]
            self.cfg = dict(fmt=self.v_fmt.get(), br=br, mono=mono, threads=pf["threads"])
            trabajo = self.trabajo_comprimir
        self.carpeta_resultado = self.carpeta or self.archivos[0].parent
        self.btn_cfg.configure(state="disabled")
        self.ir(self.pantalla_progreso, 1)

        def run():
            try:
                trabajo()
            except Exception as e:
                self.log(f"✗ Error: {e}")
            finally:
                self.cola.put(("fin", None))
        threading.Thread(target=run, daemon=True).start()

    def pantalla_progreso(self):
        self.titulo("Trabajando…", "Procesando tus archivos", "No cierres la ventana hasta que termine")
        self.barra = ctk.CTkProgressBar(self.p, mode="indeterminate", progress_color=self.tema["main"])
        self.barra.pack(fill="x", pady=10)
        self.barra.start()
        fila = ctk.CTkFrame(self.p, fg_color="transparent")
        fila.pack(side="bottom", fill="x", pady=(6, 0))
        self.btn_abrir = self.boton(fila, "📂 Abrir carpeta", self.abrir_carpeta, state="disabled")
        self.btn_abrir.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.btn_nuevo = self.boton(fila, "Procesar otro archivo", lambda: self.ir(self.pantalla_inicio, -1),
                                    state="disabled")
        self.btn_nuevo.pack(side="left", expand=True, fill="x", padx=(6, 0))
        self.log_box = ctk.CTkTextbox(self.p, state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=6)

    def log(self, msg):
        self.cola.put(("log", msg))   # seguro desde cualquier hilo

    def _sondear(self):
        """Corre en el hilo principal: pasa los mensajes de la cola a la pantalla."""
        try:
            while True:
                tipo, dato = self.cola.get_nowait()
                if tipo == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", dato + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif tipo == "fin":
                    self.terminado()
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(100, self._sondear)

    def terminado(self):
        self.barra.stop()
        self.barra.configure(mode="determinate")
        self.barra.set(1)
        self.btn_abrir.configure(state="normal")
        self.btn_nuevo.configure(state="normal")
        self.btn_cfg.configure(state="normal")
        self.log("— Listo —")

    def abrir_carpeta(self):
        ruta = str(self.carpeta_resultado)
        if sys.platform == "win32":
            os.startfile(ruta)
        elif sys.platform == "darwin":
            subprocess.run(["open", ruta])
        else:
            subprocess.run(["xdg-open", ruta])

    # ---------- trabajos ----------
    def _cargar_modelo(self, nombre, gpu, threads):
        from faster_whisper import WhisperModel
        clave = (nombre, gpu, threads)
        if self.modelo_clave == clave:
            return
        self.log(f"Cargando modelo '{nombre}' en {'GPU' if gpu else 'procesador'} "
                 "(si es la primera vez, se descarga; puede tardar)…")
        dev, tipo = ("cuda", "float16") if gpu else ("cpu", "int8")
        self.modelo = WhisperModel(nombre, device=dev, compute_type=tipo, cpu_threads=threads)
        self.modelo_clave = clave

    def _transcribir_archivo(self, a, c):
        self.log(f"→ Transcribiendo {a.name} …")
        segs, info = self.modelo.transcribe(str(a), language=c["idioma"], vad_filter=True, beam_size=c["beam"])
        self.log(f"  Idioma: {info.language} · duración: {info.duration / 60:.1f} min")
        txt, srt = [], []
        for i, s in enumerate(segs, 1):
            t = s.text.strip()
            txt.append(f"[{fmt_corto(s.start)}] {t}" if c["ts"] else t)
            srt.append(f"{i}\n{fmt_srt(s.start)} --> {fmt_srt(s.end)}\n{t}\n")
        carpeta = self.carpeta or a.parent
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{a.stem}.txt").write_text("\n".join(txt), encoding="utf-8")
        self.log(f"✓ Guardado: {carpeta / (a.stem + '.txt')}")
        if c["srt"]:
            (carpeta / f"{a.stem}.srt").write_text("\n".join(srt), encoding="utf-8")
            self.log(f"✓ Guardado: {carpeta / (a.stem + '.srt')}")

    def trabajo_transcribir(self):
        c = dict(self.cfg)
        try:
            self._cargar_modelo(c["modelo"], c["gpu"], c["threads"])
        except Exception:
            if not c["gpu"]:
                raise
            self.log("⚠ La tarjeta gráfica no está disponible. Usando el procesador.")
            c["gpu"] = False
            self.modelo_clave = None
            self._cargar_modelo(c["modelo"], False, c["threads"])

        for a in self.archivos:
            try:
                self._transcribir_archivo(a, c)
            except Exception as e:
                if not c["gpu"]:
                    self.log(f"✗ Error con {a.name}: {e}")
                    continue
                # Con GPU, el fallo suele aparecer recién al transcribir (faltan librerías CUDA).
                self.log("⚠ La tarjeta gráfica falló. Reintentando con el procesador…")
                c["gpu"] = False
                self.modelo_clave = None
                try:
                    self._cargar_modelo(c["modelo"], False, c["threads"])
                    self._transcribir_archivo(a, c)
                except Exception as e2:
                    self.log(f"✗ Error con {a.name}: {e2}")

    def trabajo_comprimir(self):
        ff = ffmpeg_exe()
        if not ff:
            raise RuntimeError("No se encontró ffmpeg.")
        c = self.cfg
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        for a in self.archivos:
            carpeta = self.carpeta or a.parent
            carpeta.mkdir(parents=True, exist_ok=True)
            salida = carpeta / f"{a.stem}_comprimido.{c['fmt']}"
            cmd = [ff, "-y", "-i", str(a), "-vn", *CODECS[c["fmt"]], "-b:a", c["br"]]
            if c["mono"]:
                cmd += ["-ac", "1"]
            if c["threads"]:
                cmd += ["-threads", str(c["threads"])]
            cmd.append(str(salida))
            self.log(f"→ Comprimiendo {a.name} …")
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            if r.returncode != 0:
                self.log(f"✗ Error con {a.name}: {r.stderr[-300:]}")
                continue
            antes, despues = a.stat().st_size / 1_048_576, salida.stat().st_size / 1_048_576
            self.log(f"✓ {antes:.2f} MB → {despues:.2f} MB ({(1 - despues / antes) * 100:.0f}% menos)")


if __name__ == "__main__":
    App().mainloop()
