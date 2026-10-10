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

T_TITULO = dict(size=24, weight="bold")
T_GRANDE = dict(size=18, weight="bold")


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
        self.geometry("720x660")
        self.minsize(640, 600)

        self.archivos: list[Path] = []
        self.accion = None
        self.carpeta: Path | None = None
        self.modelo = None
        self.modelo_clave = None

        ctk.CTkLabel(self, text="🎙️ Audio Studio", font=ctk.CTkFont(**T_TITULO)).pack(pady=(16, 0))
        self.c = ctk.CTkFrame(self, fg_color="transparent")
        self.c.pack(fill="both", expand=True, padx=28, pady=(6, 18))
        self.pantalla_inicio()

    # ---------- helpers ----------
    def limpiar(self):
        for w in self.c.winfo_children():
            w.destroy()

    def titulo(self, paso, texto, sub=None):
        ctk.CTkLabel(self.c, text=paso, text_color="gray").pack(pady=(14, 0))
        ctk.CTkLabel(self.c, text=texto, font=ctk.CTkFont(**T_GRANDE)).pack(pady=(0, 2))
        if sub:
            ctk.CTkLabel(self.c, text=sub, text_color="gray", justify="center").pack(pady=(0, 6))

    def resumen_archivos(self):
        n = [p.name for p in self.archivos]
        txt = ", ".join(n[:3]) + (f" y {len(n) - 3} más" if len(n) > 3 else "")
        return f"{len(n)} archivo{'s' if len(n) > 1 else ''}: {txt}"

    # ---------- paso 1: subir archivo ----------
    def pantalla_inicio(self):
        self.limpiar()
        self.titulo("Paso 1 de 3", "Elige tu archivo", "Puedes seleccionar uno o varios a la vez")
        ctk.CTkButton(self.c, text="📂\nElegir audio o video", height=200,
                      font=ctk.CTkFont(size=22, weight="bold"), corner_radius=18,
                      command=self.elegir_archivos).pack(fill="x", pady=24)
        ctk.CTkLabel(self.c, text="MP3 · WAV · M4A · AAC · OGG · FLAC · MP4 · MKV y más",
                     text_color="gray").pack()

    def elegir_archivos(self):
        sel = filedialog.askopenfilenames(title="Elige tus audios", filetypes=TIPOS)
        if sel:
            self.archivos = [Path(f) for f in sel]
            self.pantalla_accion()

    # ---------- paso 2: qué hacer ----------
    def pantalla_accion(self):
        self.limpiar()
        self.titulo("Paso 2 de 3", "¿Qué quieres hacer?", self.resumen_archivos())
        fila = ctk.CTkFrame(self.c, fg_color="transparent")
        fila.pack(fill="x", pady=20)
        fila.columnconfigure((0, 1), weight=1, uniform="a")
        ctk.CTkButton(fila, text="📝\nTranscribir\n\nConvierte el audio en texto",
                      height=180, corner_radius=18, font=ctk.CTkFont(size=16, weight="bold"),
                      command=lambda: self.pantalla_opciones("transcribir")
                      ).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        ctk.CTkButton(fila, text="🗜️\nComprimir\n\nReduce el peso del archivo",
                      height=180, corner_radius=18, font=ctk.CTkFont(size=16, weight="bold"),
                      command=lambda: self.pantalla_opciones("comprimir")
                      ).grid(row=0, column=1, padx=(8, 0), sticky="ew")
        ctk.CTkButton(self.c, text="← Cambiar archivo(s)", fg_color="transparent",
                      text_color=("gray30", "gray70"), hover_color=("gray85", "gray25"),
                      command=self.pantalla_inicio).pack()

    # ---------- paso 3: opciones ----------
    def pantalla_opciones(self, accion):
        self.accion = accion
        self.limpiar()
        nombre = "Transcribir" if accion == "transcribir" else "Comprimir"
        self.titulo("Paso 3 de 3", f"Opciones para {nombre.lower()}", self.resumen_archivos())

        op = ctk.CTkFrame(self.c)
        op.pack(fill="x", pady=10, ipadx=10, ipady=8)

        if accion == "transcribir":
            self.v_modelo = ctk.StringVar(value="Equilibrada (recomendada)")
            self.v_idioma = ctk.StringVar(value="Español")
            self.v_ts, self.v_srt, self.v_gpu = ctk.BooleanVar(), ctk.BooleanVar(), ctk.BooleanVar()
            self._fila(op, "Calidad", ctk.CTkOptionMenu(op, values=list(MODELOS), variable=self.v_modelo, width=240))
            self._fila(op, "Idioma", ctk.CTkOptionMenu(op, values=list(IDIOMAS), variable=self.v_idioma, width=240))
            ctk.CTkCheckBox(op, text="Incluir marcas de tiempo", variable=self.v_ts).pack(anchor="w", padx=16, pady=3)
            ctk.CTkCheckBox(op, text="Generar también subtítulos (.srt)", variable=self.v_srt).pack(anchor="w", padx=16, pady=3)
            ctk.CTkCheckBox(op, text="Usar tarjeta gráfica NVIDIA (si tienes una)", variable=self.v_gpu).pack(anchor="w", padx=16, pady=3)
            ctk.CTkLabel(self.c, text="La primera vez se descarga el modelo (requiere internet). Después funciona sin conexión.",
                         text_color="gray", font=ctk.CTkFont(size=11), wraplength=600).pack()
        else:
            self.v_preset = ctk.StringVar(value="Voz · equilibrado")
            self.v_fmt = ctk.StringVar(value="mp3")
            self._fila(op, "Tipo de audio", ctk.CTkOptionMenu(op, values=list(PRESETS), variable=self.v_preset, width=240))
            self._fila(op, "Formato", ctk.CTkOptionMenu(op, values=list(CODECS), variable=self.v_fmt, width=240))

        # carpeta de salida
        fila = ctk.CTkFrame(self.c, fg_color="transparent")
        fila.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(fila, text="📁 Carpeta de salida", width=160, fg_color="gray40",
                      hover_color="gray30", command=self.elegir_carpeta).pack(side="left")
        self.lbl_carpeta = ctk.CTkLabel(fila, text=str(self.carpeta) if self.carpeta else "Junto al archivo original",
                                        text_color="gray")
        self.lbl_carpeta.pack(side="left", padx=10)

        botones = ctk.CTkFrame(self.c, fg_color="transparent")
        botones.pack(side="bottom", fill="x", pady=6)
        ctk.CTkButton(botones, text="← Atrás", width=100, fg_color="gray40", hover_color="gray30",
                      command=self.pantalla_accion).pack(side="left")
        ctk.CTkButton(botones, text=f"{nombre} ▶", height=42, font=ctk.CTkFont(size=15, weight="bold"),
                      command=self.comenzar).pack(side="right", fill="x", expand=True, padx=(12, 0))

    def _fila(self, padre, etiqueta, widget):
        f = ctk.CTkFrame(padre, fg_color="transparent")
        f.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(f, text=etiqueta, width=110, anchor="w").pack(side="left")
        widget.pack(side="left")

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
        self.pantalla_progreso()

        def run():
            try:
                trabajo()
            except Exception as e:
                self.log(f"✗ Error: {e}")
            finally:
                self.after(0, self.terminado)
        threading.Thread(target=run, daemon=True).start()

    def pantalla_progreso(self):
        self.limpiar()
        self.titulo("Trabajando…", "Procesando tus archivos", "No cierres la ventana hasta que termine")
        self.barra = ctk.CTkProgressBar(self.c, mode="indeterminate")
        self.barra.pack(fill="x", pady=10)
        self.barra.start()
        self.log_box = ctk.CTkTextbox(self.c, state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=6)
        fila = ctk.CTkFrame(self.c, fg_color="transparent")
        fila.pack(fill="x", pady=(6, 0))
        self.btn_abrir = ctk.CTkButton(fila, text="📂 Abrir carpeta", state="disabled", command=self.abrir_carpeta)
        self.btn_abrir.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.btn_nuevo = ctk.CTkButton(fila, text="Procesar otro archivo", state="disabled",
                                       command=self.pantalla_inicio)
        self.btn_nuevo.pack(side="left", expand=True, fill="x", padx=(6, 0))

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
