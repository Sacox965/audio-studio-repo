#!/usr/bin/env python3
"""
Audio Studio: transcribe y comprime audios paso a paso.

Para probarla sin instalador:
    pip install customtkinter faster-whisper imageio-ffmpeg
    python audio_studio.py
"""
import os
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

ctk.set_appearance_mode("system")
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

ANCHO_COLUMNA = 640      # ancho fijo del contenido, centrado en la ventana
COLOR_PULSADO = "#6cb6f0"  # destello al pulsar un botón
PASOS_ANIM = 12          # fotogramas de la transición entre pantallas
MS_FOTOGRAMA = 14


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
        self.title("Audio Studio")
        self.geometry("760x700")
        self.minsize(700, 640)

        self.archivos: list[Path] = []
        self.accion = None
        self.carpeta: Path | None = None
        self.modelo = None
        self.modelo_clave = None
        self.animando = False
        self.pulsando = False
        self.p = None  # página (pantalla) actual

        ctk.CTkLabel(self, text="🎙️ Audio Studio",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(16, 0))

        # Columna central de ancho fijo: el contenido queda siempre alineado y centrado.
        self.c = ctk.CTkFrame(self, width=ANCHO_COLUMNA, fg_color="transparent")
        self.c.pack(fill="y", expand=True, pady=(6, 18))
        self.c.pack_propagate(False)

        self.ir(self.pantalla_inicio, 0)

    # ---------- navegación y animaciones ----------
    def ir(self, constructor, direccion=1):
        """Construye una pantalla nueva y la desliza. direccion: 1 = avanzar, -1 = volver, 0 = sin animar."""
        viejo = self.p
        nuevo = ctk.CTkFrame(self.c, fg_color="transparent")
        self.p = nuevo
        constructor()
        if viejo is None or direccion == 0:
            nuevo.place(relx=0, rely=0, relwidth=1, relheight=1)
            if viejo is not None:
                viejo.destroy()
            return
        self.animando = True
        nuevo.place(relx=direccion, rely=0, relwidth=1, relheight=1)
        self._animar(viejo, nuevo, direccion, 0)

    def _animar(self, viejo, nuevo, d, paso):
        t = paso / PASOS_ANIM
        e = 1 - (1 - t) ** 3  # suavizado: arranca rápido y frena al final
        nuevo.place_configure(relx=d * (1 - e))
        viejo.place_configure(relx=-d * e)
        if paso < PASOS_ANIM:
            self.after(MS_FOTOGRAMA, lambda: self._animar(viejo, nuevo, d, paso + 1))
        else:
            viejo.destroy()
            nuevo.place_configure(relx=0)
            self.animando = False

    def boton(self, master, text, comando, **kw):
        """Botón con destello breve al pulsarlo antes de ejecutar su acción."""
        b = ctk.CTkButton(master, text=text, **kw)
        b.configure(command=lambda: self.pulsar(b, comando))
        return b

    def pulsar(self, b, comando):
        if self.animando or self.pulsando:
            return
        self.pulsando = True
        original = b.cget("fg_color")
        b.configure(fg_color=COLOR_PULSADO)

        def seguir():
            self.pulsando = False
            try:
                b.configure(fg_color=original)
            except Exception:
                pass
            comando()
        self.after(110, seguir)

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

        if accion == "transcribir":
            self.v_modelo = ctk.StringVar(value="Equilibrada (recomendada)")
            self.v_idioma = ctk.StringVar(value="Español")
            self.v_ts, self.v_srt, self.v_gpu = ctk.BooleanVar(), ctk.BooleanVar(), ctk.BooleanVar()
            self._fila(op, "Calidad", lambda f: ctk.CTkOptionMenu(
                f, values=list(MODELOS), variable=self.v_modelo, width=260))
            self._fila(op, "Idioma", lambda f: ctk.CTkOptionMenu(
                f, values=list(IDIOMAS), variable=self.v_idioma, width=260))
            for texto, var in (("Incluir marcas de tiempo", self.v_ts),
                               ("Generar también subtítulos (.srt)", self.v_srt),
                               ("Usar tarjeta gráfica NVIDIA (si tienes una)", self.v_gpu)):
                ctk.CTkCheckBox(op, text=texto, variable=var).pack(anchor="w", padx=16, pady=4)
            nota = ("La primera vez se descarga el modelo (requiere internet). "
                    "Después funciona sin conexión.")
        else:
            self.v_preset = ctk.StringVar(value="Voz · equilibrado")
            self.v_fmt = ctk.StringVar(value="mp3")
            self._fila(op, "Tipo de audio", lambda f: ctk.CTkOptionMenu(
                f, values=list(PRESETS), variable=self.v_preset, width=260))
            self._fila(op, "Formato", lambda f: ctk.CTkOptionMenu(
                f, values=list(CODECS), variable=self.v_fmt, width=260))
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
        if self.accion == "transcribir":
            self.cfg = dict(modelo=MODELOS[self.v_modelo.get()], idioma=IDIOMAS[self.v_idioma.get()],
                            ts=self.v_ts.get(), srt=self.v_srt.get(), gpu=self.v_gpu.get())
            trabajo = self.trabajo_transcribir
        else:
            br, mono = PRESETS[self.v_preset.get()]
            self.cfg = dict(fmt=self.v_fmt.get(), br=br, mono=mono)
            trabajo = self.trabajo_comprimir
        self.carpeta_resultado = self.carpeta or self.archivos[0].parent
        self.ir(self.pantalla_progreso, 1)

        def run():
            try:
                trabajo()
            except Exception as e:
                self.log(f"✗ Error: {e}")
            finally:
                self.after(0, self.terminado)
        threading.Thread(target=run, daemon=True).start()

    def pantalla_progreso(self):
        self.titulo("Trabajando…", "Procesando tus archivos", "No cierres la ventana hasta que termine")
        self.barra = ctk.CTkProgressBar(self.p, mode="indeterminate")
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
        def _w():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _w)

    def terminado(self):
        self.barra.stop()
        self.barra.configure(mode="determinate")
        self.barra.set(1)
        self.btn_abrir.configure(state="normal")
        self.btn_nuevo.configure(state="normal")
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
    def trabajo_transcribir(self):
        from faster_whisper import WhisperModel
        c = self.cfg
        clave = (c["modelo"], c["gpu"])
        if self.modelo_clave != clave:
            self.log(f"Cargando modelo '{c['modelo']}' (si es la primera vez, se descarga; puede tardar)…")
            dev, tipo = ("cuda", "float16") if c["gpu"] else ("cpu", "int8")
            self.modelo = WhisperModel(c["modelo"], device=dev, compute_type=tipo)
            self.modelo_clave = clave

        for a in self.archivos:
            self.log(f"→ Transcribiendo {a.name} …")
            segs, info = self.modelo.transcribe(str(a), language=c["idioma"], vad_filter=True, beam_size=5)
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
