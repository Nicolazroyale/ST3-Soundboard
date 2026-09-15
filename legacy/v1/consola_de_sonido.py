"""
CONSOLA OBS
============
Panel de control para OBS Studio vía OBS WebSocket (v5), con estética de
mixer y botonera de sonidos real: medidores de nivel tipo LED, botones
circulares iluminados, pads de soundboard tipo launchpad, tamaño de
íconos ajustable y paneles que se pueden reacomodar (arriba/abajo,
izquierda/derecha) a gusto del usuario.

Requisitos:
    pip install obsws-python
    pip install pillow      (opcional, mejora las miniaturas del soundboard)

Para convertirlo en ejecutable (Windows):
    pip install pyinstaller
    pyinstaller --onefile --windowed --name ConsolaOBS consola_obs.py

El .exe queda en la carpeta "dist".
"""

import os
import sys
import json
import math
import time
import shutil
import struct
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from tkinter import font as tkfont

import obsws_python as obs

# ------------------------------------------------------------------
# CONGELADO DE PINTADO DE VENTANA (sólo Windows)
# ------------------------------------------------------------------
# El parpadeo al redimensionar en Windows no se origina (sólo) en
# nuestro código: Windows repinta el fondo de la ventana en cada
# micro-cambio de tamaño ANTES de que Tk llegue a dibujar nada encima,
# y ESE destello es el que se ve como parpadeo, sin importar qué tan
# liviana sea nuestra reconstrucción. La forma correcta de eliminarlo
# es pedirle al sistema operativo, con el mensaje nativo WM_SETREDRAW,
# que deje de repintar la ventana durante el arrastre, y recién forzar
# UN solo repintado limpio (con RedrawWindow) cuando el usuario suelta
# y la interfaz ya está reconstruida. Mientras el repintado está
# apagado, la pantalla se queda mostrando tal cual el último cuadro
# bueno -ni un rectángulo tapando, ni una animación: literalmente lo
# que ya había- hasta que se reactiva.
_ES_WINDOWS = sys.platform.startswith("win")

# ------------------------------------------------------------------
# NITIDEZ REAL (DPI): por qué antes se veía "pixelado"
# ------------------------------------------------------------------
# En Windows, si un programa no declara que entiende de DPI, el sistema
# lo dibuja a la resolución vieja (96 ppp) y después ESTIRA esa imagen
# hasta el tamaño real de la pantalla. En un monitor 1080p con escalado
# al 125/150% -que es como viene configurado casi cualquier equipo hoy-
# eso significa que cada pixel que dibuja Tk se agranda y se interpola:
# bordes con escalones, texto borroso, curvas dentadas. No importa qué
# tan lindo se dibuje adentro: lo que se ve es una foto agrandada.
#
# Declarando "per-monitor v2" ANTES de crear la ventana, Windows nos
# entrega el lienzo a la resolución nativa completa y cada línea que
# dibujamos cae en un pixel físico de verdad. Eso, más el render con
# Pillow de pads y botones (supersampling + LANCZOS), es lo que hace
# que la interfaz se vea "1080" y no ampliada.
if _ES_WINDOWS:
    try:
        import ctypes as _ctypes_dpi
        try:
            # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win 10+)
            _ctypes_dpi.windll.user32.SetProcessDpiAwarenessContext(_ctypes_dpi.c_void_p(-4))
        except Exception:
            try:
                _ctypes_dpi.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                _ctypes_dpi.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

if _ES_WINDOWS:
    import ctypes

    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    _GA_ROOT = 2
    _WM_SETREDRAW = 0x000B
    _RDW_INVALIDATE = 0x0001
    _RDW_ERASE = 0x0004
    _RDW_ALLCHILDREN = 0x0080
    _RDW_UPDATENOW = 0x0100
    _GCLP_HBRBACKGROUND = -10

    # Los handles de Windows (HWND, HBRUSH) son del tamaño de un
    # puntero: en Windows de 64 bits eso es 8 bytes. Si no se le avisa
    # a ctypes, asume que estas funciones devuelven un entero de 32
    # bits (el tipo por defecto) y puede llegar a truncar el valor real
    # -handles grandes que dejan de coincidir con la ventana de
    # verdad-, haciendo que estas llamadas fallen en silencio o, peor,
    # actúen sobre la ventana equivocada. Declarar los tipos correctos
    # (c_void_p) es lo que evita ese problema en 64 bits.
    _user32.GetAncestor.restype = ctypes.c_void_p
    _user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _user32.SendMessageW.restype = ctypes.c_void_p
    _user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    _user32.RedrawWindow.restype = ctypes.c_int
    _user32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    _user32.SetClassLongPtrW.restype = ctypes.c_void_p
    _user32.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    _gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    _gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint]
    _gdi32.AddFontResourceExW.restype = ctypes.c_int
    _gdi32.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p]

    def _hwnd_ventana_real():
        """winfo_id() en Tk devuelve el HWND del widget, que en el caso
        de la ventana principal ya suele ser el HWND de nivel superior
        de verdad; GetAncestor(..., GA_ROOT) sube por la jerarquía hasta
        ese HWND raíz de todos modos, por las dudas, para asegurarnos
        de mandarle los mensajes de pintado a la ventana que Windows
        administra (título, bordes) y no a un widget interno."""
        try:
            return _user32.GetAncestor(ventana.winfo_id(), _GA_ROOT)
        except Exception:
            return None

    def _fijar_color_fondo_nativo(hex_color):
        """Reemplaza el PINCEL DE FONDO de la clase de ventana (lo que
        Windows usa, por su cuenta, para 'borrar' cualquier franja nueva
        que se destapa al agrandar la ventana -ver WM_ERASEBKGND-) por
        uno del mismo color oscuro que el resto de la interfaz.

        Este es el verdadero origen del parpadeo/flash al redimensionar
        en Windows con temas oscuros: el pincel de fondo por defecto de
        la clase de ventana que usa Tk es más claro (blanco/gris), así
        que Windows pinta esa franja nueva con ESE color un instante
        antes de que Tk llegue a dibujar encima con el color correcto.
        Frenar el repintado (ver _congelar_pintado_ventana) no alcanza
        para esto, porque no cambia qué color usaría Windows para esa
        franja la próxima vez que sí repinte: hay que cambiar el pincel
        en sí, una sola vez, para que hasta el propio borrado del
        sistema operativo ya sea del color correcto."""
        hwnd = _hwnd_ventana_real()
        if not hwnd:
            return
        try:
            hex_color = hex_color.lstrip("#")
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
            colorref = r | (g << 8) | (b << 16)  # Windows usa 0x00BBGGRR
            pincel = _gdi32.CreateSolidBrush(colorref)
            if pincel:
                _user32.SetClassLongPtrW(hwnd, _GCLP_HBRBACKGROUND, pincel)
        except Exception:
            pass

    def _congelar_pintado_ventana():
        hwnd = _hwnd_ventana_real()
        if hwnd:
            try:
                _user32.SendMessageW(hwnd, _WM_SETREDRAW, 0, 0)
            except Exception:
                pass

    def _descongelar_pintado_ventana():
        hwnd = _hwnd_ventana_real()
        if hwnd:
            try:
                _user32.SendMessageW(hwnd, _WM_SETREDRAW, 1, 0)
                _user32.RedrawWindow(
                    hwnd, None, None,
                    _RDW_INVALIDATE | _RDW_ERASE | _RDW_ALLCHILDREN | _RDW_UPDATENOW
                )
            except Exception:
                pass

    _FR_PRIVATE = 0x10  # la fuente sólo queda disponible para ESTE
    # proceso (no se "instala" en Windows ni queda visible para otros
    # programas) y Windows la desregistra solo cuando el programa
    # termina, así que no hace falta ni querido hacer limpieza manual.

    def _registrar_fuente_ttf_windows(ruta):
        try:
            return _gdi32.AddFontResourceExW(ruta, _FR_PRIVATE, None) > 0
        except Exception:
            return False
else:
    def _congelar_pintado_ventana():
        pass

    def _descongelar_pintado_ventana():
        pass

    def _fijar_color_fondo_nativo(hex_color):
        pass

    def _registrar_fuente_ttf_windows(ruta):
        return False

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk, ImageFile, ImageFilter, ImageChops
    HAY_PILLOW = True
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    HAY_PILLOW = False
    try:
        import subprocess
        import sys as _sys
        subprocess.check_call(
            [_sys.executable, "-m", "pip", "install", "--quiet", "Pillow"]
        )
        from PIL import Image, ImageDraw, ImageOps, ImageTk, ImageFile, ImageFilter, ImageChops
        HAY_PILLOW = True
        Image.MAX_IMAGE_PIXELS = None
        ImageFile.LOAD_TRUNCATED_IMAGES = True
    except Exception:
        HAY_PILLOW = False

# ImageGrab es un submódulo aparte de Pillow (y en Linux depende además
# de que el sistema tenga soporte de captura de pantalla, por ejemplo
# python3-xlib): puede faltar aunque Pillow sí esté instalado. Se
# importa por separado y con su propio try/except para que, si no está
# disponible, el programa siga funcionando igual, sólo que sin la foto
# congelada de la ventana durante el redimensionado (ver
# _capturar_snapshot_ventana más abajo).
try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None



if getattr(sys, "frozen", False):
    # Empaquetado con PyInstaller (--onefile o --onedir): __file__ acá
    # apunta a la carpeta temporal donde Windows descomprime el .exe
    # al abrirlo (algo tipo AppData\Local\Temp\_MEIxxxxx), no a donde
    # está el .exe de verdad. Guardar la config ahí, o buscar la
    # carpeta "assets" ahí, no serviría de nada: esa carpeta temporal
    # se borra apenas se cierra el programa. sys.executable, en
    # cambio, sí apunta siempre al .exe real, esté donde esté.
    CARPETA_SCRIPT = os.path.dirname(os.path.abspath(sys.executable))
else:
    CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_CONEXION = os.path.join(CARPETA_SCRIPT, "config_conexion.json")
ARCHIVO_SOUNDBOARD = os.path.join(CARPETA_SCRIPT, "config_soundboard.json")
ARCHIVO_INTERFAZ = os.path.join(CARPETA_SCRIPT, "config_interfaz.json")

CARPETA_ASSETS = os.path.join(CARPETA_SCRIPT, "assets")
CARPETA_ICONOS = os.path.join(CARPETA_ASSETS, "iconos")
CARPETA_FONDOS = os.path.join(CARPETA_ASSETS, "fondos")
CARPETA_FUENTES_TIPOGRAFIA = os.path.join(CARPETA_ASSETS, "fuentes")

for _carpeta in (CARPETA_ASSETS, CARPETA_ICONOS, CARPETA_FONDOS, CARPETA_FUENTES_TIPOGRAFIA):
    try:
        os.makedirs(_carpeta, exist_ok=True)
    except Exception:
        pass


def _escribir_readme_assets(ruta, contenido):
    """Deja una guía corta dentro de cada carpeta de recursos, sólo la
    primera vez (si el archivo ya existe, no lo pisa), explicando qué
    archivos reconoce el programa ahí."""
    if os.path.exists(ruta):
        return
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido)
    except Exception:
        pass


_escribir_readme_assets(
    os.path.join(CARPETA_ICONOS, "LEEME.txt"),
    "ICONOS DE LA INTERFAZ\n"
    "======================\n"
    "Poné acá:\n"
    "  - app_icon.ico   -> ícono de la ventana/barra de tareas (Windows).\n"
    "  - app_icon.png   -> ícono de la ventana en Mac/Linux.\n"
    "  - logo_cabecera.png -> reemplaza el ícono dibujado de la cabecera.\n"
    "Ninguno es obligatorio: si faltan, el programa usa sus íconos\n"
    "dibujados por defecto y funciona exactamente igual.\n"
)
_escribir_readme_assets(
    os.path.join(CARPETA_FONDOS, "LEEME.txt"),
    "FONDOS DE LA INTERFAZ\n"
    "======================\n"
    "Carpeta reservada para futuras imágenes de fondo (por ejemplo,\n"
    "texturas o wallpapers propios). No es necesaria para que el\n"
    "programa funcione: por defecto se usan degradados dibujados por\n"
    "código.\n"
)
_escribir_readme_assets(
    os.path.join(CARPETA_FUENTES_TIPOGRAFIA, "LEEME.txt"),
    "TIPOGRAFÍA DE LA INTERFAZ\n"
    "==========================\n"
    "Poné acá cualquier archivo .ttf u .otf para personalizar la\n"
    "tipografía del programa. Por defecto se usa una tipografía moderna\n"
    "del sistema (Segoe UI / Helvetica Neue / Ubuntu, etc.), igual que\n"
    "cualquier programa de audio profesional; si querés que TODO el\n"
    "texto use la tuya en vez de la del sistema, se tiene en cuenta\n"
    "como alternativa. Si hay más de un archivo, se usa el primero (por\n"
    "orden alfabético) que el sistema operativo logre registrar; si no\n"
    "hay ninguno o falla el registro, el programa vuelve solo a elegir\n"
    "la mejor fuente instalada, sin romperse.\n"
)


# ------------------------------------------------------------------
# CARGA DE TIPOGRAFÍA PROPIA (.ttf / .otf) DESDE assets/fuentes
# ------------------------------------------------------------------
# Tk no sabe cargar un archivo de fuente por su cuenta: sólo puede usar
# fuentes que el sistema operativo ya tiene registradas. Así que para
# poder pedirle a Tk una tipografía que vino en un .ttf/.otf propio,
# primero hay que registrársela al sistema operativo -sólo para este
# proceso, sin "instalarla" de forma permanente en la máquina del
# usuario- y recién ahí Tk la puede ver y usar como una familia más.
# Cada sistema operativo lo hace distinto (ver las tres funciones de
# registro más abajo); si algo falla en el camino -el archivo está
# corrupto, el sistema no da permiso, etc.- simplemente no se agrega
# ninguna fuente propia y el programa sigue funcionando con la mejor
# fuente del sistema, como hacía antes.
_NOMBRES_FUENTES_PERSONALIZADAS = []  # familias registradas con éxito, en orden


def _nombre_familia_ttf(ruta):
    """Lee el nombre de familia tipográfica desde adentro del propio
    archivo (tabla 'name', registro 16 -nombre tipográfico preferido-
    o, si no está, el 1 -nombre de familia clásico-), para no depender
    de que el nombre del ARCHIVO coincida con el nombre interno real
    de la fuente (a veces no coinciden)."""
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
        num_tablas = struct.unpack(">H", datos[4:6])[0]
        offset_name = None
        for i in range(num_tablas):
            entrada = datos[12 + i * 16: 12 + i * 16 + 16]
            if entrada[0:4] == b"name":
                offset_name = struct.unpack(">I", entrada[8:12])[0]
                break
        if offset_name is None:
            return None
        _formato, cuenta, offset_cadenas = struct.unpack(
            ">HHH", datos[offset_name:offset_name + 6]
        )
        base_cadenas = offset_name + offset_cadenas
        candidatos = {}
        for i in range(cuenta):
            base_reg = offset_name + 6 + i * 12
            plataforma_id, _cod_id, _idioma_id, nombre_id, largo, offset = struct.unpack(
                ">HHHHHH", datos[base_reg:base_reg + 12]
            )
            if nombre_id not in (1, 16):
                continue
            crudo = datos[base_cadenas + offset: base_cadenas + offset + largo]
            try:
                texto = crudo.decode("utf-16-be") if plataforma_id in (0, 3) else crudo.decode("latin-1")
            except Exception:
                continue
            texto = texto.strip()
            if texto:
                candidatos[nombre_id] = texto
        return candidatos.get(16) or candidatos.get(1)
    except Exception:
        # Archivo corrupto, con una tabla 'name' rara, etc.: no hay
        # forma segura de saber el nombre, así que mejor no arriesgar
        # a registrar una fuente con un nombre inventado.
        return None


def _registrar_fuente_ttf_macos(ruta):
    try:
        core_text = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreText.framework/CoreText"
        )
        core_foundation = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        core_foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        ruta_bytes = os.fsencode(ruta)
        url = core_foundation.CFURLCreateFromFileSystemRepresentation(
            None, ruta_bytes, len(ruta_bytes), False
        )
        if not url:
            return False
        # kCTFontManagerScopeProcess = 1: registrada sólo para este
        # proceso, nada queda instalado de forma permanente en el Mac
        # del usuario.
        return bool(core_text.CTFontManagerRegisterFontsForURL(url, 1, None))
    except Exception:
        return False


def _registrar_fuente_ttf_linux(ruta):
    """En Linux (fontconfig) no existe un registro "sólo para este
    proceso" tan directo como en Windows/Mac, así que la forma
    práctica y estándar es copiar el archivo a la carpeta de fuentes
    del usuario (~/.local/share/fonts, no requiere permisos de
    administrador) y refrescar el caché con fc-cache. Si algo de esto
    falla -sin fc-cache instalado, sin permisos, etc.- no se registra
    nada y el programa sigue con la fuente del sistema."""
    try:
        carpeta_usuario = os.path.join(os.path.expanduser("~"), ".local", "share", "fonts")
        os.makedirs(carpeta_usuario, exist_ok=True)
        destino = os.path.join(carpeta_usuario, os.path.basename(ruta))
        if not os.path.exists(destino):
            shutil.copy2(ruta, destino)
        subprocess.run(["fc-cache", "-f", carpeta_usuario], capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _registrar_fuentes_personalizadas():
    if not os.path.isdir(CARPETA_FUENTES_TIPOGRAFIA):
        return
    for archivo in sorted(os.listdir(CARPETA_FUENTES_TIPOGRAFIA)):
        if not archivo.lower().endswith((".ttf", ".otf")):
            continue
        ruta = os.path.join(CARPETA_FUENTES_TIPOGRAFIA, archivo)
        if _ES_WINDOWS:
            registrada = _registrar_fuente_ttf_windows(ruta)
        elif sys.platform == "darwin":
            registrada = _registrar_fuente_ttf_macos(ruta)
        else:
            registrada = _registrar_fuente_ttf_linux(ruta)
        if not registrada:
            continue
        nombre_familia = _nombre_familia_ttf(ruta)
        if nombre_familia and nombre_familia not in _NOMBRES_FUENTES_PERSONALIZADAS:
            _NOMBRES_FUENTES_PERSONALIZADAS.append(nombre_familia)


_registrar_fuentes_personalizadas()

TAMANOS_ICONO = {
    "Chico": {
        "diametro_boton": 22, "fuente_boton": 10,
        "pad_ancho": 72, "pad_alto": 72,
        "diametro_pad_chico": 12, "fuente_pad_icono": 7,
        "fuente_pad_texto": 6, "fuente_nombre": 10,
        "fuente_ancho": 70, "fuente_alto": 300,
        "fuente_alto_canal": 120, "fuente_ancho_vu": 10,
    },
    "Mediano": {
        "diametro_boton": 28, "fuente_boton": 12,
        "pad_ancho": 105, "pad_alto": 105,
        "diametro_pad_chico": 16, "fuente_pad_icono": 8,
        "fuente_pad_texto": 6, "fuente_nombre": 12,
        "fuente_ancho": 100, "fuente_alto": 340,
        "fuente_alto_canal": 175, "fuente_ancho_vu": 15,
    },
    "Grande": {
        "diametro_boton": 36, "fuente_boton": 14,
        "pad_ancho": 138, "pad_alto": 138,
        "diametro_pad_chico": 21, "fuente_pad_icono": 9,
        "fuente_pad_texto": 7, "fuente_nombre": 14,
        "fuente_ancho": 132, "fuente_alto": 434,
        "fuente_alto_canal": 230, "fuente_ancho_vu": 20,
    },
}
TAMANO_ICONO_POR_DEFECTO = "Mediano"

ANCHO_VENTANA_REFERENCIA = 1300
ALTO_VENTANA_REFERENCIA = 760
ESCALA_MINIMA = 0.40
ESCALA_MAXIMA = 1.50
ESCALA_BASE = 0.78

DISENOS = {
    "Fuentes arriba":    ("vertical",   ["fuentes", "soundboard"]),
    "Fuentes abajo":     ("vertical",   ["soundboard", "fuentes"]),
    "Fuentes izquierda": ("horizontal", ["fuentes", "soundboard"]),
    "Fuentes derecha":   ("horizontal", ["soundboard", "fuentes"]),
}

NOMBRE_FUENTE_EFECTOS = "Soundboard_Efectos"
ETIQUETA_FUENTE_EFECTOS = "Efectos De Sonido"
NUM_BOTONES_SOUNDBOARD_INICIAL = 12
TAMANO_MINIATURA = (150, 70)
UMBRAL_SILENCIO = -60.0

# ============================================================
# SECCIÓN DE CALIBRACIÓN DEL MEDIDOR DE VOLUMEN
# ============================================================
# Estos son TODOS los parámetros que controlan cómo se ve y se
# comporta el medidor de nivel (el de las lucecitas de colores), en un
# solo lugar para poder tocarlos sin andar buscando por todo el
# archivo. Nota importante: como el medidor ahora usa DIRECTAMENTE el
# nivel que OBS reporta para cada fuente (ver niveles_actuales /
# actualizar_vu_meters_ui, más abajo en el archivo) en vez de
# reconstruirlo a mano, NO hay (ni hace falta) un número de "offset en
# dB" para que coincida con OBS: coincide porque es el mismo dato. Lo
# que sí se puede ajustar acá es CUÁNDO se considera que algo está
# saturando, CUÁNTO se sostiene el aviso, y qué tan sensible es el
# medidor a huecos cortos en los datos.

# A partir de qué nivel (en múltiplo lineal, donde 1.0 = 0 dB) se
# considera que la fuente está saturando. OBS manda el nivel de audio
# como multiplicador lineal y puede llegar a superar 1.0 cuando la
# señal excede los 0 dB (clipping real); 0.999 en vez de 1.0 exacto es
# sólo para no perder el aviso por el redondeo normal de punto
# flotante. No conviene tocarlo salvo que quieras que el aviso de
# saturación sea más/menos estricto que el de OBS.
UMBRAL_SATURACION_MUL = 0.999

# Cuánto tiempo (en segundos) se queda el medidor todo en rojo después
# de detectar una saturación, aunque el pico haya durado un instante.
# Sin este "sostenido" un clip de un solo cuadro sería casi invisible;
# con demasiado tiempo, en cambio, el aviso deja de sentirse ligado al
# momento exacto en que pasó, y con audio muy denso (picos seguidos,
# más frecuentes que este tiempo) puede dar la sensación de que el
# medidor queda "pegado" en rojo sin bajar nunca. Igual de criterio
# que el indicador de clipping de OBS. Si sentís que se sostiene
# demasiado, bajalo; si sentís que los clips cortos casi no se notan,
# subilo.
DURACION_SATURACION_SEG = 1.0

# Cuánto tiempo (en segundos) tiene que pasar SIN que una fuente mande
# ningún nivel de audio para que asumamos que de verdad dejó de sonar
# (se sacó la fuente, se cerró la app capturada, etc.) y mostremos
# silencio. Fuentes como "Captura de ventana" pierden y reenganchan su
# hook de captura seguido -sobre todo al minimizar/restaurar la
# ventana capturada- y en ese momento dejan de mandar niveles durante
# una fracción de segundo; con un umbral chico, esos huecos cortos y
# normales hacen que el medidor caiga a silencio de golpe y después
# salte para arriba en cuanto vuelve el dato, lo que se ve como un
# medidor errático sin que el audio real haya cambiado. Si notás que
# el medidor tarda de más en apagarse cuando de verdad se cortó el
# audio, bajalo un poco; si lo seguís viendo parpadear con fuentes que
# pierden el hook seguido, subilo.
UMBRAL_DATOS_VIEJOS_SEG = 1.5

# Por debajo de qué nivel (múltiplo lineal) se considera que
# 'niveles_actuales' (el mismo dato que usa la fuente SIN mutear) está
# realmente en silencio para una fuente muteada, y por lo tanto hay que
# reconstruir el nivel a mano con 'niveles_crudos' (ver 'elif muted' en
# actualizar_vu_meters_ui). Motivo: con la mayoría de las fuentes,
# mutear SÍ corta 'niveles_actuales' a 0 de verdad, así que ahí la
# reconstrucción manual es necesaria; pero hay casos puntuales donde
# OBS sigue reportando ahí el nivel real y en vivo aun estando muteada,
# y ahí conviene usar ese dato directamente en vez de reconstruirlo:
# es el valor exacto que ya calculó OBS, sin ninguna aproximación de
# acá. 0.0005 (~-66 dB) es lo
# bastante chico para no confundir ruido de piso/redondeo con audio
# real, pero deja pasar cualquier nivel que se pueda considerar "vivo".
UMBRAL_NIVEL_MUTEADO_DIRECTO = 0.0005

# Cada cuánto (en milisegundos) se refresca el medidor en pantalla.
# Cuanto más bajo, más fluido se ve, pero más trabajo le exige a la
# interfaz; 33ms es aproximadamente 30 cuadros por segundo.
INTERVALO_VU_MS = 33

# Velocidad a la que "cae" la aguja/LED del medidor cuando el nivel
# baja (subir siempre es instantáneo, como en un medidor de pico
# real; sólo la bajada se anima). A 70 dB/seg recorre el rango
# completo (0 a -60 dB) en menos de 1 segundo, parecido a un medidor
# de consola real; bajalo para que la caída se vea más lenta/suave,
# subilo para que sea más brusca/inmediata.
CAIDA_DB_POR_SEG = 70
CAIDA_POR_CUADRO = CAIDA_DB_POR_SEG * (INTERVALO_VU_MS / 1000.0)

# Ajuste manual (en dB) para el nivel RECONSTRUIDO que se muestra
# mientras una fuente está MUTEADA (ver 'elif muted' en
# actualizar_vu_meters_ui, más abajo). Ese nivel se arma multiplicando
# el dato "de entrada" que manda OBS por la ganancia del fader y la de
# los filtros, y ese cálculo puede quedar corrido unos cuantos dB del
# nivel real por cosas que este programa no puede leer de OBS (por
# ejemplo, la ganancia de hardware del dispositivo de audio, o
# filtros/etapas que no están en CAMPOS_GANANCIA_FILTRO). En vez de
# seguir adivinando, este número lo compensa a mano:
#   - Si la barra muteada queda MÁS ALTA que la activa -sube de más-,
#     bajá este número (poné un valor más negativo).
#   - Si la barra muteada queda MÁS BAJA que la activa -sube de
#     menos-, subí este número (poné un valor más positivo).
# Probalo muteando y desmuteando la MISMA fuente con el MISMO audio
# sonando, comparando dónde queda la barra en cada caso, y ajustá de a
# poco (2 o 3 dB por vez) hasta que las dos coincidan.
CALIBRACION_VU_MUTEADO_DB = 0.0

# Diagnóstico (opcional): imprime en la consola, para UNA fuente
# puntual, el nivel real que reporta OBS y si hubo algún hueco de
# datos. Poné acá el nombre EXACTO de la fuente tal como aparece en el
# panel (por ejemplo "Captura de ventana") para activarlo; dejalo en
# None para no imprimir nada (así queda por defecto). Sirve para
# comparar con certeza, mirando los números en la consola en vez de a
# ojo entre capturas de pantalla, si en algún momento el medidor
# vuelve a parecer raro.
DEBUG_VU_FUENTE = None
INTERVALO_DEBUG_VU_SEG = 0.25
# lo que trae "de fábrica" (y que por lo tanto el medidor tiene que
# tener en cuenta): para cada "kind" de filtro que puede sumar
# ganancia, el nombre del campo (en dB) que hay que leerle. El filtro
# de "Ganancia" es el caso obvio, pero la "Ganancia de salida" del
# Compresor hace exactamente lo mismo (sube la señal después de
# comprimirla), así que se suma igual.
CAMPOS_GANANCIA_FILTRO = {
    "gain_filter": "db",
    "compressor_filter": "output_gain",
}

# Cada cuánto (en milisegundos) se vuelve a consultar a OBS, en
# segundo plano, cuánta ganancia extra están aplicando los filtros de
# cada fuente (ver _calcular_ganancia_extra_fuente). No hace falta que
# sea tan seguido como el medidor de nivel: la ganancia de un filtro no
# cambia sola, sólo cuando alguien la toca -y esos casos puntuales ya
# se refrescan al toque, sin esperar este ciclo (ver
# _refrescar_ganancia_fuente)-, así que este ciclo es sólo una red de
# contención por si algo se movió desde otro lado (otra instancia del
# programa, o la propia ventana de filtros de OBS).
INTERVALO_REFRESCO_GANANCIA_MS = 1000

TIPOS_MONITOREO = [
    "OBS_MONITORING_TYPE_NONE",
    "OBS_MONITORING_TYPE_MONITOR_ONLY",
    "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT",
]

ETIQUETAS_MONITOREO = {
    "OBS_MONITORING_TYPE_NONE": "🎧 OFF",
    "OBS_MONITORING_TYPE_MONITOR_ONLY": "🎧 SOLO YO",
    "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT": "🎧 YO + STREAM",
}

COLORES_MONITOREO = {
    "OBS_MONITORING_TYPE_NONE": "#394151",
    "OBS_MONITORING_TYPE_MONITOR_ONLY": "#4dabf7",
    "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT": "#2fd693",
}

PALETA_ETIQUETAS = [
    None,
    "#e53935",   # rojo
    "#fb8c00",   # naranja
    "#fdd835",   # amarillo
    "#43a047",   # verde
    "#00acc1",   # turquesa/cian
    "#1e88e5",   # azul
    "#5e35b1",   # índigo
    "#8e24aa",   # violeta
    "#e91e63",   # rosa/magenta
]

COLOR_BORDE_PRINCIPAL = "#ffb454"

COLOR_GRIS_ATENUADO = "#8e98ad"



conectado = False
cliente_obs = None                                                                    
cliente_eventos = None                                              

_lock_pedidos_obs = threading.Lock()

_lock_sincronizar_escenas = threading.Lock()


class _ClienteOBSSincronizado:
    """Envoltorio fino sobre el ReqClient real: cada llamada a un método
    (get_input_list, set_input_volume, etc.) pasa por _lock_pedidos_obs
    antes de tocar el socket, así nunca hay dos pedidos en simultáneo
    sin importar desde qué hilo se llamen."""

    def __init__(self, cliente_real):
        self._cliente_real = cliente_real

    def __getattr__(self, nombre_attr):
        atributo = getattr(self._cliente_real, nombre_attr)
        if not callable(atributo):
            return atributo

        def _llamada_con_lock(*args, **kwargs):
            with _lock_pedidos_obs:
                return atributo(*args, **kwargs)

        return _llamada_con_lock

fuentes = {}                                                          
niveles_actuales = {}                                                                             
# Igual que 'niveles_entrada' de acá abajo (el nivel "de entrada" que
# sigue llegando en vivo con la fuente muteada), pero SIN recortarlo a
# 1.0. Se usa sólo para la reconstrucción del nivel de una fuente
# muteada en actualizar_vu_meters_ui: recortar a 1.0 ANTES de
# multiplicar por la ganancia del fader le pone un techo artificial al
# resultado (nunca puede superar la posición del fader), lo que hace
# que el medidor deje de reaccionar al audio real en cualquier fuente
# cuyo filtro de Ganancia empuje la señal por encima de 0dB -algo
# habitual en audio de escritorio-. Ver el comentario completo en
# actualizar_vu_meters_ui.
niveles_crudos = {}
niveles_entrada = {}                                                                                       
niveles_antes_mute = {}
ultima_actualizacion_nivel = {}                                                              
# Momento (time.monotonic) de la última vez que cada fuente saturó (0 dB
# o más). Se guarda por separado del nivel normal porque el nivel normal
# ya llega recortado a 1.0 (ver on_input_volume_meters) y esa
# información se perdería; el LED en rojo total se sostiene un ratito
# después de ese instante (ver DURACION_SATURACION_SEG) para que se
# note aunque el pico haya sido muy corto, igual que el indicador de
# clipping de OBS.
ultima_vez_saturado = {}
# Ganancia extra (en dB) que están aplicando ahora mismo los filtros de
# audio de cada fuente (ver CAMPOS_GANANCIA_FILTRO), sumada entre todos
# los filtros de ese tipo que estén HABILITADOS. Se usa para que el
# medidor de nivel reaccione a esos filtros -sobre todo al de
# "Ganancia"- y para poder marcar saturación en rojo cuando la
# ganancia agregada es alta, aunque la señal de entrada sea floja.
ganancia_filtros_db = {}

config_soundboard = {}
miniaturas_cargadas = {}
# Caché de las imágenes YA DECODIFICADAS (abiertas con Pillow, con el
# exif_transpose y la conversión de modo ya aplicados), separado del
# caché de miniaturas de arriba. La apertura + decodificación de un
# archivo de imagen (sobre todo fotos grandes, de celular o 4K) es la
# parte cara de mostrar un pad; una vez decodificada una vez, achicarla
# a distintos tamaños de pad es prácticamente gratis. Sin este caché,
# cada reconstrucción de la interfaz (por ejemplo, al redimensionar la
# ventana) volvía a leer y decodificar TODAS las imágenes de TODOS los
# pads desde cero, porque el tamaño de pad cambia con la ventana y el
# caché de miniaturas usa el tamaño exacto como parte de la clave — eso
# era lo que tildaba el programa.
_imagenes_decodificadas_cache = {}

tamano_icono_actual = TAMANO_ICONO_POR_DEFECTO

orientacion_paneles = "vertical"                                             
orden_paneles = ["fuentes", "soundboard"]                          

cuerpo = None                                                                    
columnas_soundboard = 4                                                  

num_pads_soundboard = NUM_BOTONES_SOUNDBOARD_INICIAL                          

orden_fuentes = []                                                                    
columnas_fuentes = 1                                                                               

fuentes_principales = set()                                             
colores_fuentes = {}                                                          
escena_actual_nombres = set()                                                          
escena_actual_obtenida = False                                                            

_dialogo_filtros_abierto = {"nombre": None, "refrescar": None, "ventana": None}

_panel_en_arrastre = {"origen": None}



def cargar_config_conexion():
    if os.path.exists(ARCHIVO_CONEXION):
        try:
            with open(ARCHIVO_CONEXION, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"host": "localhost", "puerto": "4455", "password": ""}


def guardar_config_conexion(host, puerto, password):
    try:
        with open(ARCHIVO_CONEXION, "w", encoding="utf-8") as f:
            json.dump({"host": host, "puerto": str(puerto), "password": password}, f)
    except Exception as e:
        print(f"No se pudo guardar la configuración de conexión: {e}")



def cargar_config_soundboard():
    global config_soundboard
    if os.path.exists(ARCHIVO_SOUNDBOARD):
        try:
            with open(ARCHIVO_SOUNDBOARD, "r", encoding="utf-8") as f:
                config_soundboard = json.load(f)
                return
        except Exception as e:
            print(f"No se pudo leer la configuración del soundboard: {e}")
    config_soundboard = {}


def guardar_config_soundboard():
    try:
        with open(ARCHIVO_SOUNDBOARD, "w", encoding="utf-8") as f:
            json.dump(config_soundboard, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"No se pudo guardar la configuración del soundboard: {e}")



def cargar_config_interfaz():
    if os.path.exists(ARCHIVO_INTERFAZ):
        try:
            with open(ARCHIVO_INTERFAZ, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"No se pudo leer la configuración de interfaz: {e}")
    return {}


def guardar_config_interfaz(datos_nuevos):
    """Actualiza sólo las claves indicadas, conservando el resto de la
    configuración de interfaz ya guardada."""
    actual = cargar_config_interfaz()
    actual.update(datos_nuevos)
    try:
        with open(ARCHIVO_INTERFAZ, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"No se pudo guardar la configuración de interfaz: {e}")



def _valor(obj, *nombres_posibles):
    """Lee un campo probando varios nombres, porque según la versión de la
    librería los datos llegan como diccionarios o como objetos con
    atributos (algunas respuestas y eventos vienen en snake_case, otras en
    camelCase). Se usa tanto para los eventos de audio como para la lista
    de fuentes, así ninguna fuente se pierde por una diferencia de formato."""
    for nombre in nombres_posibles:
        if isinstance(obj, dict):
            if nombre in obj:
                return obj[nombre]
        elif hasattr(obj, nombre):
            return getattr(obj, nombre)
    return None


def on_input_volume_meters(datos):
    try:
        entradas = _valor(datos, "inputs") or []
        ahora = time.monotonic()
        for item in entradas:
            nombre = _valor(item, "input_name", "inputName")
            canales = _valor(item, "input_levels_mul", "inputLevelsMul") or []
            pico = 0.0
            pico_entrada = 0.0
            pico_entrada_crudo = 0.0
            hubo_saturacion = False
            for canal in canales:
                valores_validos = []
                if isinstance(canal, (list, tuple)):
                    for v in canal:
                        try:
                            valores_validos.append(float(v))
                        except (TypeError, ValueError):
                            continue
                else:
                    try:
                        valores_validos.append(float(canal))
                    except (TypeError, ValueError):
                        continue
                if not valores_validos:
                    continue
                if len(valores_validos) >= 2:
                    valor_canal = valores_validos[1]
                else:
                    valor_canal = valores_validos[0]
                valor_entrada = valores_validos[2] if len(valores_validos) >= 3 else valor_canal
                # NO se convierte nada acá. OBS manda 'input_levels_mul'
                # siempre como multiplicador LINEAL (1.0 = 0dB), nunca
                # como porcentaje 0-100 -antes había código que, si el
                # valor caía entre 1.0 y 100.0, asumía que era un
                # porcentaje y lo dividía por 100. Esa suposición está
                # mal: el multiplicador lineal real SÍ puede superar
                # 1.0 cuando el audio está saturando de verdad (por
                # ejemplo con el filtro de Ganancia bien arriba, un
                # pasaje fuerte puede dar 3.0, 5.0, etc. -varios dB por
                # encima de 0dB-), y esos valores caen justo en el
                # rango "1.0 a 100.0". Dividirlos por 100 los aplastaba
                # a un número chico, así que cuanto MÁS saturaba el
                # audio real, más probable era que el medidor mostrara
                # MENOS nivel en vez de más. El recorte a 1.0 para el
                # dibujo del medidor se hace más abajo con min(1.0, ...),
                # que es la forma correcta de manejar un valor por
                # encima de 0dB sin perder la información de que hubo
                # saturación real.
                # Ojo: la saturación se chequea ACÁ, sobre el valor
                # todavía sin recortar a 1.0. El multiplicador lineal
                # que manda OBS vale 1.0 en 0 dB y puede superarlo si la
                # señal realmente está saturando (clipping); si
                # esperáramos a "pico" (ya recortado un poco más abajo
                # con min(1.0, ...)) esa información se perdería sin
                # forma de distinguir "llegó justo a 0 dB" de "se fue
                # varios dB por encima".
                #
                # Importante: se chequea SÓLO 'valor_canal' (el nivel
                # después de aplicar fader y mute, lo que de verdad
                # está sonando ahora mismo), NUNCA 'valor_entrada' (la
                # señal cruda de ANTES del fader). Antes se chequeaban
                # los dos, y eso hacía que una fuente cuya señal de
                # origen entra fuerte apareciera marcada como
                # "saturando" aunque el fader estuviera bien abajo y
                # fuera literalmente imposible que saturase con ese
                # volumen -'valor_entrada' ya se usa por separado más
                # abajo (ver nivel_mul_crudo en actualizar_vu_meters_ui)
                # para la detección DINÁMICA que sí tiene en cuenta el
                # fader actual; no hace falta duplicarla acá encima con
                # el dato crudo.
                if valor_canal >= UMBRAL_SATURACION_MUL:
                    hubo_saturacion = True
                pico = max(pico, max(0.0, min(1.0, valor_canal)))
                pico_entrada = max(pico_entrada, max(0.0, min(1.0, valor_entrada)))
                # Mismo valor que 'pico_entrada' arriba, pero SIN el
                # techo de 1.0: se necesita entero para la
                # reconstrucción de 'elif muted' en
                # actualizar_vu_meters_ui (ver 'niveles_crudos' y el
                # comentario ahí) -si a esta altura ya lo recortamos a
                # 1.0, una fuente con un filtro de Ganancia que empuja
                # la señal bien por encima de 0dB (algo común en audio
                # de escritorio, más "caliente" que un micrófono) queda
                # SIEMPRE en el techo, y multiplicada después por la
                # ganancia del fader da un número que nunca puede
                # superar exactamente la posición del fader -aunque el
                # audio real suba y baje, para la cuenta es como si
                # siempre estuviera al tope-, y el medidor deja de
                # reaccionar al audio real.
                pico_entrada_crudo = max(pico_entrada_crudo, max(0.0, valor_entrada))
            if nombre:
                niveles_actuales[nombre] = pico
                niveles_entrada[nombre] = pico_entrada
                niveles_crudos[nombre] = pico_entrada_crudo
                widgets = fuentes.get(nombre)
                if pico > 0.0001:
                    niveles_antes_mute[nombre] = pico
                ultima_actualizacion_nivel[nombre] = ahora
                if hubo_saturacion:
                    ultima_vez_saturado[nombre] = ahora
    except Exception as e:
        print(f"Error procesando niveles de audio: {e}")

def on_scene_created(_datos):
    """Cuando se crea una escena nueva en OBS, la igualamos al resto:
    le agregamos la fuente de efectos del soundboard y las fuentes
    marcadas como 'principales' (ver _alternar_principal), sin que el
    usuario tenga que acordarse de apretar 'Actualizar fuentes'. El
    resto de las fuentes de audio YA NO se fuerzan a todas las escenas:
    sólo se muestran (grises si no están en la escena al aire)."""
    threading.Thread(target=preparar_fuente_efectos, daemon=True).start()
    threading.Thread(target=asegurar_fuentes_principales_en_todas_las_escenas, daemon=True).start()


def on_current_program_scene_changed(_datos):
    """La escena al aire cambió (desde OBS o desde otra instancia de
    este mismo programa): recalculamos qué fuentes están 'en escena'
    para actualizar el gris de las que no están."""
    threading.Thread(target=_refrescar_membresia_escena, daemon=True).start()


def on_scene_item_enable_state_changed(_datos):
    """Alguien prendió/apagó el 'ojito' de una fuente en la escena
    actual: puede cambiar si esa fuente cuenta como 'en escena'."""
    threading.Thread(target=_refrescar_membresia_escena, daemon=True).start()


def on_scene_item_created(_datos):
    threading.Thread(target=_refrescar_membresia_escena, daemon=True).start()


def on_scene_item_removed(_datos):
    threading.Thread(target=_refrescar_membresia_escena, daemon=True).start()


def on_input_mute_state_changed(datos):
    """Alguien mutea/desmutea una fuente directamente desde OBS (u otro
    controlador): reflejarlo acá en tiempo real, sin esperar al próximo
    'Actualizar fuentes'."""
    nombre = _valor(datos, "input_name", "inputName")
    muted = _valor(datos, "input_muted", "inputMuted")
    if nombre is None or muted is None:
        return
    ventana.after(0, lambda: _sincronizar_mute_remoto(nombre, muted))


def _sincronizar_mute_remoto(nombre, muted):
    widgets = fuentes.get(nombre)
    if not widgets:
        return
    widgets["muted"] = muted
    _actualizar_boton_circular(
        widgets["mute"],
        texto_nuevo=("🔇" if muted else "🔊"),
        color_nuevo=("#ff5567" if muted else "#394151")
    )
    _actualizar_estado_gris(nombre)


def on_input_volume_changed(datos):
    """Ídem para el volumen: si alguien mueve el fader desde OBS."""
    nombre = _valor(datos, "input_name", "inputName")
    vol_db = _valor(datos, "input_volume_db", "inputVolumeDb")
    if nombre is None or vol_db is None:
        return
    ventana.after(0, lambda: _sincronizar_volumen_remoto(nombre, vol_db))


def _sincronizar_volumen_remoto(nombre, vol_db):
    widgets = fuentes.get(nombre)
    if not widgets or widgets.get("arrastrando"):
        return
    widgets["fader"].set(vol_db)
    if vol_db <= UMBRAL_SILENCIO:
        widgets["db"].config(text="SILENCIO", fg="#828da6")
    else:
        widgets["db"].config(text=f"{vol_db:.1f} dB", fg="#2fd693")


def on_input_audio_monitor_type_changed(datos):
    """Ídem para el tipo de monitoreo (auriculares)."""
    nombre = _valor(datos, "input_name", "inputName")
    tipo = _valor(datos, "monitor_type", "monitorType")
    if nombre is None or not tipo:
        return
    ventana.after(0, lambda: _sincronizar_monitor_remoto(nombre, tipo))


def _sincronizar_monitor_remoto(nombre, tipo):
    widgets = fuentes.get(nombre)
    if not widgets:
        return
    widgets["tipo_monitor"] = tipo
    _actualizar_boton_circular(widgets["monitor"], color_nuevo=COLORES_MONITOREO.get(tipo, "#394151"))


def on_input_name_changed(datos):
    """Alguien renombró una fuente directamente desde OBS (o desde
    otra instancia de este mismo programa): reflejarlo acá sin que el
    usuario tenga que apretar 'Actualizar fuentes'. Si el renombre se
    originó ACÁ (ver _confirmar_renombrar_fuente), este mismo evento
    también llega -OBS le hace eco a todos los clientes conectados,
    incluido el que lo pidió- pero para entonces _renombrar_fuente_localmente
    ya no encuentra el nombre viejo (ya se migró) y no hace nada de
    más."""
    nombre_viejo = _valor(datos, "old_input_name", "oldInputName")
    nombre_nuevo = _valor(datos, "input_name", "inputName")
    if not nombre_viejo or not nombre_nuevo:
        return
    ventana.after(0, lambda: _renombrar_fuente_localmente(nombre_viejo, nombre_nuevo))


def _quizas_refrescar_dialogo_filtros(datos):
    """Si hay un diálogo de filtros abierto para esta misma fuente, lo
    refresca para reflejar el cambio hecho desde OBS (filtro agregado,
    quitado, activado/desactivado, etc.)."""
    nombre = _valor(datos, "source_name", "sourceName")
    if nombre and _dialogo_filtros_abierto["nombre"] == nombre and _dialogo_filtros_abierto["refrescar"]:
        ventana.after(0, _dialogo_filtros_abierto["refrescar"])


def _calcular_ganancia_extra_fuente(nombre):
    """Suma, en dB, la ganancia que están aplicando ahora mismo los
    filtros de audio de 'nombre' que pueden subir la señal por encima
    de lo que ya trae (ver CAMPOS_GANANCIA_FILTRO). Sólo cuenta la de
    los filtros que están HABILITADOS: uno desactivado no afecta el
    audio real. Hace varios pedidos a OBS (uno por la lista de
    filtros, uno más por cada filtro relevante para leerle el valor),
    así que SIEMPRE se llama desde un hilo aparte, nunca desde el hilo
    de la interfaz."""
    if not conectado:
        return 0.0
    total_db = 0.0
    try:
        filtros = cliente_obs.get_source_filter_list(nombre).filters or []
    except Exception:
        return 0.0
    for filtro in filtros:
        kind = _valor(filtro, "filter_kind", "filterKind")
        campo = CAMPOS_GANANCIA_FILTRO.get(kind)
        if not campo:
            continue
        if not _valor(filtro, "filter_enabled", "filterEnabled"):
            continue
        nombre_filtro = _valor(filtro, "filter_name", "filterName")
        try:
            info = cliente_obs.get_source_filter(nombre, nombre_filtro)
            ajustes = _valor(info, "filter_settings", "filterSettings") or {}
        except Exception:
            continue
        try:
            total_db += float(ajustes.get(campo, 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return total_db


def _refrescar_ganancia_fuente(nombre):
    """Recalcula y guarda la ganancia extra de UNA fuente puntual, en
    segundo plano. Se llama después de cualquier acción que pueda
    haberla cambiado (tocar el filtro de Ganancia, prenderlo/apagarlo,
    borrarlo, etc.) para que el medidor reaccione al toque, sin
    esperar al ciclo de refresco periódico."""
    ganancia_filtros_db[nombre] = _calcular_ganancia_extra_fuente(nombre)


def _refrescar_ganancia_fuente_en_hilo(nombre):
    threading.Thread(target=_refrescar_ganancia_fuente, args=(nombre,), daemon=True).start()


def _refrescar_ganancia_todas_las_fuentes():
    for nombre in list(fuentes.keys()):
        _refrescar_ganancia_fuente(nombre)


def _programar_refresco_ganancia():
    """Red de contención: cada INTERVALO_REFRESCO_GANANCIA_MS
    reconsulta la ganancia de todas las fuentes conocidas, por si algo
    la cambió sin pasar por ninguno de los ganchos puntuales de arriba
    (por ejemplo, tocando el filtro directamente desde OBS)."""
    if conectado:
        threading.Thread(target=_refrescar_ganancia_todas_las_fuentes, daemon=True).start()
    ventana.after(INTERVALO_REFRESCO_GANANCIA_MS, _programar_refresco_ganancia)


def _quizas_refrescar_ganancia_por_evento(datos):
    nombre = _valor(datos, "source_name", "sourceName")
    if nombre:
        _refrescar_ganancia_fuente_en_hilo(nombre)


def on_source_filter_created(datos):
    _quizas_refrescar_dialogo_filtros(datos)
    _quizas_refrescar_ganancia_por_evento(datos)


def on_source_filter_removed(datos):
    _quizas_refrescar_dialogo_filtros(datos)
    _quizas_refrescar_ganancia_por_evento(datos)


def on_source_filter_enable_state_changed(datos):
    _quizas_refrescar_dialogo_filtros(datos)
    _quizas_refrescar_ganancia_por_evento(datos)


def on_source_filter_list_reindexed(datos):
    _quizas_refrescar_dialogo_filtros(datos)
    _quizas_refrescar_ganancia_por_evento(datos)


def on_source_filter_name_changed(datos):
    _quizas_refrescar_dialogo_filtros(datos)


def _leer_fuentes_globales_obs():
    """Los canales de audio 'globales' de OBS (Desktop Audio 1/2, Mic/Aux
    1 a 4, los que se eligen desde Configuración > Audio, no desde una
    escena puntual) no son parte de NINGUNA escena: suenan siempre,
    tanto si la escena al aire los tiene como si no, porque OBS los
    mezcla por canal y no como ítem de escena. Como get_scene_item_list()
    nunca los va a devolver, sin este chequeo aparte el programa no podía
    detectarlos como "activos" y los pintaba en gris permanentemente
    (como si estuvieran fuera de la escena), aunque en realidad sonaran
    siempre. Achican esto tratándolos igual que una fuente 'principal':
    siempre cuentan como en escena."""
    try:
        especiales = cliente_obs.get_special_inputs()
    except Exception as e:
        print(f"No se pudieron leer las fuentes de audio globales de OBS: {e}")
        return set()

    nombres = set()
    for campo in ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4"):
        nombre = _valor(especiales, campo)
        if nombre:
            nombres.add(nombre)
    return nombres


def _refrescar_membresia_escena():
    """Vuelve a leer qué fuentes están presentes Y activas en la escena
    que está al aire ahora mismo, para poder mostrar en gris las que no
    lo están. Se llama al conectar, al actualizar fuentes, y cada vez
    que la escena activa cambia (evento de OBS)."""
    if not conectado:
        return
    try:
        respuesta_escena = cliente_obs.get_current_program_scene()
        escena_actual = _valor(
            respuesta_escena,
            "current_program_scene_name", "currentProgramSceneName",
            "scene_name", "sceneName"
        )
        nombres_en_escena = set()
        if escena_actual:
            items = cliente_obs.get_scene_item_list(escena_actual).scene_items
            for it in items:
                nombre_item = _valor(it, "source_name", "sourceName")
                habilitado = _valor(it, "scene_item_enabled", "sceneItemEnabled")
                if nombre_item and habilitado:
                    nombres_en_escena.add(nombre_item)
        nombres_en_escena |= _leer_fuentes_globales_obs()
        ventana.after(0, lambda: _aplicar_membresia_escena(nombres_en_escena))
    except Exception as e:
        print(f"No se pudo actualizar la escena activa: {e}")


def _aplicar_membresia_escena(nombres_en_escena):
    global escena_actual_nombres, escena_actual_obtenida
    escena_actual_nombres = nombres_en_escena
    escena_actual_obtenida = True
    for nombre in list(fuentes.keys()):
        _actualizar_estado_gris(nombre)


def conectar_obs():
    global cliente_obs, cliente_eventos, conectado

    if conectado:
        desconectar_obs()
        return

    host = entrada_host.get().strip() or "localhost"
    texto_puerto = entrada_puerto.get().strip() or "4455"
    password = entrada_password.get()

    try:
        puerto = int(texto_puerto)
    except ValueError:
        messagebox.showerror("Puerto inválido", "El puerto debe ser un número.")
        return

    try:
        nuevo_cliente = obs.ReqClient(host=host, port=puerto, password=password, timeout=5)
        nuevo_cliente.get_version()                                                 

        nuevo_cliente_eventos = obs.EventClient(
            host=host, port=puerto, password=password,
            subs=(
                obs.Subs.LOW_VOLUME | obs.Subs.INPUTVOLUMEMETERS |
                obs.Subs.INPUTS | obs.Subs.SCENES | obs.Subs.SCENEITEMS |
                obs.Subs.FILTERS
            )
        )
        nuevo_cliente_eventos.callback.register(on_input_volume_meters)
        nuevo_cliente_eventos.callback.register(on_scene_created)
        nuevo_cliente_eventos.callback.register(on_current_program_scene_changed)
        nuevo_cliente_eventos.callback.register(on_scene_item_enable_state_changed)
        nuevo_cliente_eventos.callback.register(on_scene_item_created)
        nuevo_cliente_eventos.callback.register(on_scene_item_removed)
        nuevo_cliente_eventos.callback.register(on_input_mute_state_changed)
        nuevo_cliente_eventos.callback.register(on_input_volume_changed)
        nuevo_cliente_eventos.callback.register(on_input_audio_monitor_type_changed)
        nuevo_cliente_eventos.callback.register(on_input_name_changed)
        nuevo_cliente_eventos.callback.register(on_source_filter_created)
        nuevo_cliente_eventos.callback.register(on_source_filter_removed)
        nuevo_cliente_eventos.callback.register(on_source_filter_enable_state_changed)
        nuevo_cliente_eventos.callback.register(on_source_filter_list_reindexed)
        nuevo_cliente_eventos.callback.register(on_source_filter_name_changed)

    except Exception as e:
        conectado = False
        messagebox.showerror("Error de conexión", f"No se pudo conectar a OBS.\n\n{e}")
        return

    cliente_obs = _ClienteOBSSincronizado(nuevo_cliente)
    cliente_eventos = nuevo_cliente_eventos
    conectado = True

    guardar_config_conexion(host, puerto, password)
    actualizar_estado_conexion()
    actualizar()


def desconectar_obs():
    global conectado, cliente_obs, cliente_eventos, escena_actual_nombres, escena_actual_obtenida

    conectado = False

    for cliente in (cliente_obs, cliente_eventos):
        try:
            if cliente is not None:
                cliente.disconnect()
        except Exception:
            pass

    cliente_obs = None
    cliente_eventos = None

    for nombre in list(fuentes.keys()):
        fuentes[nombre]["tarjeta_sombra"].destroy()
        del fuentes[nombre]
    niveles_actuales.clear()
    niveles_crudos.clear()
    niveles_entrada.clear()
    niveles_antes_mute.clear()
    ultima_vez_saturado.clear()
    orden_fuentes.clear()
    escena_actual_nombres = set()
    escena_actual_obtenida = False

    actualizar_estado_conexion()


def actualizar_estado_conexion():
    if conectado:
        estado.config(text="● CONECTADO", fg="#2fd693")
        estado_chip.config(highlightbackground="#2fd693")
        boton_conectar.config(text="DESCONECTAR", bg="#ff5567", activebackground="#cb3542")
    else:
        estado.config(text="● DESCONECTADO", fg="#ff5d6c")
        estado_chip.config(highlightbackground="#3f4a5e")
        boton_conectar.config(text="CONECTAR", bg="#2fd693", activebackground="#4fe3ae")



def _ajustar_color(color_hex, cantidad):
    """Aclara (cantidad > 0) u oscurece (cantidad < 0) un color #rrggbb."""
    color_hex = color_hex.lstrip("#")
    r = int(color_hex[0:2], 16)
    g = int(color_hex[2:4], 16)
    b = int(color_hex[4:6], 16)
    r = max(0, min(255, r + cantidad))
    g = max(0, min(255, g + cantidad))
    b = max(0, min(255, b + cantidad))
    return f"#{r:02x}{g:02x}{b:02x}"


def _aclarar_color(color_hex, cantidad=50):
    return _ajustar_color(color_hex, cantidad)


def _oscurecer_color(color_hex, cantidad=50):
    return _ajustar_color(color_hex, -cantidad)


def _oscurecer_color_pct(color_hex, factor=0.4):
    """Oscurece un color #rrggbb multiplicando cada canal por 'factor'
    (0-1), conservando el matiz. A diferencia de _oscurecer_color (que
    resta un valor fijo y termina 'lavando' los colores oscuros hacia
    negro puro), esto sirve para teñir paneles enteros manteniendo el
    tono reconocible."""
    color_hex = color_hex.lstrip("#")
    r = int(int(color_hex[0:2], 16) * factor)
    g = int(int(color_hex[2:4], 16) * factor)
    b = int(int(color_hex[4:6], 16) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _puntos_rect_redondeado(x0, y0, x1, y1, radio):
    """Devuelve los puntos de un rectángulo de esquinas redondeadas de
    verdad, listos para dibujar con create_polygon(..., smooth=True) (el
    suavizado de Tk hace que las esquinas se vean como curvas reales en
    vez de ángulos rectos)."""
    r = max(0, min(radio, (x1 - x0) / 2, (y1 - y0) / 2))
    return [
        x0 + r, y0,  x1 - r, y0,  x1, y0,  x1, y0 + r,
        x1, y1 - r,  x1, y1,  x1 - r, y1,  x0 + r, y1,
        x0, y1,  x0, y1 - r,  x0, y0 + r,  x0, y0,
    ]


def _mezclar_color(color_a, color_b, t):
    """Interpola linealmente entre dos colores #rrggbb (t=0 -> color_a,
    t=1 -> color_b). Base de los degradados de cabecera/franjas de acento."""
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")
    r = int(int(a[0:2], 16) * (1 - t) + int(b[0:2], 16) * t)
    g = int(int(a[2:4], 16) * (1 - t) + int(b[2:4], 16) * t)
    bch = int(int(a[4:6], 16) * (1 - t) + int(b[4:6], 16) * t)
    return f"#{r:02x}{g:02x}{bch:02x}"


def _gradiente_vertical(canvas, x0, y0, x1, y1, color_arriba, color_abajo, pasos=48):
    """Dibuja un degradado vertical suave (franja por franja) entre dos
    colores, usado para dar profundidad a la cabecera y a los paneles en
    vez de un color plano único."""
    alto = max(1, y1 - y0)
    ids = []
    for i in range(pasos):
        t = i / max(1, pasos - 1)
        ya = y0 + alto * i / pasos
        yb = y0 + alto * (i + 1) / pasos
        color = _mezclar_color(color_arriba, color_abajo, t)
        ids.append(canvas.create_rectangle(x0, ya, x1, yb + 1, fill=color, outline=""))
    return ids


def _gradiente_horizontal(canvas, x0, y0, x1, y1, color_izq, color_der, pasos=60):
    """Ídem, pero de izquierda a derecha (se usa para la franja de acento
    fina debajo de la cabecera y las barras de título de los paneles)."""
    ancho = max(1, x1 - x0)
    ids = []
    for i in range(pasos):
        t = i / max(1, pasos - 1)
        xa = x0 + ancho * i / pasos
        xb = x0 + ancho * (i + 1) / pasos
        color = _mezclar_color(color_izq, color_der, t)
        ids.append(canvas.create_rectangle(xa, y0, xb + 1, y1, fill=color, outline=""))
    return ids


def _gradiente_vertical_redondeado(canvas, x0, y0, x1, y1, radio, color_arriba, color_abajo, pasos=48):
    """Igual que _gradiente_vertical (franja por franja, de arriba
    hacia abajo), pero cada franja se recorta en X según la curva de un
    rectángulo de esquinas redondeadas con el radio indicado. Antes el
    marco se dibujaba con rectángulos rectos de punta a punta, así que
    sus esquinas cuadradas sobresalían por fuera del contorno redondeado
    del pad (el 'fondo que se sale por los bordes'); con esto la franja
    se angosta cerca de cada esquina, igual que lo hace el propio
    contorno curvo, y ya no se nota nada por fuera de él."""
    alto = max(1, y1 - y0)
    ancho = max(1, x1 - x0)
    radio = max(0.0, min(radio, ancho / 2, alto / 2))
    ids = []

    def _inset_en(y):
        if radio <= 0:
            return 0.0
        dist_arriba = (y0 + radio) - y
        dist_abajo = y - (y1 - radio)
        dist = max(dist_arriba, dist_abajo, 0.0)
        if dist <= 0:
            return 0.0
        dist = min(dist, radio)
        return radio - math.sqrt(max(0.0, radio * radio - dist * dist))

    for i in range(pasos):
        t = i / max(1, pasos - 1)
        ya = y0 + alto * i / pasos
        yb = y0 + alto * (i + 1) / pasos
        color = _mezclar_color(color_arriba, color_abajo, t)
        inset = max(_inset_en(ya), _inset_en(yb))
        ids.append(canvas.create_rectangle(
            x0 + inset, ya, x1 - inset, yb + 1, fill=color, outline=""
        ))
    return ids


def _dibujar_icono_ecualizador(canvas, cx, cy, alto, color="#2fd693"):
    """Ícono de marca dibujado a mano (barras tipo ecualizador), usado en
    la cabecera cuando no hay un logo propio en assets/iconos/. Así la
    interfaz tiene un ícono desde el primer momento, sin depender de que
    el usuario agregue un archivo."""
    alturas = [0.55, 1.0, 0.7, 0.85]
    ancho_barra = max(3, alto * 0.14)
    espacio = ancho_barra * 1.6
    x0 = cx - (espacio * (len(alturas) - 1)) / 2
    for i, factor in enumerate(alturas):
        h = alto * factor
        x = x0 + i * espacio
        color_barra = _aclarar_color(color, int(20 * (i % 2)))
        _dibujar_rect_redondeado(
            canvas, x - ancho_barra / 2, cy - h / 2, x + ancho_barra / 2, cy + h / 2,
            radio=ancho_barra * 0.5, fill=color_barra, outline=""
        )


def _dibujar_rect_redondeado(canvas, x0, y0, x1, y1, radio=10, **kwargs):
    """Rectángulo de esquinas redondeadas reales (estética de software de
    audio profesional, en vez del borde 100% recto de antes). Se
    mantiene el nombre y la firma originales para no tener que tocar
    cada lugar donde se usaba."""
    return canvas.create_polygon(_puntos_rect_redondeado(x0, y0, x1, y1, radio), smooth=True, **kwargs)


COLOR_SOMBRA = "#05070c"


def _dibujar_bisel_pixel(canvas, x0, y0, x1, y1, color_fondo, grosor=2):
    """Dibuja el relleno de un botón/pad con look de "tecla de consola
    profesional": esquinas redondeadas reales, sombra de profundidad
    detrás, borde definido y un degradado simulado (franja de brillo
    arriba, franja de sombra abajo) en vez del bisel de líneas duras de
    antes. Devuelve el id del relleno y las listas de ids de brillo/
    sombra (para poder recolorear el botón después, ej. al mutear o
    cambiar el modo de escucha), igual que la versión anterior."""
    ancho = max(1, x1 - x0)
    alto = max(1, y1 - y0)
    radio = max(4, min(16, min(ancho, alto) * 0.22))

    canvas.create_polygon(
        _puntos_rect_redondeado(x0 + 3, y0 + 4, x1 + 2, y1 + 4, radio),
        smooth=True, fill=COLOR_SOMBRA, outline=""
    )

    id_relleno = canvas.create_polygon(
        _puntos_rect_redondeado(x0, y0, x1, y1, radio),
        smooth=True, fill=color_fondo, outline=_oscurecer_color(color_fondo, 60), width=grosor
    )

    m = grosor + 1
    alto_brillo = max(3, alto * 0.42)
    ids_claro = [
        canvas.create_polygon(
            _puntos_rect_redondeado(x0 + m, y0 + m, x1 - m, y0 + m + alto_brillo, max(2, radio * 0.6)),
            smooth=True, fill=_aclarar_color(color_fondo, 55), outline=""
        )
    ]
    alto_sombra = max(3, alto * 0.24)
    ids_oscuro = [
        canvas.create_polygon(
            _puntos_rect_redondeado(x0 + m, y1 - m - alto_sombra, x1 - m, y1 - m, max(2, radio * 0.6)),
            smooth=True, fill=_oscurecer_color(color_fondo, 35), outline=""
        )
    ]
    return id_relleno, ids_claro, ids_oscuro


def _dibujar_sombra_difusa(canvas, x0, y0, x1, y1, radio=14, capas=5, color_fondo_panel="#131825", tag="sombra_difusa"):
    ids = []
    for i in range(capas):
        t = i / max(1, capas - 1)
        color = _mezclar_color(color_fondo_panel, COLOR_SOMBRA, t)
        expansion = (capas - i) * 2
        ids.append(canvas.create_polygon(
            _puntos_rect_redondeado(
                x0 - expansion + 3, y0 - expansion + 4,
                x1 + expansion + 3, y1 + expansion + 4,
                radio + expansion
            ),
            smooth=True, fill=color, outline="", tags=(tag,)
        ))
    return ids


def _dibujar_boton_vidrio(canvas, x0, y0, x1, y1, color_fondo, grosor=3):
    ancho = max(1, x1 - x0)
    alto = max(1, y1 - y0)
    radio = max(6, min(24, min(ancho, alto) * 0.22))
    sombra = max(2, min(5, round(min(ancho, alto) * 0.02)))
    canvas.create_polygon(
        _puntos_rect_redondeado(x0 + sombra, y0 + sombra + 2, x1 + sombra, y1 + sombra + 2, radio),
        smooth=True, fill=COLOR_SOMBRA, outline=""
    )
    id_relleno = canvas.create_polygon(
        _puntos_rect_redondeado(x0, y0, x1, y1, radio),
        smooth=True, fill=color_fondo, outline=_oscurecer_color(color_fondo, 55), width=grosor
    )
    inset = max(3, round(min(ancho, alto) * 0.045))
    brillo = _aclarar_color(color_fondo, 24)
    canvas.create_polygon(
        _puntos_rect_redondeado(x0 + inset, y0 + inset, x1 - inset, y0 + alto * 0.22, max(2, radio * 0.55)),
        smooth=True, fill=brillo, outline=""
    )
    return id_relleno, [id_relleno], []

# ------------------------------------------------------------------
# PLACAS DE VIDRIO (el nuevo aspecto de los pads del soundboard)
# ------------------------------------------------------------------
# Los pads ya no se dibujan con primitivas de Tk (polígonos + líneas).
# Tk no suaviza nada de lo que dibuja: cada curva y cada borde diagonal
# queda con escalones visibles, y eso es exactamente lo que se veía
# como "pixelado". Acá, en cambio, cada pad se arma con Pillow a varias
# veces la resolución final (supersampling) y recién al final se achica
# con LANCZOS, que promedia esos sub-píxeles: el resultado es un botón
# con marco claro biselado, foso negro, cara oscura en degradado y un
# reflejo de vidrio en la esquina superior izquierda, todo con bordes
# perfectamente lisos a cualquier tamaño.
FACTOR_SUPERSAMPLING_PLACAS = 4
LADO_MAXIMO_RENDER_PLACA = 1600      # tope del lienzo interno, por costo

COLOR_PLACA_MARCO_ARRIBA = "#d4dcea"     # brillo del marco metálico
COLOR_PLACA_MARCO_ABAJO = "#69778f"
COLOR_PLACA_FOSO = "#04060a"             # ranura negra entre marco y cara
COLOR_PLACA_CARA_ARRIBA = "#2e3646"      # cara del pad, arriba
COLOR_PLACA_CARA_ABAJO = "#161b25"       # cara del pad, abajo

# Caché de placas ya renderizadas. La clave incluye tamaño, color y
# estado, así que mover el mouse por encima de los pads (o reconstruir
# la grilla) no vuelve a pagar el costo del render con Pillow.
_cache_placas = {}
LIMITE_CACHE_PLACAS = 120


def _mezclar_rgb(color_a, color_b, t):
    ra, rb = _hex_a_rgb(color_a), _hex_a_rgb(color_b)
    return tuple(round(ra[i] + (rb[i] - ra[i]) * t) for i in range(3))


def _mezclar_hex(color_a, color_b, t):
    return "#%02x%02x%02x" % _mezclar_rgb(color_a, color_b, t)


def _gradiente_imagen(tam, color_arriba, color_abajo):
    """Franja vertical de color continuo (sin escalones), hecha con una
    tira de 1 pixel de ancho que después se estira: es la forma barata
    de tener un degradado real en vez de los 14/48 rectángulos apilados
    que usa el canvas de Tk."""
    ancho, alto = tam
    tira = Image.new("RGB", (1, max(2, alto)))
    px = tira.load()
    for y in range(tira.height):
        px[0, y] = _mezclar_rgb(color_arriba, color_abajo, y / (tira.height - 1))
    return tira.resize((max(1, ancho), max(1, alto)), Image.BILINEAR)


def _mascara_redondeada(tam, caja, radio):
    mascara = Image.new("L", tam, 0)
    ImageDraw.Draw(mascara).rounded_rectangle(caja, radius=max(1, radio), fill=255)
    return mascara


def _imagen_placa(ancho, alto, acento=None, encendido=False, hover=False, presionado=False, color_marco=None,
                   reproduciendo=False):
    """Devuelve la imagen PIL de una placa de vidrio del tamaño pedido.
    - acento: color del pad (etiqueta del usuario o verde si tiene sonido)
    - encendido: el pad tiene un sonido cargado (cara teñida + halo)
    - hover / presionado: estados visuales del mouse.
    - color_marco: si el usuario le puso una etiqueta de color al pad,
      el marco metálico (lo que antes era un cuadrado de color pegado
      POR FUERA de la placa, vía el borde del widget) ahora se tiñe con
      ese color directamente en el propio marco, así queda integrado a
      la placa en vez de verse como un recuadro aparte.
    - reproduciendo: el pad está sonando DE VERDAD en este momento,
      según el estado real que reporta OBS (no una suposición nuestra).
      Se dibuja bien fuerte -marco y halo se tiñen con el color del
      pad a máxima intensidad- para que quede claro que el efecto
      sigue activo aunque ya no se lo llegue a escuchar."""
    ancho = max(24, int(ancho))
    alto = max(24, int(alto))

    color_resplandor = None
    if reproduciendo:
        base_resplandor = acento or color_marco or "#2fd693"
        # Aclarado con blanco: un acento oscuro o poco saturado (que
        # casi no se nota sólo teñido) igual queda bien luminoso.
        color_resplandor = _mezclar_hex(base_resplandor, "#ffffff", 0.3)

    S = FACTOR_SUPERSAMPLING_PLACAS
    while S > 1 and max(ancho, alto) * S > LADO_MAXIMO_RENDER_PLACA:
        S -= 1

    W, H = ancho * S, alto * S
    lado = min(W, H)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    margen = max(1, round(lado * 0.035))
    # Bordes finos: el marco claro y el foso negro son apenas una
    # línea cada uno, así la cara del pad se come casi todo el cuadro
    # en vez de quedar encerrada en un aro grueso.
    grosor_marco = max(1, round(lado * 0.018))
    grosor_foso = max(1, round(lado * 0.009))
    radio = max(2, round(lado * 0.235))

    caja_ext = [margen, margen, W - 1 - margen, H - 1 - margen]
    caja_foso = [caja_ext[0] + grosor_marco, caja_ext[1] + grosor_marco,
                 caja_ext[2] - grosor_marco, caja_ext[3] - grosor_marco]
    caja_cara = [caja_foso[0] + grosor_foso, caja_foso[1] + grosor_foso,
                 caja_foso[2] - grosor_foso, caja_foso[3] - grosor_foso]
    radio_foso = max(2, radio - grosor_marco)
    radio_cara = max(2, radio_foso - grosor_foso)

    # Sombra proyectada (difusa de verdad, con desenfoque gaussiano).
    sombra = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sombra).rounded_rectangle(
        [caja_ext[0], caja_ext[1] + margen * 0.5, caja_ext[2], caja_ext[3] + margen * 0.9],
        radius=radio, fill=210
    )
    sombra = sombra.filter(ImageFilter.GaussianBlur(margen * 0.85))
    img.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), sombra)

    if color_resplandor:
        # Resplandor que se escapa por fuera del marco, sobre el
        # margen que separa la placa del fondo oscuro del panel: así
        # el borde se ve realmente "iluminado" contra el fondo, en vez
        # de depender sólo de que el color contraste con el propio
        # marco metálico.
        fuga = Image.new("L", (W, H), 0)
        ImageDraw.Draw(fuga).rounded_rectangle(caja_ext, radius=radio, fill=255)
        fuga = fuga.filter(ImageFilter.GaussianBlur(margen * 2.4))
        img.paste(Image.new("RGBA", (W, H), _hex_a_rgb(color_resplandor) + (255,)), (0, 0),
                  fuga.point(lambda v: int(v * 0.9)))

    # Marco claro con degradado (arriba brilla, abajo se apaga). Si el
    # pad tiene una etiqueta de color, el marco se tiñe con ese color
    # (en vez de quedar siempre gris metálico y depender de un cuadrado
    # de color aparte alrededor de la placa).
    mascara_ext = _mascara_redondeada((W, H), caja_ext, radio)
    color_marco_arriba, color_marco_abajo = COLOR_PLACA_MARCO_ARRIBA, COLOR_PLACA_MARCO_ABAJO
    if color_marco:
        color_marco_arriba = _mezclar_hex(COLOR_PLACA_MARCO_ARRIBA, color_marco, 0.65)
        color_marco_abajo = _mezclar_hex(COLOR_PLACA_MARCO_ABAJO, color_marco, 0.65)
    if color_resplandor:
        color_marco_arriba = color_resplandor
        color_marco_abajo = color_resplandor
    marco = _gradiente_imagen((W, H), color_marco_arriba, color_marco_abajo).convert("RGBA")
    img.paste(marco, (0, 0), mascara_ext)

    # Foso negro: la ranura que separa el marco de la cara y da la
    # sensación de que la tecla está encastrada.
    mascara_foso = _mascara_redondeada((W, H), caja_foso, radio_foso)
    img.paste(Image.new("RGBA", (W, H), _hex_a_rgb(COLOR_PLACA_FOSO) + (255,)), (0, 0), mascara_foso)

    # Cara del pad.
    color_arriba, color_abajo = COLOR_PLACA_CARA_ARRIBA, COLOR_PLACA_CARA_ABAJO
    if acento and encendido:
        color_arriba = _mezclar_hex(COLOR_PLACA_CARA_ARRIBA, acento, 0.55)
        color_abajo = _mezclar_hex(COLOR_PLACA_CARA_ABAJO, acento, 0.35)
    elif acento:
        color_arriba = _mezclar_hex(COLOR_PLACA_CARA_ARRIBA, acento, 0.22)
        color_abajo = _mezclar_hex(COLOR_PLACA_CARA_ABAJO, acento, 0.14)
    mascara_cara = _mascara_redondeada((W, H), caja_cara, radio_cara)
    cara = _gradiente_imagen((W, H), color_arriba, color_abajo).convert("RGBA")
    img.paste(cara, (0, 0), mascara_cara)

    # Sin reflejo de vidrio: la cara queda lisa (sólo el degradado), así
    # no compite con la miniatura del sonido que va encima ocupando casi
    # toda la cara del pad.

    # Halo interior de color: tenue cuando el pad sólo tiene sonido
    # cargado, y mucho más fuerte -con el color del resplandor- cuando
    # está sonando de verdad en este momento.
    color_halo = None
    intensidad_halo = 0.55
    if color_resplandor:
        color_halo = color_resplandor
        intensidad_halo = 0.9
    elif acento and encendido:
        color_halo = acento

    if color_halo:
        adentro = _mascara_redondeada(
            (W, H),
            [caja_cara[0] + grosor_foso * 1.6, caja_cara[1] + grosor_foso * 1.6,
             caja_cara[2] - grosor_foso * 1.6, caja_cara[3] - grosor_foso * 1.6],
            radio_cara
        )
        halo = ImageChops.subtract(mascara_cara, adentro)
        halo = halo.filter(ImageFilter.GaussianBlur(max(1, grosor_foso * (1.6 if color_resplandor else 1.2))))
        halo = halo.point(lambda v: int(v * intensidad_halo))
        img.paste(Image.new("RGBA", (W, H), _hex_a_rgb(color_halo) + (255,)), (0, 0), halo)

    if hover:
        img.paste(Image.new("RGBA", (W, H), (255, 255, 255, 255)), (0, 0),
                  mascara_ext.point(lambda v: int(v * 0.10)))
    if presionado:
        img.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0),
                  mascara_ext.point(lambda v: int(v * 0.28)))

    if color_resplandor:
        # Trazo nítido justo sobre el borde del marco, encima de todo
        # lo demás: le da al brillo un límite definido y bien marcado,
        # en vez de quedar sólo como un degradado difuso.
        ancho_trazo = max(2, round(grosor_marco * 1.4))
        ImageDraw.Draw(img).rounded_rectangle(
            caja_ext, radius=radio,
            outline=_hex_a_rgb(color_resplandor) + (255,), width=ancho_trazo
        )

    return img.resize((ancho, alto), Image.LANCZOS)


def _placa_tk(ancho, alto, acento=None, encendido=False, hover=False, presionado=False, color_marco=None,
              reproduciendo=False):
    """Versión cacheada y ya convertida a PhotoImage (lista para
    create_image). Si no hay Pillow devuelve None y quien la llama cae
    al dibujo viejo con primitivas de Tk."""
    if not HAY_PILLOW:
        return None
    clave = (int(ancho), int(alto), acento, bool(encendido), bool(hover), bool(presionado), color_marco,
              bool(reproduciendo))
    foto = _cache_placas.get(clave)
    if foto is not None:
        return foto
    try:
        foto = ImageTk.PhotoImage(
            _imagen_placa(ancho, alto, acento, encendido, hover, presionado, color_marco, reproduciendo)
        )
    except Exception:
        return None
    if len(_cache_placas) > LIMITE_CACHE_PLACAS:
        _cache_placas.clear()
    _cache_placas[clave] = foto
    return foto


FACTOR_SUPERSAMPLING_CIRCULOS = 6
# Mismo truco que el '_S' de tu otro programa: Tk dibuja un
# create_oval tal cual, sin suavizar el borde (se nota como
# "escalones" sobre todo en botones chicos). Acá en cambio el círculo
# se dibuja con Pillow a esta cantidad de veces más resolución de la
# que se va a mostrar, y se achica con un filtro de buena calidad
# (LANCZOS): al promediar varios "sub-píxeles" en cada píxel final, el
# borde curvo queda liso. Más alto = más suave y más caro de generar;
# 4 ya se ve bien y sigue siendo instantáneo para un botón de este
# tamaño.


def _hex_a_rgb(color_hex):
    color_hex = color_hex.lstrip("#")
    return tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4))


def _renderizar_circulo_boton(lado, color_fondo):
    """Dibuja con Pillow, ya suavizadas, todas las capas del círculo de
    un botón (sombra proyectada, relleno con borde, brillo superior
    tipo vidrio y sombra interior inferior) y devuelve un
    ImageTk.PhotoImage del tamaño final ('lado' x 'lado'), lista para
    poner en el canvas con create_image. El ícono/texto de encima se
    sigue dibujando aparte con create_text -ese ya sale nítido con Tk,
    el problema era sólo el borde curvo del círculo."""
    S = FACTOR_SUPERSAMPLING_CIRCULOS
    grande = lado * S
    img = Image.new("RGBA", (grande, grande), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = grande / 2
    r = (lado - 8) / 2 * S

    draw.ellipse(
        [cx - r + 2 * S, cy - r + 3 * S, cx + r + 2 * S, cy + r + 3 * S],
        fill=_hex_a_rgb(COLOR_SOMBRA) + (255,)
    )
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        fill=_hex_a_rgb(color_fondo) + (255,),
        outline=_hex_a_rgb(_oscurecer_color(color_fondo, 70)) + (255,),
        width=max(1, round(2 * S)),
    )
    draw.ellipse(
        [cx - r * 0.55, cy - r * 0.78, cx + r * 0.55, cy - r * 0.05],
        fill=_hex_a_rgb(_aclarar_color(color_fondo, 60)) + (255,)
    )
    # Pillow mide el ángulo del arco al revés que el create_arc de Tk
    # (sentido y punto de arranque distintos); (20, 160) es la
    # conversión que deja este pedazo de sombra en el mismo lugar
    # -pegado abajo del círculo- que el start=200/extent=140 original.
    draw.pieslice(
        [cx - r + 2 * S, cy - r * 0.1, cx + r - 2 * S, cy + r - 2 * S],
        start=20, end=160,
        fill=_hex_a_rgb(_oscurecer_color(color_fondo, 35)) + (255,)
    )

    img = img.resize((lado, lado), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def _crear_boton_circular(parent, texto, diametro, fuente_tam, color_fondo, comando):
    lado = max(28, diametro + 8)
    canvas = tk.Canvas(parent, width=lado, height=lado, bg=parent["bg"], highlightthickness=0, cursor="hand2")
    cx = cy = lado / 2
    r = (lado - 8) / 2

    if HAY_PILLOW:
        imagen_tk = _renderizar_circulo_boton(lado, color_fondo)
        id_circulo = canvas.create_image(cx, cy, image=imagen_tk)
        canvas.imagen_circulo_actual = imagen_tk  # referencia viva: sin
        # esto Python recolecta la imagen apenas termina la función y
        # el botón se queda en blanco.
        ids_bisel_extra = []
        bisel_claro, bisel_oscuro = [], []
    else:
        # Sin Pillow disponible: se cae al dibujo nativo del canvas de
        # siempre (con los escalones de toda la vida, pero funcionando
        # igual en todo lo demás).
        canvas.create_oval(cx - r + 2, cy - r + 3, cx + r + 2, cy + r + 3, fill=COLOR_SOMBRA, outline="")
        id_circulo = canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color_fondo, outline=_oscurecer_color(color_fondo, 70), width=2
        )
        id_gloss = canvas.create_oval(
            cx - r * 0.55, cy - r * 0.78, cx + r * 0.55, cy - r * 0.05,
            fill=_aclarar_color(color_fondo, 60), outline=""
        )
        id_sombra_int = canvas.create_arc(
            cx - r + 2, cy - r * 0.1, cx + r - 2, cy + r - 2,
            start=200, extent=140, fill=_oscurecer_color(color_fondo, 35), outline="", style="chord"
        )
        ids_bisel_extra = [id_gloss, id_sombra_int]
        bisel_claro, bisel_oscuro = [id_gloss], [id_sombra_int]

    # Íconos de mute (altavoz) y monitoreo (auriculares) como emoji de
    # texto, igual que en la versión original de la consola: más simple
    # y liviano que dibujarlos a mano vector por vector.
    tipo_icono = "texto"
    texto_id = canvas.create_text(
        cx, cy, text=texto,
        font=(FUENTE_ICONOS, max(9, min(fuente_tam, int(r * 0.80))), "bold"),
        fill="white"
    )
    ids_icono = [texto_id]

    def _click(_event):
        comando()

    for item in (id_circulo, *ids_bisel_extra, *ids_icono):
        canvas.tag_bind(item, "<Button-1>", _click)

    canvas.datos_boton = {
        "circulo": id_circulo,
        "texto": texto_id,
        "iconos": ids_icono,
        "tipo_icono": tipo_icono,
        "comando": comando,
        "usa_pillow": HAY_PILLOW,
        "lado": lado,
        "bisel_claro": bisel_claro,
        "bisel_oscuro": bisel_oscuro,
        "ranuras": [],
    }
    return canvas


def _actualizar_boton_circular(canvas, texto_nuevo=None, color_nuevo=None):
    datos = canvas.datos_boton
    if texto_nuevo is not None and datos.get("texto") is not None:
        canvas.itemconfig(datos["texto"], text=texto_nuevo)
    if color_nuevo is not None:
        if datos.get("usa_pillow"):
            imagen_tk = _renderizar_circulo_boton(datos["lado"], color_nuevo)
            canvas.itemconfig(datos["circulo"], image=imagen_tk)
            canvas.imagen_circulo_actual = imagen_tk
        else:
            canvas.itemconfig(datos["circulo"], fill=color_nuevo)
            claro = _aclarar_color(color_nuevo)
            oscuro = _oscurecer_color(color_nuevo)
            for iid in datos["bisel_claro"]:
                canvas.itemconfig(iid, fill=claro)
            for iid in datos["bisel_oscuro"]:
                canvas.itemconfig(iid, fill=oscuro)
        # Las almohadillas de los auriculares tienen una "ranura" pintada
        # del color de fondo del botón (para simular el corte del ícono
        # de referencia); si el color de fondo cambia (ej. al cambiar el
        # modo de escucha), hay que actualizar también esa ranura o
        # queda con el color viejo pegado encima.
        for iid in datos.get("ranuras", []):
            canvas.itemconfig(iid, fill=color_nuevo)


NUM_SEGMENTOS_VU = 24


def _dibujar_segmentos_led(canvas, ancho_segmentos, alto, bg_apagado="#10161f", offset_y=0):
    """Dibuja los 'segmentos' (LEDs) del medidor y devuelve la lista con
    sus ids y colores: cada LED tiene un color fijo según su posición
    (verde abajo, amarillo en el medio, rojo arriba), igual que en un
    mixer de verdad, y se prende o apaga según el nivel actual. También
    guarda un color gris equivalente por segmento (más claro arriba,
    más oscuro abajo) para cuando la fuente está atenuada (fuera de
    escena o muteada, ver _actualizar_estado_gris): ahí el medidor deja
    de usar los colores normales y pasa a tonos de gris, para que
    coincida con el resto del pad.

    'offset_y' corre todo el dibujo hacia abajo esa cantidad de
    píxeles, dejando ese margen libre arriba (y el mismo abajo, si el
    canvas se hizo más alto que 'alto' en esa medida) para que las
    etiquetas "0" y "-60" de los extremos no queden pegadas al borde
    del canvas y termine viéndose la mitad del número cortada."""
    segmentos = []
    gap = 2
    alto_seg = alto / NUM_SEGMENTOS_VU
    radio_led = max(1, min(3, (ancho_segmentos - 2) * 0.35))
    for i in range(NUM_SEGMENTOS_VU):
        y0 = i * alto_seg
        y1 = y0 + alto_seg - gap
        y_medio = (y0 + y1) / 2
        db_seg = -(y_medio / alto) * 60.0
        if db_seg >= -9:
            color_on = "#ff5d6c"
            color_on_gris = "#cbd2e6"
        elif db_seg >= -20:
            color_on = "#f2c464"
            color_on_gris = "#8e9ab3"
        else:
            color_on = "#2fd693"
            color_on_gris = "#6b7492"
        rect_id = _dibujar_rect_redondeado(
            canvas, 1, y0 + offset_y, ancho_segmentos - 1, y1 + offset_y, radio=radio_led, fill=bg_apagado, outline=""
        )
        segmentos.append({"id": rect_id, "db": db_seg, "color_on": color_on, "color_on_gris": color_on_gris})
    return segmentos


COLOR_LED_SATURADO = "#ff2441"
# Equivalente en gris del color de saturación de arriba: se usa cuando
# la fuente está atenuada (fuera de escena / muteada) y satura, para
# marcarlo igual que en color pero sin romper el "modo gris" -nada de
# rojo mezclado con la tarjeta gris, todo el tramo encendido pasa a
# este gris bien claro, más claro que cualquiera de los grises
# normales del degradado (ver color_on_gris en _dibujar_segmentos_led)
# para que la saturación siga notándose como un aviso distinto.
COLOR_LED_SATURADO_GRIS = "#f2f4fb"


def _actualizar_medidor_led(canvas, segmentos, db_visual, atenuado=False, bg_apagado="#10161f", saturado=False):
    """Prende los LEDs hasta el nivel actual. Si 'saturado' es True (la
    fuente llegó a 0 dB / está saturando), TODO el tramo encendido se
    pinta de golpe con el color de saturación, sin importar en qué
    posición esté cada LED (verde/amarillo/rojo normal) — igual que el
    aviso de clipping de OBS — en vez de mantener el degradado de
    colores de siempre. Con la fuente atenuada (gris) se usa el
    equivalente en gris (COLOR_LED_SATURADO_GRIS) en vez del rojo
    normal, para que el aviso de saturación siga funcionando igual
    -mismo criterio, mismo "todo encendido de un color"- sin romper el
    modo gris con un rojo de por medio. Sólo aplica mientras
    'saturado' esté activo; apenas se apaga, el medidor vuelve a sus
    colores normales."""
    for seg in segmentos:
        if db_visual >= seg["db"]:
            if saturado:
                color = COLOR_LED_SATURADO_GRIS if atenuado else COLOR_LED_SATURADO
            else:
                color = seg["color_on_gris"] if atenuado else seg["color_on"]
        else:
            color = bg_apagado
        canvas.itemconfig(seg["id"], fill=color)



ALTO_CANAL = 230
ANCHO_BARRA_VU = 20
MARCAS_DB = [0, -10, -20, -30, -40, -50, -60]


def factor_escala_ui():
    """Cuánto hay que escalar la interfaz según el tamaño ACTUAL de la
    ventana, comparado con el tamaño de referencia. Si la ventana es más
    chica que la referencia, todo se achica para que entre en pantalla;
    si es más grande, todo crece para aprovechar el espacio. Se limita
    entre ESCALA_MINIMA y ESCALA_MAXIMA para que nunca quede ilegible ni
    absurdamente gigante."""
    try:
        ancho = ventana.winfo_width()
        alto = ventana.winfo_height()
    except Exception:
        return 1.0

    if ancho <= 1 or alto <= 1:
        return 1.0

    factor = min(ancho / ANCHO_VENTANA_REFERENCIA, alto / ALTO_VENTANA_REFERENCIA) * ESCALA_BASE
    return max(ESCALA_MINIMA, min(ESCALA_MAXIMA, factor))


def medida_actual():
    """Medida del tamaño de ícono elegido (Chico/Mediano/Grande), ya
    escalada según el tamaño actual de la ventana. Los pads del
    soundboard siempre quedan cuadrados: se escala el mismo valor base
    para ancho y alto, así nunca se desalinean entre sí."""
    base = TAMANOS_ICONO[tamano_icono_actual]
    f = factor_escala_ui()
    return {
                                                                     
                                                                    
                                                                  
                                                                      
                                                                    
                                                 
        "diametro_boton": max(24, round(base["diametro_boton"] * f)),
        "fuente_boton": max(12, round(base["fuente_boton"] * f)),
        "pad_ancho": max(70, round(base["pad_ancho"] * f)),
        "pad_alto": max(70, round(base["pad_alto"] * f)),
        "diametro_pad_chico": max(16, round(base["diametro_pad_chico"] * f)),
        "fuente_pad_icono": max(12, round(base["fuente_pad_icono"] * f)),
        "fuente_pad_texto": max(8, round(base["fuente_pad_texto"] * f)),
        "fuente_ancho": max(86, round(base["fuente_ancho"] * f)),
        "fuente_alto": max(270, round(base["fuente_alto"] * f)),
        "fuente_alto_canal": max(72, round(base["fuente_alto_canal"] * f)),
        "fuente_ancho_vu": max(8, round(base["fuente_ancho_vu"] * f)),
        "fuente_nombre": max(10, round(base["fuente_nombre"] * f)),
    }


def _y_para_db(db, alto=ALTO_CANAL):
    """Convierte un valor en dB (0 a -60) a una coordenada Y del canvas,
    con 0 dB arriba de todo y -60 dB abajo de todo, igual que en OBS."""
    db = max(-60, min(0, db))
    return (-db / 60.0) * alto


def _ancho_preferido_fuente():
    """Ancho 'de catálogo' (mínimo) de una tarjeta de fuente, según el
    tamaño de ícono elegido (Chico/Mediano/Grande) y el factor de escala
    de ventana actual, SIN estirar todavía para llenar la fila."""
    return medida_actual()["fuente_ancho"]


def _ancho_contenedor_fuente():
    """Ancho aproximado (con su padding) de una tarjeta de fuente, al
    factor de escala actual. Se usa para calcular cuántas entran por
    fila, igual que se hace con los pads del soundboard."""
    return _ancho_preferido_fuente() + 12                  


# Margen (en píxeles) de "colchón" antes de sacarle una columna entera a
# la grilla de fuentes. Sin este margen, cualquier achique mínimo del
# panel (por ejemplo, arrastrar apenas un poco el divisor hacia el lado
# de las fuentes) hacía caer de golpe la última columna entera a la fila
# de abajo. Con el margen, un achique chico simplemente angosta un poco
# las tarjetas (ver _ancho_celda_fuentes) en vez de reacomodar todo; sólo
# se saca una columna cuando el espacio que falta es "de verdad" y no
# "apenas un poco".
MARGEN_HISTERESIS_COLUMNAS = 40


def _columnas_disponibles_fuentes():
    """Cuántas tarjetas de fuente entran por fila en el ancho actual del
    panel. A diferencia del soundboard (ver _columnas_disponibles), acá
    NO se usa ningún margen de tolerancia: las tarjetas de fuente son de
    tamaño FIJO (ver _ancho_celda_fuentes, ya no se achican para entrar),
    así que si una columna entera no entra, tiene que bajar de fila sí o
    sí -sostenerla "por las dudas" sólo hace que quede una tarjeta
    cortada en el borde del panel en vez de acomodarse en la fila de
    abajo-."""
    ancho_disponible = canvas.winfo_width()
    ancho_celda_con_padding = _ancho_contenedor_fuente()
    if ancho_disponible <= 1 or ancho_celda_con_padding <= 0:
        return columnas_fuentes
    return max(1, ancho_disponible // ancho_celda_con_padding)


def _ancho_celda_fuentes():
    """Ancho de cada tarjeta de fuente: SIEMPRE el tamaño 'de catálogo'
    para el tamaño de ícono elegido (Chico/Mediano/Grande) y el factor
    de escala de ventana actual (ver _ancho_preferido_fuente). Las
    tarjetas ya NO se estiran ni se achican para llenar el ancho
    disponible del panel -antes sí lo hacían, lo que hacía que cada
    fuente pareciera "redimensionarse sola" al mover el borde de la
    ventana-. Lo único que cambia con el ancho disponible es CUÁNTAS
    tarjetas entran por fila (ver _columnas_disponibles_fuentes): si
    deja de entrar una columna entera, esa tarjeta pasa a la fila de
    abajo tal cual estaba, sin cambiar de tamaño."""
    return _ancho_preferido_fuente()


def _fila_col_fuente(nombre):
    """Posición (fila, columna) de una fuente dentro de la grilla, según
    el orden en que fue apareciendo y la cantidad de columnas que entran
    en el ancho actual. Las fuentes se acomodan en varias filas, en vez
    de una sola fila larga con scroll horizontal, para que entren todas
    en pantalla."""
    if nombre not in orden_fuentes:
        orden_fuentes.append(nombre)
    idx = orden_fuentes.index(nombre)
    columnas = max(1, columnas_fuentes)
    return idx // columnas, idx % columnas


_ultimo_ancho_celda_fuentes = {"valor": None}


def _reajustar_fuente_nombre_tarjeta(nombre, ancho_disponible):
    """Recalcula el tamaño de letra del nombre de 'nombre' para el nuevo
    ancho disponible (se llama cuando la tarjeta cambia de tamaño), con
    la misma lógica de achicar-antes-que-cortar que usa crear_fader_fuente.
    También ajusta el alto de la cabecera si el nombre pasa a necesitar
    tres renglones o más (o deja de necesitarlos), para que el título
    quede centrado verticalmente en vez de recortado."""
    widgets = fuentes.get(nombre)
    if not widgets:
        return
    nombre_visible = widgets.get("nombre_visible", nombre)
    tam_max = widgets.get("fuente_tam_max", medida_actual()["fuente_nombre"])
    tam_min = widgets.get("fuente_tam_min", max(7, tam_max - 5))
    alto_cabecera_base = widgets.get("alto_cabecera_base", 26)
    ancho_texto = max(70, ancho_disponible)

    tam, ancho_wrap_texto, lineas_nombre = _ajustar_texto_tarjeta(
        nombre_visible, FUENTE_TITULO, tam_max, ancho_texto, tam_min
    )

    cabecera_canal = widgets["cabecera"]
    fuente_nueva = (FUENTE_TITULO, tam, "bold")
    cabecera_canal.itemconfig(widgets["id_texto_nombre"], font=fuente_nueva, width=ancho_wrap_texto)
    cabecera_canal.itemconfig(widgets["id_texto_sombra"], font=fuente_nueva, width=ancho_wrap_texto)

    if lineas_nombre >= 3:
        fuente_real = tkfont.Font(family=FUENTE_TITULO, size=tam, weight="bold")
        alto_cabecera = max(alto_cabecera_base, fuente_real.metrics("linespace") * lineas_nombre + 16)
    else:
        alto_cabecera = alto_cabecera_base
    if int(cabecera_canal.cget("height")) != int(alto_cabecera):
        cabecera_canal.config(height=alto_cabecera)
        redibujar = widgets.get("redibujar_cabecera")
        if redibujar:
            redibujar()


def _reubicar_fuentes(forzar=False):
    """Reacomoda (sin recrear) las tarjetas de fuente ya existentes según
    la cantidad de columnas actual. No recrear evita perder el estado
    del fader mientras el usuario lo está arrastrando.

    El ancho de cada tarjeta (ver _ancho_celda_fuentes) es siempre el
    tamaño 'de catálogo': ya no se estira ni se achica según el espacio
    disponible. Lo que SÍ puede cambiar con el ancho del panel es la
    cantidad de columnas (ver _columnas_disponibles_fuentes); cuando
    eso pasa, esta función reposiciona las tarjetas en su nueva fila/
    columna, pero ninguna tarjeta cambia de tamaño por eso."""
    columnas = max(1, columnas_fuentes)
    ancho_celda = _ancho_celda_fuentes()
    ancho_sin_cambios = (not forzar) and (_ultimo_ancho_celda_fuentes["valor"] == ancho_celda)
    _ultimo_ancho_celda_fuentes["valor"] = ancho_celda
    for idx, nombre in enumerate(orden_fuentes):
        if nombre not in fuentes:
            continue
        fila = idx // columnas
        col = idx % columnas
        tarjeta_sombra = fuentes[nombre]["tarjeta_sombra"]
        contenedor = fuentes[nombre]["contenedor"]
        if not ancho_sin_cambios:
            tarjeta_sombra.config(width=ancho_celda + 18)
            tarjeta_sombra.delete("sombra_difusa")
            _dibujar_sombra_difusa(
                tarjeta_sombra, 6, 6, ancho_celda + 4, contenedor.winfo_reqheight() + 4,
                radio=10, capas=3, color_fondo_panel="#131825"
            )
            contenedor.config(width=ancho_celda)
            _reajustar_fuente_nombre_tarjeta(nombre, ancho_celda - 24)
        # Reposicionar en la grilla es barato (no crea nada nuevo), así
        # que se hace siempre, haya cambiado el ancho o no: es lo que
        # de verdad mueve una tarjeta a otra fila/columna.
        tarjeta_sombra.grid(row=fila, column=col, padx=6, pady=6, sticky="n")
    actualizar_scroll()


_trabajo_redimension_fuentes = {"id": None}


def _al_redimensionar_fuentes(event=None):
    """Recalcula cuántas columnas entran en el ancho actual del panel de
    fuentes y, si cambió, reacomoda las tarjetas (sin recrearlas). Se
    espera un toque de calma antes de reacomodar (igual que con el
    redimensionado de toda la ventana): reaccionar en CADA evento de
    Configure mientras se arrastra el borde es lo que hacía que las
    tarjetas se vieran saltando/superpuestas a mitad de camino."""
    if _trabajo_redimension_fuentes["id"] is not None:
        ventana.after_cancel(_trabajo_redimension_fuentes["id"])
    # Antes 90ms, después 40ms; ahora 16ms (aprox. un cuadro de
    # pantalla a 60Hz): es el mínimo con sentido, porque durante un
    # arrastre real los eventos de Configure ya vienen espaciados por
    # el refresco de pantalla, así que bajar más no cambia nada salvo
    # hacer más trabajo de más.
    _trabajo_redimension_fuentes["id"] = ventana.after(16, _aplicar_redimension_fuentes)


def _aplicar_redimension_fuentes():
    global columnas_fuentes
    _trabajo_redimension_fuentes["id"] = None
    if _reconstruccion_en_curso["activa"]:
        # Hay una reconstrucción completa de la interfaz en curso (ver
        # _aplicar_redimension): no tocar la grilla ahora, la
        # reconstrucción ya va a dejarla acomodada al tamaño final.
        return
    nuevas_columnas = _columnas_disponibles_fuentes()
    if nuevas_columnas != columnas_fuentes:
        columnas_fuentes = nuevas_columnas
    # Se reacomoda siempre, no sólo cuando cambia la cantidad de
    # columnas: aunque siga entrando la misma cantidad por fila, el
    # ancho de cada tarjeta se recalcula según el espacio disponible
    # ahora mismo (ver _reubicar_fuentes), así ninguna tarjeta queda
    # sobresaliendo del borde visible del panel cuando éste se achica o
    # agranda un poco. _reubicar_fuentes ya evita el trabajo de más si
    # el ancho no cambió de verdad.
    _reubicar_fuentes()


def _envolver_texto_por_ancho(texto, fuente, ancho_max):
    """Parte 'texto' en líneas que entran en 'ancho_max' píxeles con
    'fuente', cortando por palabra completa (nunca a mitad de palabra)."""
    palabras = texto.split()
    if not palabras:
        return [texto]
    lineas = []
    actual = palabras[0]
    for palabra in palabras[1:]:
        candidato = f"{actual} {palabra}"
        if fuente.measure(candidato) <= ancho_max:
            actual = candidato
        else:
            lineas.append(actual)
            actual = palabra
    lineas.append(actual)
    return lineas


def _ajustar_texto_tarjeta(texto, familia, tam_max, ancho_max, tam_min=8, max_lineas=2, peso="bold"):
    """Decide con qué tamaño de letra y si hace falta envolver el nombre
    de una fuente para que entre en el ancho disponible. Devuelve
    (tamaño, ancho_de_envoltura, cantidad_de_renglones).

    Para cada tamaño (de grande a chico) primero se prueba una sola
    línea y, si no entra, se prueba envolviendo por palabra: apenas
    alguna de las dos formas entra, se usa ESE tamaño, así un nombre de
    dos palabras puede quedar tan grande como uno de una sola palabra
    que sí entra entero. Sólo se achica la letra cuando ni envolviendo
    entra en 'max_lineas' renglones; si ni al tamaño mínimo alcanza
    (nombres larguísimos), se devuelve igual la cantidad real de
    renglones que hacen falta, para que quien llama pueda agrandar el
    alto disponible en vez de recortar el texto."""
    tam_max = max(tam_min, int(tam_max))
    for tam in range(tam_max, tam_min - 1, -1):
        fuente = tkfont.Font(family=familia, size=tam, weight=peso)
        if fuente.measure(texto) <= ancho_max:
            return tam, 0, 1
        lineas = _envolver_texto_por_ancho(texto, fuente, ancho_max)
        if len(lineas) <= max_lineas and all(fuente.measure(l) <= ancho_max for l in lineas):
            return tam, ancho_max, len(lineas)
    fuente_min = tkfont.Font(family=familia, size=tam_min, weight=peso)
    lineas_min = _envolver_texto_por_ancho(texto, fuente_min, ancho_max)
    return tam_min, ancho_max, max(1, len(lineas_min))


def crear_fader_fuente(nombre, vol_db, muted, tipo_monitor, nombre_visible=None):

    if nombre_visible is None:
        nombre_visible = nombre

    medida_icono = medida_actual()
    f = factor_escala_ui()
    alto_canal = medida_icono["fuente_alto_canal"]
    ancho_barra_vu = medida_icono["fuente_ancho_vu"]
    ancho_contenedor = _ancho_celda_fuentes()
    alto_contenedor = medida_icono["fuente_alto"]

    color_etiqueta = colores_fuentes.get(nombre)
    color_cabecera_base = color_etiqueta or "#3d4d66"
    color_cuerpo = _oscurecer_color_pct(color_etiqueta, 0.42) if color_etiqueta else "#202633"
    color_meta = _oscurecer_color_pct(color_etiqueta, 0.30) if color_etiqueta else "#141a26"

    es_principal_inicial = nombre in fuentes_principales
    color_borde = COLOR_BORDE_PRINCIPAL if es_principal_inicial else "#0e1219"
    grosor_borde = 3 if es_principal_inicial else 2

    tarjeta_sombra = tk.Canvas(
        panel_fuentes, bg="#10141b",
        width=ancho_contenedor + 18, height=alto_contenedor + 18,
        highlightthickness=0
    )
    fila, col = _fila_col_fuente(nombre)
    tarjeta_sombra.grid(row=fila, column=col, padx=6, pady=6, sticky="n")
    tarjeta_sombra.grid_propagate(False)

    _dibujar_sombra_difusa(
        tarjeta_sombra, 6, 6, ancho_contenedor + 4, alto_contenedor + 4,
        radio=10, capas=3, color_fondo_panel="#131825"
    )

    contenedor = tk.Frame(
        tarjeta_sombra,
        bg=color_cuerpo,
        width=ancho_contenedor,
        height=alto_contenedor,
        highlightbackground=color_borde,
        highlightthickness=grosor_borde
    )
    contenedor.pack_propagate(False)
    tarjeta_sombra.create_window(6, 6, anchor="nw", window=contenedor)

    ancho_texto = max(70, ancho_contenedor - 24)
    fuente_nombre_tam_max = medida_icono["fuente_nombre"]
    fuente_nombre_tam_min = max(7, fuente_nombre_tam_max - 5)

    # El nombre se achica automáticamente sólo cuando hace falta: primero
    # se intenta en una sola línea, después envolviendo por palabra (sin
    # cortar a mitad de palabra), y recién si ni así entra se lo achica
    # (ver _ajustar_texto_tarjeta).
    fuente_nombre_tam, ancho_wrap_texto, lineas_nombre = _ajustar_texto_tarjeta(
        nombre_visible, FUENTE_TITULO, fuente_nombre_tam_max, ancho_texto, fuente_nombre_tam_min
    )

    # La altura de la cabecera se calcula con el tamaño MÁXIMO y para 2
    # renglones (aunque el texto termine dibujándose más chico), así
    # todas las tarjetas quedan con la misma altura de cabecera sin
    # importar cuán largo sea el nombre de cada una. La excepción es
    # cuando el nombre necesita TRES renglones o más (nombres muy
    # largos que ni achicados entran en dos): ahí la cabecera crece lo
    # necesario para mostrarlos enteros, y como el texto sigue anclado
    # al centro del canvas, queda centrado verticalmente en vez de
    # recortado contra el alto fijo de siempre.
    _fuente_medicion = tkfont.Font(family=FUENTE_TITULO, size=fuente_nombre_tam_max, weight="bold")
    alto_cabecera_base = max(26, _fuente_medicion.metrics("linespace") * 2 + 16)
    if lineas_nombre >= 3:
        _fuente_real = tkfont.Font(family=FUENTE_TITULO, size=fuente_nombre_tam, weight="bold")
        alto_cabecera = max(alto_cabecera_base, _fuente_real.metrics("linespace") * lineas_nombre + 16)
    else:
        alto_cabecera = alto_cabecera_base
    cabecera_canal = tk.Canvas(contenedor, height=alto_cabecera, highlightthickness=0, bg=color_cabecera_base, cursor="fleur")
    cabecera_canal.pack(fill="x")
    cabecera_canal.pack_propagate(False)
    cabecera_canal.datos_color_actual = color_cabecera_base

    id_texto_sombra = cabecera_canal.create_text(
        0, 0, text=nombre_visible, fill=_oscurecer_color(color_cabecera_base, 70),
        font=(FUENTE_TITULO, fuente_nombre_tam, "bold"),
        width=ancho_wrap_texto, justify="center", tags=("texto_sombra",)
    )
    id_texto_nombre = cabecera_canal.create_text(
        0, 0, text=nombre_visible, fill="white",
        font=(FUENTE_TITULO, fuente_nombre_tam, "bold"),
        width=ancho_wrap_texto, justify="center", tags=("texto_nombre",)
    )

    def _redibujar_gradiente_cabecera_fuente(event=None, cv=cabecera_canal, id_sombra=id_texto_sombra, id_nombre=id_texto_nombre):
        ancho_cab = cv.winfo_width()
        alto_cab = cv.winfo_height()
        if ancho_cab < 2 or alto_cab < 2:
            return
        color_base = cv.datos_color_actual
        cv.delete("degradado_cabecera")
        color_claro = _aclarar_color(color_base, 40)
        color_oscuro = _oscurecer_color(color_base, 15)
        ids = _gradiente_vertical(cv, 0, 0, ancho_cab, alto_cab, color_claro, color_oscuro, pasos=min(14, max(2, alto_cab)))
        for iid in ids:
            cv.itemconfig(iid, tags=("degradado_cabecera",))
        cv.tag_lower("degradado_cabecera")
        cx, cy = ancho_cab / 2, alto_cab / 2
        cv.coords(id_sombra, cx + 1, cy + 2)
        cv.coords(id_nombre, cx, cy + 1)

    cabecera_canal.bind("<Configure>", _redibujar_gradiente_cabecera_fuente)
    # Doble clic sobre el título = renombrar. Clic derecho en cualquier
    # parte de la tarjeta = menú con el resto de las acciones (color de
    # etiqueta, filtros, marcar como principal, renombrar), que antes
    # eran botones sueltos siempre a la vista. Clic izquierdo sostenido
    # y arrastrado sobre la cabecera = mover la tarjeta de lugar.
    cabecera_canal.bind("<Double-Button-1>", lambda e: _iniciar_renombrar_fuente(nombre))
    cabecera_canal.bind("<ButtonPress-1>", lambda e: _iniciar_arrastre_fuente(nombre, e))
    cabecera_canal.bind("<B1-Motion>", lambda e: _mover_arrastre_fuente(nombre, e))
    cabecera_canal.bind("<ButtonRelease-1>", lambda e: _soltar_arrastre_fuente(nombre, e))
    cabecera_canal.bind("<Button-3>", lambda e: _abrir_menu_contextual_fuente(nombre, e))

    # Tira fina y puramente decorativa (ya no tiene botones encima: ver
    # comentario más arriba, todo eso ahora vive en el menú contextual).
    fila_meta = tk.Frame(contenedor, bg=color_meta, height=10)
    fila_meta.pack(fill="x")
    fila_meta.pack_propagate(False)
    fila_meta.bind("<Button-3>", lambda e: _abrir_menu_contextual_fuente(nombre, e))

    # El chasis entero también reconoce el clic derecho, para no
    # obligar a apuntarle justo a la cabecera o la tira fina.
    contenedor.nombre_fuente = nombre
    contenedor.bind("<Button-3>", lambda e: _abrir_menu_contextual_fuente(nombre, e))

    etiqueta_db = tk.Label(
        contenedor,
        text="SILENCIO" if vol_db <= UMBRAL_SILENCIO else f"{vol_db:.1f} dB",
        bg=color_cuerpo,
        fg="#2fd693",
        font=(FUENTE_UI, 10, "bold")
    )
    etiqueta_db.pack(pady=(5, 2))


    fila_vertical = tk.Frame(contenedor, bg=color_cuerpo)
    fila_vertical.pack(pady=1)

    # Margen vertical para que las etiquetas "0" y "-60" (arriba y abajo
    # del todo) tengan lugar completo para dibujarse: antes el canvas
    # medía exactamente 'alto_canal' y el texto se centraba justo en el
    # borde (y=0 e y=alto_canal), así que la mitad de esos números
    # quedaba recortada por el borde del canvas. Se calcula con las
    # métricas reales de la fuente (no un número fijo) para que
    # funcione bien sea cual sea el tamaño de letra o la plataforma.
    _fuente_marcas_db = tkfont.Font(family=FUENTE_UI, size=6)
    margen_marcas_db = math.ceil(_fuente_marcas_db.metrics("linespace") / 2) + 1

    vu_canvas = tk.Canvas(
        fila_vertical, width=ancho_barra_vu + 16, height=alto_canal + margen_marcas_db * 2,
        bg="#0e1219", highlightthickness=0
    )
    vu_canvas.pack(side="left", anchor="n")

    vu_segmentos = _dibujar_segmentos_led(vu_canvas, ancho_barra_vu, alto_canal, offset_y=margen_marcas_db)

    for marca in MARCAS_DB:
        y = _y_para_db(marca, alto_canal) + margen_marcas_db
        vu_canvas.create_text(
            ancho_barra_vu + 3, y, text=str(marca),
            fill="#79859f", font=(FUENTE_UI, 6), anchor="w"
        )

    escala = tk.Scale(
        fila_vertical,
        from_=0,
        to=-60,
        resolution=0.5,
        orient="vertical",
        length=alto_canal,
        width=14,
        sliderlength=20,
        showvalue=False,
        bg="#2a3243",
        fg="white",
        troughcolor="#141a26",
        highlightthickness=0,
        activebackground="#4fe3ae"
    )
    escala.set(vol_db)
    escala.pack(side="left", padx=(4, 0), pady=(margen_marcas_db, 0), anchor="n")

    def cambiar_volumen(valor):
        if not conectado:
            return
        try:
            db = float(valor)
            if db <= UMBRAL_SILENCIO:
                cliente_obs.set_input_volume(nombre, vol_mul=0)
                etiqueta_db.config(text="SILENCIO", fg="#828da6")
            else:
                cliente_obs.set_input_volume(nombre, vol_db=db)
                etiqueta_db.config(text=f"{db:.1f} dB", fg="#2fd693")
        except Exception as e:
            print(f"Error cambiando volumen de {nombre}: {e}")

    escala.config(command=cambiar_volumen)

    escala.bind("<ButtonPress-1>", lambda e: fuentes[nombre].__setitem__("arrastrando", True))
    escala.bind("<ButtonRelease-1>", lambda e: fuentes[nombre].__setitem__("arrastrando", False))


    fila_iconos = tk.Frame(contenedor, bg=color_cuerpo)
    fila_iconos.pack(pady=(5, 5))

    boton_mute = _crear_boton_circular(
        fila_iconos,
        "🔇" if muted else "🔊",
        medida_icono["diametro_boton"],
        medida_icono["fuente_boton"],
        "#ff5567" if muted else "#394151",
        lambda: cambiar_mute(nombre)
    )
    boton_mute.pack(side="left", padx=6)

    boton_monitor = _crear_boton_circular(
        fila_iconos,
        "🎧",
        medida_icono["diametro_boton"],
        medida_icono["fuente_boton"],
        COLORES_MONITOREO.get(tipo_monitor, "#394151"),
        lambda: cambiar_monitor(nombre)
    )
    boton_monitor.pack(side="left", padx=6)

    fuentes[nombre] = {
        "contenedor": contenedor,
        "tarjeta_sombra": tarjeta_sombra,
        "cabecera": cabecera_canal,
        "id_texto_nombre": id_texto_nombre,
        "id_texto_sombra": id_texto_sombra,
        "redibujar_cabecera": _redibujar_gradiente_cabecera_fuente,
        "fila_meta": fila_meta,
        "fila_vertical": fila_vertical,
        "fila_iconos": fila_iconos,
        "fader": escala,
        "db": etiqueta_db,
        "mute": boton_mute,
        "monitor": boton_monitor,
        "vu_canvas": vu_canvas,
        "vu_segmentos": vu_segmentos,
        "vu_alto": alto_canal,
        "vu_visual_db": -60.0,                                                              
        "muted": muted,
        "tipo_monitor": tipo_monitor,
        "nombre_visible": nombre_visible,
        "fuente_tam_max": fuente_nombre_tam_max,
        "fuente_tam_min": fuente_nombre_tam_min,
        "alto_cabecera_base": alto_cabecera_base,
        "arrastrando": False,
        "en_escena": True,
    }

    _actualizar_estado_gris(nombre)


def _actualizar_estado_gris(nombre):
    """Pinta la tarjeta de 'nombre' con un tono gris claro -aplicado a
    TODO el chasis, con el mismo mecanismo que una etiqueta de color
    (ver _actualizar_estado_gris más abajo y crear_fader_fuente)- si
    esa fuente NO está en la escena que está al aire ahora mismo (y no
    es una fuente marcada como 'principal', que siempre cuenta como
    activa), o si está muteada. Antes esto sólo oscurecía el cartel de
    arriba, una señal visual pobre; ahora se nota el pad entero, y
    también el medidor VU pasa a tonos de gris (ver
    actualizar_vu_meters_ui). El resto de los controles (fader, mute,
    filtros) siguen funcionando igual: el gris es sólo para que de un
    vistazo se note cuál fuente está realmente sonando en el programa y
    cuál no."""
    widgets = fuentes.get(nombre)
    if not widgets:
        return

    es_principal = nombre in fuentes_principales
    muted = widgets.get("muted", False)
    en_escena = es_principal or (not escena_actual_obtenida) or (nombre in escena_actual_nombres)
    widgets["en_escena"] = en_escena

    atenuado = muted or not en_escena
    widgets["atenuado"] = atenuado

    color_etiqueta = colores_fuentes.get(nombre)
    if atenuado:
        color_cabecera = COLOR_GRIS_ATENUADO
        color_texto = "#202633"
        color_cuerpo = _oscurecer_color_pct(COLOR_GRIS_ATENUADO, 0.42)
        color_meta = _oscurecer_color_pct(COLOR_GRIS_ATENUADO, 0.30)
    else:
        color_cabecera = color_etiqueta or "#3d4d66"
        color_texto = "white"
        color_cuerpo = _oscurecer_color_pct(color_etiqueta, 0.42) if color_etiqueta else "#202633"
        color_meta = _oscurecer_color_pct(color_etiqueta, 0.30) if color_etiqueta else "#141a26"

    widgets["cabecera"].config(bg=color_cabecera)
    widgets["cabecera"].datos_color_actual = color_cabecera
    widgets["cabecera"].itemconfig(widgets["id_texto_nombre"], fill=color_texto)
    widgets["cabecera"].itemconfig(widgets["id_texto_sombra"], fill=_oscurecer_color(color_cabecera, 70))
    widgets["redibujar_cabecera"]()
    for hijo in widgets["cabecera"].winfo_children():
        if isinstance(hijo, tk.Canvas):
            hijo.config(bg=color_cabecera)

    widgets["contenedor"].config(
        bg=color_cuerpo,
        highlightbackground=(COLOR_BORDE_PRINCIPAL if es_principal else "#0e1219"),
        highlightthickness=(3 if es_principal else 2)
    )
    widgets["db"].config(bg=color_cuerpo)
    widgets["fila_vertical"].config(bg=color_cuerpo)
    widgets["fila_iconos"].config(bg=color_cuerpo)
    for hijo in widgets["fila_iconos"].winfo_children():
        if isinstance(hijo, tk.Canvas):
            hijo.config(bg=color_cuerpo)

    widgets["fila_meta"].config(bg=color_meta)


def _abrir_menu_contextual_fuente(nombre, event):
    """Menú de clic derecho de una tarjeta de fuente: agrupa acá todo lo
    que antes eran botones sueltos siempre visibles en la tarjeta
    (etiqueta de color, filtros, marcar como principal, renombrar), para
    que la tarjeta se vea limpia y esas acciones aparezcan sólo cuando
    se las pide."""
    if nombre not in fuentes:
        return
    es_principal = nombre in fuentes_principales

    menu = tk.Menu(ventana, tearoff=0, bg="#151a24", fg="white", activebackground="#323b4c", activeforeground="white")
    menu.add_command(label="✏  Renombrar…", command=lambda: _iniciar_renombrar_fuente(nombre))
    menu.add_command(
        label=("☆  Quitar de principales" if es_principal else "★  Marcar como principal"),
        command=lambda: _alternar_principal(nombre)
    )
    menu.add_command(label="🎚  Filtros…", command=lambda: abrir_filtros(nombre))
    menu.add_separator()

    submenu_color = tk.Menu(menu, tearoff=0, bg="#151a24", fg="white", activebackground="#323b4c")
    submenu_color.add_command(label="Sin etiqueta", command=lambda: _asignar_color_fuente(nombre, None))
    submenu_color.add_separator()
    for color in PALETA_ETIQUETAS:
        if color is None:
            continue
        submenu_color.add_command(
            label="        ", background=color, activebackground=color,
            command=lambda c=color: _asignar_color_fuente(nombre, c)
        )
    menu.add_cascade(label="🏷  Color de etiqueta", menu=submenu_color)

    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


UMBRAL_ARRASTRE_PX = 6

_arrastre_fuente = {
    "nombre": None, "arrastrando": False,
    "x_inicio": 0, "y_inicio": 0, "destino_resaltado": None,
}


def _fuente_bajo_puntero(x_root, y_root):
    """Devuelve el nombre de la fuente cuya tarjeta está bajo el
    puntero (en coordenadas absolutas de pantalla), o None."""
    try:
        widget = ventana.winfo_containing(x_root, y_root)
    except Exception:
        return None
    while widget is not None:
        nombre = getattr(widget, "nombre_fuente", None)
        if nombre is not None:
            return nombre
        widget = widget.master
    return None


def _resaltar_destino_fuente(nombre_nuevo):
    anterior = _arrastre_fuente["destino_resaltado"]
    if anterior == nombre_nuevo:
        return
    if anterior is not None and anterior in fuentes:
        _actualizar_estado_gris(anterior)
    if nombre_nuevo is not None and nombre_nuevo in fuentes:
        fuentes[nombre_nuevo]["contenedor"].config(highlightbackground="#2fd693", highlightthickness=3)
    _arrastre_fuente["destino_resaltado"] = nombre_nuevo


def _limpiar_resaltado_fuentes():
    anterior = _arrastre_fuente["destino_resaltado"]
    if anterior is not None and anterior in fuentes:
        _actualizar_estado_gris(anterior)
    _arrastre_fuente["destino_resaltado"] = None


def _iniciar_arrastre_fuente(nombre, event):
    _arrastre_fuente["nombre"] = nombre
    _arrastre_fuente["arrastrando"] = False
    _arrastre_fuente["x_inicio"] = event.x_root
    _arrastre_fuente["y_inicio"] = event.y_root


def _mover_arrastre_fuente(nombre, event):
    datos = _arrastre_fuente
    if datos["nombre"] != nombre:
        return
    if not datos["arrastrando"]:
        dx = abs(event.x_root - datos["x_inicio"])
        dy = abs(event.y_root - datos["y_inicio"])
        if dx < UMBRAL_ARRASTRE_PX and dy < UMBRAL_ARRASTRE_PX:
            return
        datos["arrastrando"] = True
    _resaltar_destino_fuente(_fuente_bajo_puntero(event.x_root, event.y_root))


def _soltar_arrastre_fuente(nombre, event):
    datos = _arrastre_fuente
    if datos["nombre"] != nombre:
        return
    fue_arrastre = datos["arrastrando"]
    datos["nombre"] = None
    datos["arrastrando"] = False
    _limpiar_resaltado_fuentes()
    if not fue_arrastre:
        return
    destino = _fuente_bajo_puntero(event.x_root, event.y_root)
    if destino is None or destino == nombre:
        return
    _reordenar_fuente(nombre, destino)


def _reordenar_fuente(nombre_origen, nombre_destino):
    """Mueve 'nombre_origen' a la posición de 'nombre_destino' dentro de
    orden_fuentes (arrastrar y soltar para reordenar las tarjetas), y
    reacomoda la grilla y guarda el nuevo orden.

    Antes esto sólo funcionaba bien arrastrando de derecha a izquierda:
    al sacar el origen de la lista para reinsertarlo, si el origen
    estaba ANTES que el destino, todo lo que había entre medio (destino
    incluido) se corría un lugar hacia atrás, así que insertarlo "en la
    posición del destino" lo dejaba apenas un lugar más adelante en vez
    de en el lugar donde se soltó. Moviendo de izquierda a derecha hay
    que insertarlo DESPUÉS de la nueva posición del destino para que
    termine de verdad donde el usuario lo soltó."""
    if nombre_origen not in orden_fuentes or nombre_destino not in orden_fuentes:
        return
    idx_origen = orden_fuentes.index(nombre_origen)
    idx_destino_original = orden_fuentes.index(nombre_destino)
    if idx_origen == idx_destino_original:
        return
    moviendo_hacia_adelante = idx_origen < idx_destino_original
    orden_fuentes.remove(nombre_origen)
    idx_destino = orden_fuentes.index(nombre_destino)
    if moviendo_hacia_adelante:
        idx_destino += 1
    orden_fuentes.insert(idx_destino, nombre_origen)
    guardar_config_interfaz({"orden_fuentes": list(orden_fuentes)})
    _reubicar_fuentes()


def _asignar_color_fuente(nombre, color):
    if color is None:
        colores_fuentes.pop(nombre, None)
    else:
        colores_fuentes[nombre] = color
    guardar_config_interfaz({"colores_fuentes": colores_fuentes})
    _actualizar_estado_gris(nombre)


def _alternar_principal(nombre):
    """Marca/desmarca una fuente como 'principal'. Las principales son
    las ÚNICAS que este programa fuerza a crear y mantener activas en
    TODAS las escenas; el resto sólo se muestran tal cual estén."""
    if nombre in fuentes_principales:
        fuentes_principales.discard(nombre)
    else:
        fuentes_principales.add(nombre)
    guardar_config_interfaz({"fuentes_principales": sorted(fuentes_principales)})
    _actualizar_estado_gris(nombre)
    if nombre in fuentes_principales and conectado:
        threading.Thread(
            target=_asegurar_fuente_en_todas_las_escenas, args=(nombre,), daemon=True
        ).start()


def _asegurar_fuente_en_todas_las_escenas(nombre_fuente):
    """Se asegura de que 'nombre_fuente' esté presente Y ACTIVA en TODAS
    las escenas de OBS: si falta en alguna, se agrega; si está pero
    deshabilitada ('ojito' apagado), se habilita. Se usa tanto para la
    fuente interna de efectos como para las fuentes marcadas como
    'principales'. Serializada con _lock_sincronizar_escenas (ver más
    arriba) para que no se pueda ejecutar en paralelo con otra operación
    del mismo tipo y terminar creando la fuente dos veces en la misma
    escena."""
    if not conectado:
        return
    with _lock_sincronizar_escenas:
        try:
            escenas = [_valor(e, "scene_name", "sceneName") for e in cliente_obs.get_scene_list().scenes]
        except Exception as e:
            print(f"No se pudieron listar las escenas: {e}")
            return

        for escena in escenas:
            if not escena:
                continue
            try:
                items = cliente_obs.get_scene_item_list(escena).scene_items
                item_existente = None
                for it in items:
                    if _valor(it, "source_name", "sourceName") == nombre_fuente:
                        item_existente = it
                        break

                if item_existente is None:
                    cliente_obs.create_scene_item(escena, nombre_fuente, True)
                else:
                    habilitado = _valor(item_existente, "scene_item_enabled", "sceneItemEnabled")
                    if not habilitado:
                        item_id = _valor(item_existente, "scene_item_id", "sceneItemId")
                        if item_id is not None:
                            cliente_obs.set_scene_item_enabled(escena, item_id, True)
            except Exception as e:
                print(f"No se pudo asegurar '{nombre_fuente}' en la escena '{escena}': {e}")


def asegurar_fuentes_principales_en_todas_las_escenas():
    """Antes esto igualaba TODAS las fuentes de audio en TODAS las
    escenas (invasivo: tocaba escenas del usuario sin que lo pidiera).
    Ahora sólo se hace con las fuentes marcadas explícitamente como
    'principales' (ver _alternar_principal); el resto de las fuentes
    sólo se muestran (grises si no están en la escena activa)."""
    for nombre in list(fuentes_principales):
        _asegurar_fuente_en_todas_las_escenas(nombre)



# Filtros que le sirven a un sonidista para controlar audio: sólo estos
# se ofrecen al "Agregar filtro" (nada de croma, máscaras, LUTs, etc.,
# que son de video y no pintan en una consola de sonido). Cada entrada
# es (nombre a mostrar, ícono, lista de "kind" técnicos posibles que
# puede usar OBS para ese mismo filtro según la versión -algunos
# filtros cambiaron de implementación interna en versiones nuevas de
# OBS, manteniendo el mismo nombre visible-, en orden de preferencia).
# El nombre y el orden calcan el menú "+" de filtros de audio de OBS.
FILTROS_DE_SONIDO_PERMITIDOS = [
    ("Compresor",                    "🗜", ["compressor_filter"]),
    ("Compresor ascendente",         "📊", ["upward_compressor_filter"]),
    # El identificador real que usa OBS para este filtro es
    # "basic_eq_filter" (no "eq_filter": ese nombre lo usa sólo la
    # variable interna del código fuente de OBS, pero el "kind" que
    # informa OBS-WebSocket -y el que hay que usar para crearlo y para
    # reconocer sus ajustes- es "basic_eq_filter"). Se deja "eq_filter"
    # como alternativa por si alguna versión vieja de OBS lo reportara
    # con ese otro nombre.
    ("Ecualizador de 3 bandas",      "🎛", ["basic_eq_filter", "eq_filter"]),
    ("Eliminación de ruido",         "🔇", ["noise_suppress_filter_v2", "noise_suppress_filter"]),
    ("Expansor",                     "📈", ["expander_filter"]),
    ("Extensión VST 2.x",            "🔌", ["vst_filter"]),
    ("Ganancia",                     "🎚", ["gain_filter"]),
    ("Invertir polaridad",           "🔃", ["invert_polarity_filter"]),
    ("Limitador",                    "🚧", ["limiter_filter"]),
    ("Puerta anti-ruidos",           "🚪", ["noise_gate_filter"]),
    ("Retardo de Video (asíncrono)", "⏱", ["async_delay_filter"]),
]


def _filtros_de_sonido_disponibles(tipos_que_ofrece_obs):
    """Cruza el catálogo fijo de arriba con lo que esta instancia de OBS
    realmente tiene registrado (cliente_obs.get_source_filter_kind_list),
    para no ofrecer un filtro que esa versión de OBS no trae. Devuelve
    una lista de (nombre, ícono, kind_real_a_usar)."""
    disponibles = set(tipos_que_ofrece_obs)
    opciones = []
    for nombre, icono, kinds_posibles in FILTROS_DE_SONIDO_PERMITIDOS:
        kind_real = next((k for k in kinds_posibles if k in disponibles), None)
        if kind_real:
            opciones.append((nombre, icono, kind_real))
    return opciones


def _abrir_selector_nuevo_filtro(nombre_fuente, al_crear=None):
    """Ventana simple para elegir QUÉ filtro agregar, sólo con los
    filtros de audio que le sirven a un sonidista (ver
    FILTROS_DE_SONIDO_PERMITIDOS), con nombre entendible e ícono por
    tipo, tal como el menú "+" de la lista de filtros dentro de OBS. Al
    confirmar, crea el filtro en OBS con la configuración por defecto
    que trae ese tipo y, si se pasó 'al_crear', lo llama con el nombre
    final del filtro (para, por ejemplo, abrirle el editor de ajustes en
    el acto)."""
    if not conectado:
        return

    try:
        respuesta_tipos = cliente_obs.get_source_filter_kind_list()
        tipos = _valor(respuesta_tipos, "source_filter_kinds", "sourceFilterKinds") or []
    except Exception as e:
        messagebox.showerror(
            "Error",
            f"No se pudo obtener la lista de tipos de filtro disponibles.\n\n{e}"
        )
        return

    opciones = _filtros_de_sonido_disponibles(tipos)

    if not opciones:
        messagebox.showinfo(
            "Sin filtros disponibles",
            "Esta versión de OBS no informó ninguno de los filtros de audio esperados."
        )
        return

    nombre_por_kind = {kind: nombre for nombre, _icono, kind in opciones}
    icono_por_kind = {kind: icono for nombre, icono, kind in opciones}

    selector = tk.Toplevel(ventana)
    selector.title("Agregar filtro")
    selector.configure(bg="#10141b")
    selector.geometry("360x460")
    selector.transient(ventana)
    selector.grab_set()

    tk.Label(
        selector, text=f"Nuevo filtro para: {nombre_fuente}", bg="#10141b", fg="white",
        font=(FUENTE_UI, 11, "bold"), wraplength=330, justify="left"
    ).pack(anchor="w", padx=12, pady=(12, 2))

    tk.Label(
        selector, text="Elegí el tipo de filtro:", bg="#10141b", fg="#8e9ab3",
        font=(FUENTE_UI, 9)
    ).pack(anchor="w", padx=12, pady=(4, 4))

    marco_scroll = tk.Frame(selector, bg="#10141b")
    marco_scroll.pack(fill="both", expand=True, padx=12)

    canvas_tipos = tk.Canvas(marco_scroll, bg="#10141b", highlightthickness=0)
    scrollbar_tipos = ttk.Scrollbar(marco_scroll, orient="vertical", command=canvas_tipos.yview)
    marco_lista_tipos = tk.Frame(canvas_tipos, bg="#10141b")
    marco_lista_tipos.bind(
        "<Configure>", lambda e: canvas_tipos.configure(scrollregion=canvas_tipos.bbox("all"))
    )
    id_ventana_tipos = canvas_tipos.create_window((0, 0), window=marco_lista_tipos, anchor="nw")
    canvas_tipos.bind(
        "<Configure>", lambda e: canvas_tipos.itemconfig(id_ventana_tipos, width=e.width)
    )
    canvas_tipos.configure(yscrollcommand=scrollbar_tipos.set)
    canvas_tipos.pack(side="left", fill="both", expand=True)
    scrollbar_tipos.pack(side="right", fill="y")

    primer_kind = opciones[0][2]
    var_tipo_elegido = tk.StringVar(value=primer_kind)

    for nombre_amigable, icono, kind in opciones:
        tk.Radiobutton(
            marco_lista_tipos, text=f"{icono}  {nombre_amigable}", value=kind,
            variable=var_tipo_elegido, bg="#10141b", fg="white",
            activebackground="#1c2331", activeforeground="white",
            selectcolor="#1a202b", font=(FUENTE_UI, 10), anchor="w",
            justify="left", indicatoron=True, padx=6, pady=5
        ).pack(fill="x")

    marco_nombre = tk.Frame(selector, bg="#10141b")
    marco_nombre.pack(fill="x", padx=12, pady=(8, 4))
    tk.Label(
        marco_nombre, text="Nombre del filtro:", bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)
    ).pack(anchor="w")
    var_nombre_filtro = tk.StringVar(value=nombre_por_kind[primer_kind])
    entrada_nombre_filtro = tk.Entry(
        marco_nombre, textvariable=var_nombre_filtro, bg="#1a202b", fg="white",
        insertbackground="white", relief="flat", font=(FUENTE_UI, 9)
    )
    entrada_nombre_filtro.pack(fill="x", ipady=3)

    _ultimo_sugerido = {"texto": var_nombre_filtro.get()}

    def _al_cambiar_tipo(*_ignorar):
        # Si el usuario no tocó el nombre a mano (sigue siendo el nombre
        # sugerido para el tipo anterior), lo actualiza solo al nuevo
        # tipo elegido; si ya lo editó, lo respeta tal cual.
        sugerido_nuevo = nombre_por_kind[var_tipo_elegido.get()]
        if var_nombre_filtro.get().strip() == _ultimo_sugerido["texto"]:
            var_nombre_filtro.set(sugerido_nuevo)
        _ultimo_sugerido["texto"] = sugerido_nuevo

    var_tipo_elegido.trace_add("write", _al_cambiar_tipo)

    etiqueta_error = tk.Label(
        selector, text="", bg="#10141b", fg="#ff8a95", font=(FUENTE_UI, 8),
        wraplength=330, justify="left"
    )
    etiqueta_error.pack(fill="x", padx=12)

    def _confirmar():
        nombre_filtro = var_nombre_filtro.get().strip()
        if not nombre_filtro:
            etiqueta_error.config(text="Poné un nombre para el filtro.")
            return
        try:
            filtros_actuales = cliente_obs.get_source_filter_list(nombre_fuente).filters or []
        except Exception:
            filtros_actuales = []
        nombres_existentes = {
            _valor(f, "filter_name", "filterName") for f in filtros_actuales
        }
        nombre_final = nombre_filtro
        contador = 2
        while nombre_final in nombres_existentes:
            nombre_final = f"{nombre_filtro} {contador}"
            contador += 1

        try:
            cliente_obs.create_source_filter(nombre_fuente, nombre_final, var_tipo_elegido.get())
        except Exception as e:
            etiqueta_error.config(text=f"No se pudo crear el filtro.\n{e}")
            return

        selector.destroy()
        if al_crear:
            ventana.after(150, lambda: al_crear(nombre_final))

    marco_botones = tk.Frame(selector, bg="#10141b")
    marco_botones.pack(fill="x", padx=12, pady=10)

    tk.Button(
        marco_botones, text="Cancelar", command=selector.destroy,
        bg="#323b4c", fg="white", relief="flat", font=(FUENTE_UI, 9)
    ).pack(side="right", padx=(6, 0))

    tk.Button(
        marco_botones, text="Agregar", command=_confirmar,
        bg="#2fd693", fg="#131825", relief="flat", font=(FUENTE_UI, 9, "bold")
    ).pack(side="right", ipadx=8)

    entrada_nombre_filtro.focus_set()
    entrada_nombre_filtro.select_range(0, "end")
    entrada_nombre_filtro.bind("<Return>", lambda e: _confirmar())


def abrir_filtros(nombre):
    if not conectado:
        messagebox.showwarning("Sin conexión", "Conectate a OBS para ver los filtros.")
        return

    # Si ya había una ventana de "Filtros" abierta (de esta fuente o de
    # otra), se cierra antes de abrir la nueva -sin esto, cada vez que
    # se apretaba "Filtros" se apilaba una ventana más arriba de la
    # anterior, hasta llenar la pantalla de ventanas superpuestas-. Sólo
    # puede haber una a la vez.
    ventana_previa = _dialogo_filtros_abierto.get("ventana")
    if ventana_previa is not None:
        try:
            ventana_previa.destroy()
        except Exception:
            pass
        _dialogo_filtros_abierto["nombre"] = None
        _dialogo_filtros_abierto["refrescar"] = None
        _dialogo_filtros_abierto["ventana"] = None

    ventana_filtros = tk.Toplevel(ventana)
    ventana_filtros.title(f"Filtros — {nombre}")
    ventana_filtros.configure(bg="#10141b")
    ventana_filtros.geometry("400x440")

    tk.Label(
        ventana_filtros, text=nombre, bg="#10141b", fg="white",
        font=(FUENTE_UI, 11, "bold")
    ).pack(anchor="w", padx=10, pady=(10, 0))

    marco_lista = tk.Frame(ventana_filtros, bg="#10141b")
    marco_lista.pack(fill="both", expand=True, padx=10, pady=10)

    def cerrar():
        if _dialogo_filtros_abierto["nombre"] == nombre:
            _dialogo_filtros_abierto["nombre"] = None
            _dialogo_filtros_abierto["refrescar"] = None
            _dialogo_filtros_abierto["ventana"] = None
        ventana_filtros.destroy()

    ventana_filtros.protocol("WM_DELETE_WINDOW", cerrar)

    def refrescar():
        for w in marco_lista.winfo_children():
            w.destroy()
        try:
            filtros = cliente_obs.get_source_filter_list(nombre).filters
        except Exception as e:
            tk.Label(
                marco_lista, text=f"No se pudieron leer los filtros.\n\n{e}",
                bg="#10141b", fg="#ff8a95", wraplength=340, justify="left"
            ).pack(pady=20)
            return

        if not filtros:
            tk.Label(
                marco_lista, text="Esta fuente no tiene filtros.",
                bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)
            ).pack(pady=20)
            return

        for filtro in filtros:
            nombre_filtro = _valor(filtro, "filter_name", "filterName")
            tipo_filtro = _valor(filtro, "filter_kind", "filterKind")
            habilitado = _valor(filtro, "filter_enabled", "filterEnabled")

            fila = tk.Frame(marco_lista, bg="#1a202b", highlightbackground="#0e1219", highlightthickness=1)
            fila.pack(fill="x", pady=3)

            var_habilitado = tk.BooleanVar(value=bool(habilitado))

            def _alternar_filtro(nf=nombre_filtro, var=var_habilitado):
                try:
                    cliente_obs.set_source_filter_enabled(nombre, nf, var.get())
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo cambiar el filtro.\n\n{e}")
                    return
                _refrescar_ganancia_fuente_en_hilo(nombre)

            tk.Checkbutton(
                fila, variable=var_habilitado, command=_alternar_filtro,
                bg="#1a202b", activebackground="#222a38", selectcolor="#131825",
                fg="white", activeforeground="white"
            ).pack(side="left", padx=4)

            tk.Label(
                fila, text=f"{nombre_filtro}\n{tipo_filtro}", bg="#1a202b", fg="white",
                font=(FUENTE_UI, 9), justify="left", anchor="w"
            ).pack(side="left", padx=4, pady=4, fill="x", expand=True)

            tk.Button(
                fila, text="Ajustes", command=lambda nf=nombre_filtro: _abrir_editor_ajustes_filtro(nombre, nf),
                bg="#3d4d66", fg="white", relief="flat", font=(FUENTE_UI, 8)
            ).pack(side="right", padx=4)

            def _eliminar_filtro(nf=nombre_filtro):
                if messagebox.askyesno("Eliminar filtro", f"¿Eliminar el filtro '{nf}'?"):
                    try:
                        cliente_obs.remove_source_filter(nombre, nf)
                        refrescar()
                        _refrescar_ganancia_fuente_en_hilo(nombre)
                    except Exception as e:
                        messagebox.showerror("Error", f"No se pudo eliminar el filtro.\n\n{e}")

            tk.Button(
                fila, text="✕", command=_eliminar_filtro,
                bg="#ff5567", fg="white", relief="flat", font=(FUENTE_UI, 8)
            ).pack(side="right", padx=(4, 8))

    _dialogo_filtros_abierto["nombre"] = nombre
    _dialogo_filtros_abierto["refrescar"] = refrescar
    _dialogo_filtros_abierto["ventana"] = ventana_filtros

    def _al_crear_filtro_nuevo(nombre_filtro_creado):
        # Al crear un filtro desde ACÁ, OBS de todas formas manda el
        # evento SourceFilterCreated (le hace eco a todos los clientes,
        # incluido éste), que ya refresca la lista sólo con eso — pero
        # se refresca también al toque, sin esperar la ida y vuelta por
        # la red, y de paso se le abre el editor de ajustes en el acto
        # para no obligar a un segundo click.
        refrescar()
        _refrescar_ganancia_fuente_en_hilo(nombre)
        _abrir_editor_ajustes_filtro(nombre, nombre_filtro_creado)

    marco_botones_inferior = tk.Frame(ventana_filtros, bg="#10141b")
    marco_botones_inferior.pack(pady=(0, 8))

    tk.Button(
        marco_botones_inferior, text="➕ Agregar filtro", command=lambda: _abrir_selector_nuevo_filtro(nombre, _al_crear_filtro_nuevo),
        bg="#2fd693", fg="#131825", relief="flat", font=(FUENTE_UI, 9, "bold")
    ).pack(side="left", padx=(0, 6))

    tk.Button(
        marco_botones_inferior, text="↻ Actualizar", command=refrescar,
        bg="#323b4c", fg="white", relief="flat", font=(FUENTE_UI, 9)
    ).pack(side="left")

    refrescar()


# ESQUEMA de los filtros de sonido "importantes" (los mismos que
# ofrece FILTROS_DE_SONIDO_PERMITIDOS): acá se calcan, campo por
# campo, los mismos nombres de ajuste ("threshold", "ratio",
# "attack_time", etc.), los mismos rangos y los mismos valores por
# defecto que usa el código fuente real de OBS para el Limitador
# (limiter-filter.c), el Compresor (compressor-filter.c), la Ganancia
# (gain-filter.c) y el Ecualizador de 3 bandas (eq-filter.c).
#
# Por qué hace falta esto y no alcanza con "leer los ajustes y armar
# una barra para cada uno" (que es lo que hace el editor genérico más
# abajo): OBS sólo guarda en el filtro los valores que se tocaron
# alguna vez a mano. Un filtro recién agregado (o uno al que nunca le
# tocaste, por ejemplo, el "Ataque") todavía no tiene ese valor
# guardado -aunque el motor de audio SÍ esté usando el valor por
# defecto internamente-, así que OBS-WebSocket devuelve para ese
# filtro un diccionario vacío o incompleto. Si sólo se dibujara una
# barra por cada clave que vino en la respuesta, el Limitador o el
# Compresor recién creados aparecerían "sin ajustes editables" (o con
# la mitad de los controles faltantes), que es justo el síntoma de
# "no funciona". Wiraendo cada filtro con su esquema fijo, el editor
# siempre muestra el juego completo de controles, con el valor real de
# OBS si ya está guardado, o si no, el mismo valor por defecto que
# OBS le está aplicando igual.
ESQUEMA_FILTROS_CONOCIDOS = {
    "gain_filter": [
        dict(clave="db", etiqueta="Ganancia", tipo="float",
             minimo=-30.0, maximo=30.0, paso=0.1, sufijo=" dB", defecto=0.0),
    ],
    "limiter_filter": [
        dict(clave="threshold", etiqueta="Umbral", tipo="float",
             minimo=-60.0, maximo=0.0, paso=0.1, sufijo=" dB", defecto=-6.0),
        dict(clave="release_time", etiqueta="Liberar", tipo="int",
             minimo=1, maximo=1000, paso=1, sufijo=" ms", defecto=60),
    ],
    "compressor_filter": [
        dict(clave="ratio", etiqueta="Relación", tipo="float",
             minimo=1.0, maximo=32.0, paso=0.5, sufijo=":1", defecto=10.0),
        dict(clave="threshold", etiqueta="Umbral", tipo="float",
             minimo=-60.0, maximo=0.0, paso=0.1, sufijo=" dB", defecto=-18.0),
        dict(clave="attack_time", etiqueta="Ataque", tipo="int",
             minimo=1, maximo=500, paso=1, sufijo=" ms", defecto=6),
        dict(clave="release_time", etiqueta="Liberar", tipo="int",
             minimo=1, maximo=1000, paso=1, sufijo=" ms", defecto=60),
        dict(clave="output_gain", etiqueta="Ganancia de salida", tipo="float",
             minimo=-32.0, maximo=32.0, paso=0.1, sufijo=" dB", defecto=0.0),
        dict(clave="sidechain_source", etiqueta="Fuente de atenuación/reducción",
             tipo="lista", defecto="none"),
    ],
    # "basic_eq_filter" es el kind real que usa OBS para el "Ecualizador
    # de 3 bandas"; se deja "eq_filter" también con el mismo esquema
    # por si alguna versión vieja lo reportara con ese otro nombre (ver
    # el comentario en FILTROS_DE_SONIDO_PERMITIDOS).
    "basic_eq_filter": [
        dict(clave="high", etiqueta="Alto", tipo="float",
             minimo=-20.0, maximo=20.0, paso=0.1, sufijo=" dB", defecto=0.0),
        dict(clave="mid", etiqueta="Medio", tipo="float",
             minimo=-20.0, maximo=20.0, paso=0.1, sufijo=" dB", defecto=0.0),
        dict(clave="low", etiqueta="Bajos", tipo="float",
             minimo=-20.0, maximo=20.0, paso=0.1, sufijo=" dB", defecto=0.0),
    ],
}
ESQUEMA_FILTROS_CONOCIDOS["eq_filter"] = ESQUEMA_FILTROS_CONOCIDOS["basic_eq_filter"]


def _valor_obs_o_defecto(ajustes, clave, defecto):
    """El valor que trajo OBS para esa clave si está presente (incluso
    si es 0, False o ""), o si no, el valor por defecto del esquema."""
    if clave in ajustes and ajustes[clave] is not None:
        return ajustes[clave]
    return defecto


def _valores_equivalentes(a, b):
    """Compara dos valores de ajuste tolerando el redondeo de punto
    flotante que puede introducir el viaje de ida y vuelta por JSON,
    para no hacer "temblar" una barra por una diferencia de milésimas
    que en realidad no cambió nada."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-4
    return a == b


def _construir_opciones_sidechain():
    """Fuentes que se pueden elegir como "Fuente de atenuación/
    reducción" (sidechain) de un compresor: las mismas fuentes de
    audio que ya están en la consola, más "Ninguno" primero -tal como
    hace el desplegable equivalente dentro de OBS."""
    opciones = [("Ninguno", "none")]
    for nombre_fuente_disp in sorted(fuentes.keys()):
        opciones.append((nombre_fuente_disp, nombre_fuente_disp))
    return opciones


# Rangos "de catálogo" para los campos numéricos de filtros que NO
# están en ESQUEMA_FILTROS_CONOCIDOS (croma, VST de terceros, etc.):
# para esos se sigue usando el editor genérico de siempre, adivinando
# un rango razonable por el nombre del campo. Se busca por
# coincidencia de texto dentro del nombre del campo (por ejemplo "db"
# coincide con "gain_db", "threshold_db", etc.), así que no hace falta
# cubrir cada nombre exacto de cada filtro.
RANGOS_CAMPOS_FILTRO = [
    ("ratio", (1, 32)),
    ("output_gain", (-32, 32)),
    ("makeup_gain", (0, 30)),
    ("gain", (-30, 30)),
    ("db", (-60, 30)),
    ("threshold", (-96, 0)),
    ("attack_time", (1, 500)),
    ("release_time", (1, 1000)),
    ("hold_time", (1, 1000)),
    ("sync_offset", (-950, 20000)),
    ("opacity", (0, 100)),
    ("contrast", (-100, 100)),
    ("brightness", (-100, 100)),
    ("gamma", (-100, 100)),
    ("saturation", (-100, 100)),
    ("similarity", (0, 1000)),
    ("smoothness", (0, 1000)),
    ("spill", (0, 1000)),
]


def _rango_para_campo(clave, valor):
    """Adivina un rango razonable (mínimo, máximo) para la barra
    deslizante de un campo de ajuste de filtro, según su nombre."""
    clave_baja = clave.lower()
    for patron, rango in RANGOS_CAMPOS_FILTRO:
        if patron in clave_baja:
            return rango
    if isinstance(valor, float) and 0 <= valor <= 1:
        return (0.0, 1.0)
    if isinstance(valor, bool):
        return (0, 1)
    amplitud = max(10, abs(valor) * 2)
    minimo = -amplitud if valor < 0 else 0
    return (minimo, amplitud)


def _abrir_editor_ajustes_filtro(nombre_fuente, nombre_filtro):
    """Editor de los ajustes de un filtro con controles simples: una
    barra deslizante para cada valor numérico (en vez del típico cuadro
    de texto/código con el número de dB), una tilde para cada valor de
    sí/no, un desplegable para las fuentes (por ej. el sidechain del
    compresor), y un campo de texto para el resto.

    Para el Limitador, el Compresor, la Ganancia y el Ecualizador de 3
    bandas (ver ESQUEMA_FILTROS_CONOCIDOS) se dibuja siempre el juego
    COMPLETO de controles que trae OBS, con las mismas claves, rangos y
    valores por defecto que usa OBS -así el filtro recién agregado ya
    aparece con todos sus controles funcionando, no sólo "Ganancia".
    Para cualquier otro filtro se sigue armando un control genérico por
    cada ajuste que haya devuelto OBS, como antes.

    Los cambios en los controles se mandan a OBS al instante, igual que
    el fader de volumen principal. Mientras el editor esté abierto,
    también se sondea a OBS varias veces por segundo para traer de
    vuelta cualquier cambio hecho desde DENTRO de OBS (por ejemplo, si
    alguien mueve el mismo Umbral desde la ventana de filtros de OBS),
    de forma que los cambios viajen en los dos sentidos en tiempo real
    -salvo en el control que el usuario tiene agarrado en ese instante,
    para no pelearle el slider mientras lo está arrastrando."""
    if not conectado:
        return
    try:
        info = cliente_obs.get_source_filter(nombre_fuente, nombre_filtro)
        ajustes = _valor(info, "filter_settings", "filterSettings") or {}
        tipo_filtro = _valor(info, "filter_kind", "filterKind")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudieron leer los ajustes del filtro.\n\n{e}")
        return

    editor = tk.Toplevel(ventana)
    editor.title(f"Ajustes — {nombre_filtro}")
    editor.configure(bg="#10141b")
    editor.geometry("400x480")

    tk.Label(
        editor, text=f"{nombre_fuente} · {nombre_filtro}",
        bg="#10141b", fg="white", font=(FUENTE_UI, 11, "bold")
    ).pack(anchor="w", padx=12, pady=(12, 8))

    marco_scroll = tk.Frame(editor, bg="#10141b")
    marco_scroll.pack(fill="both", expand=True, padx=12)

    canvas_ajustes = tk.Canvas(marco_scroll, bg="#10141b", highlightthickness=0)
    scrollbar_ajustes = ttk.Scrollbar(marco_scroll, orient="vertical", command=canvas_ajustes.yview)
    marco_campos = tk.Frame(canvas_ajustes, bg="#10141b")
    marco_campos.bind(
        "<Configure>", lambda e: canvas_ajustes.configure(scrollregion=canvas_ajustes.bbox("all"))
    )
    id_ventana_campos = canvas_ajustes.create_window((0, 0), window=marco_campos, anchor="nw")
    canvas_ajustes.bind(
        "<Configure>", lambda e: canvas_ajustes.itemconfig(id_ventana_campos, width=e.width)
    )
    canvas_ajustes.configure(yscrollcommand=scrollbar_ajustes.set)
    canvas_ajustes.pack(side="left", fill="both", expand=True)
    scrollbar_ajustes.pack(side="right", fill="y")

    # Nombre de campo -> función que devuelve su valor actual leído del
    # control correspondiente; se arma en el mismo orden en que se van
    # creando los controles, más abajo.
    controles = {}
    # Campos que llegaron con un tipo de dato que no tiene un control
    # simple acá (listas, objetos anidados): se guardan tal cual están
    # y se reenvían sin tocar al aplicar, para no perderlos.
    claves_avanzadas = {}
    # Último valor por campo que ESTE editor le mandó a OBS (o leyó de
    # OBS): sirve para que el sondeo periódico sepa si lo que acaba de
    # leer es un cambio genuino hecho desde OBS, o simplemente el eco
    # de lo que el editor mismo acaba de aplicar.
    valores_conocidos = {}
    # Campo -> True mientras el usuario tiene ese slider agarrado con
    # el mouse: el sondeo no le toca el valor mientras dure el arrastre.
    arrastrando = {}
    # Campo -> función(valor) que actualiza el control en pantalla SIN
    # volver a aplicar nada (para cuando el cambio vino de OBS).
    actualizadores = {}

    trabajo_sondeo = {"id": None}

    def aplicar(*_ignorar):
        nuevos_ajustes = dict(claves_avanzadas)
        for clave, obtener in controles.items():
            try:
                nuevos_ajustes[clave] = obtener()
            except Exception:
                pass
        valores_conocidos.update(nuevos_ajustes)
        try:
            cliente_obs.set_source_filter_settings(nombre_fuente, nombre_filtro, nuevos_ajustes, True)
        except Exception as e:
            print(f"No se pudieron aplicar los ajustes del filtro '{nombre_filtro}': {e}")
            return
        if tipo_filtro in CAMPOS_GANANCIA_FILTRO:
            # Este filtro puede estar cambiando la ganancia extra de la
            # fuente (ver CAMPOS_GANANCIA_FILTRO): recalcularla al
            # toque para que el medidor de nivel reaccione ya mismo,
            # sin esperar al ciclo de refresco periódico.
            _refrescar_ganancia_fuente_en_hilo(nombre_fuente)

    def _crear_barra(clave, etiqueta_texto, minimo, maximo, paso, sufijo, valor_inicial, es_entero):
        fila = tk.Frame(marco_campos, bg="#10141b")
        fila.pack(fill="x", pady=6)

        cabecera_campo = tk.Frame(fila, bg="#10141b")
        cabecera_campo.pack(fill="x")
        tk.Label(
            cabecera_campo, text=etiqueta_texto, bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)
        ).pack(side="left")
        valor_mostrado_inicial = round(float(valor_inicial)) if es_entero else float(valor_inicial)
        etiqueta_valor = tk.Label(
            cabecera_campo, text=f"{valor_mostrado_inicial:g}{sufijo}", bg="#10141b", fg="#2fd693",
            font=(FUENTE_UI, 9, "bold")
        )
        etiqueta_valor.pack(side="right")

        var_num = tk.DoubleVar(value=float(valor_inicial))

        def _al_mover(v, etq=etiqueta_valor, entero=es_entero, sfj=sufijo, clv=clave):
            valor_mostrado = round(float(v)) if entero else float(v)
            etq.config(text=f"{valor_mostrado:g}{sfj}")
            arrastrando[clv] = True
            aplicar()

        def _al_soltar(_evento=None, clv=clave):
            arrastrando[clv] = False

        control_slider = tk.Scale(
            fila, from_=minimo, to=maximo, resolution=paso,
            orient="horizontal", showvalue=False, variable=var_num, command=_al_mover,
            bg="#2fd693", fg="white", troughcolor="#293244",
            highlightthickness=0, activebackground="#4fe3ae", sliderrelief="flat",
            sliderlength=18, width=12
        )
        control_slider.pack(fill="x")
        control_slider.bind("<ButtonRelease-1>", _al_soltar)
        control_slider.bind("<ButtonPress-1>", lambda _e, clv=clave: arrastrando.__setitem__(clv, True))

        tipo_original = int if es_entero else float
        controles[clave] = (lambda var=var_num, t=tipo_original: t(var.get()))
        valores_conocidos[clave] = valor_inicial

        def _actualizar_desde_afuera(v, var=var_num, etq=etiqueta_valor, entero=es_entero, sfj=sufijo):
            var.set(float(v))
            valor_mostrado = round(float(v)) if entero else float(v)
            etq.config(text=f"{valor_mostrado:g}{sfj}")
        actualizadores[clave] = _actualizar_desde_afuera

    def _crear_lista(clave, etiqueta_texto, valor_inicial):
        fila = tk.Frame(marco_campos, bg="#10141b")
        fila.pack(fill="x", pady=6)
        tk.Label(
            fila, text=etiqueta_texto, bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)
        ).pack(anchor="w")

        opciones = _construir_opciones_sidechain()
        etiquetas_opciones = [o[0] for o in opciones]
        etiqueta_por_valor = {v: e for e, v in opciones}
        valor_por_etiqueta = {e: v for e, v in opciones}

        var_combo = tk.StringVar(value=etiqueta_por_valor.get(valor_inicial, etiquetas_opciones[0]))
        combo = ttk.Combobox(
            fila, textvariable=var_combo, values=etiquetas_opciones,
            state="readonly", style="Discreta.TCombobox"
        )
        combo.pack(fill="x", pady=(2, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: aplicar())

        controles[clave] = (lambda var=var_combo, m=valor_por_etiqueta: m.get(var.get(), "none"))
        valores_conocidos[clave] = valor_inicial

        def _actualizar_desde_afuera(v, var=var_combo, m=etiqueta_por_valor, etqs=etiquetas_opciones):
            var.set(m.get(v, etqs[0]))
        actualizadores[clave] = _actualizar_desde_afuera

    esquema = ESQUEMA_FILTROS_CONOCIDOS.get(tipo_filtro)

    if esquema:
        for campo in esquema:
            clave = campo["clave"]
            valor_inicial = _valor_obs_o_defecto(ajustes, clave, campo["defecto"])
            if campo["tipo"] == "lista":
                _crear_lista(clave, campo["etiqueta"], valor_inicial)
            else:
                _crear_barra(
                    clave, campo["etiqueta"], campo["minimo"], campo["maximo"],
                    campo["paso"], campo["sufijo"], valor_inicial, campo["tipo"] == "int"
                )

        # Ajustes que trajo OBS pero que no forman parte del esquema
        # fijo (por ejemplo, versiones de OBS que le agreguen algún
        # campo nuevo a futuro a estos mismos filtros): se conservan
        # tal cual y se reenvían sin tocar, para no perderlos al
        # aplicar, aunque no tengan un control propio acá.
        claves_conocidas = {c["clave"] for c in esquema}
        for clave, valor in ajustes.items():
            if clave not in claves_conocidas:
                claves_avanzadas[clave] = valor
    else:
        # Filtro sin esquema fijo (croma, VST de terceros, etc.): se
        # arma un control genérico por cada ajuste que haya devuelto
        # OBS, adivinando un rango razonable por el nombre del campo.
        for clave, valor in ajustes.items():
            if isinstance(valor, bool):
                var_bool = tk.BooleanVar(value=valor)
                tk.Checkbutton(
                    marco_campos, text=clave, variable=var_bool, command=aplicar,
                    bg="#10141b", fg="white", activebackground="#10141b", activeforeground="white",
                    selectcolor="#1a202b", font=(FUENTE_UI, 9), anchor="w"
                ).pack(fill="x", pady=4)
                controles[clave] = var_bool.get
                valores_conocidos[clave] = valor

            elif isinstance(valor, (int, float)):
                minimo, maximo = _rango_para_campo(clave, valor)
                es_entero = isinstance(valor, int) and not isinstance(valor, bool)
                _crear_barra(clave, clave, minimo, maximo, (1 if es_entero else 0.1), "", valor, es_entero)

            elif isinstance(valor, str):
                fila = tk.Frame(marco_campos, bg="#10141b")
                fila.pack(fill="x", pady=4)
                tk.Label(fila, text=clave, bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)).pack(anchor="w")
                var_txt = tk.StringVar(value=valor)
                entrada = tk.Entry(
                    fila, textvariable=var_txt, bg="#1a202b", fg="white",
                    insertbackground="white", relief="flat", font=(FUENTE_UI, 9)
                )
                entrada.pack(fill="x", ipady=3)
                entrada.bind("<Return>", aplicar)
                entrada.bind("<FocusOut>", aplicar)
                controles[clave] = var_txt.get
                valores_conocidos[clave] = valor

            else:
                claves_avanzadas[clave] = valor

        if not ajustes:
            tk.Label(
                marco_campos, text="Este filtro no tiene ajustes editables.",
                bg="#10141b", fg="#8e9ab3", font=(FUENTE_UI, 9)
            ).pack(pady=20)

    if claves_avanzadas:
        tk.Label(
            marco_campos,
            text=f"({len(claves_avanzadas)} ajuste(s) avanzado(s) no se muestran acá,\n"
                 "pero se conservan tal cual al aplicar.)",
            bg="#10141b", fg="#79859f", font=(FUENTE_UI, 7), justify="left"
        ).pack(anchor="w", pady=(12, 0))

    # Las barras y los desplegables ya aplican solos al tocarlos; este
    # botón es sobre todo para confirmar los campos de texto (que no se
    # aplican letra por letra) y, al tocarlo, además cierra la ventana
    # -es la confirmación final de "quedó todo como quiero".
    def _aplicar_y_cerrar():
        aplicar()
        _cerrar_editor()

    tk.Button(
        editor, text="Aplicar", command=_aplicar_y_cerrar,
        bg="#2fd693", fg="#131825", relief="flat", font=(FUENTE_UI, 9, "bold")
    ).pack(pady=10, ipadx=16, ipady=3)

    # ------------------------------------------------------------------
    # Sincronización EN VIVO desde OBS hacia este editor: OBS-WebSocket
    # avisa con eventos cuando un filtro se crea, se borra, se renombra
    # o se prende/apaga, pero NO cuando cambian sus AJUSTES (no existe
    # ningún evento "SourceFilterSettingsChanged" en el protocolo). La
    # única forma de enterarse de un cambio hecho desde dentro de OBS
    # mientras este editor está abierto es preguntarle a OBS de tanto
    # en tanto. Se hace cada medio segundo, sólo mientras la ventana
    # sigue abierta, y sin pisar el control que el usuario tenga
    # agarrado en ese instante.
    def _sondear_cambios_externos():
        if not conectado:
            trabajo_sondeo["id"] = editor.after(500, _sondear_cambios_externos)
            return
        try:
            info_actual = cliente_obs.get_source_filter(nombre_fuente, nombre_filtro)
            ajustes_actuales = _valor(info_actual, "filter_settings", "filterSettings") or {}
        except Exception:
            trabajo_sondeo["id"] = editor.after(500, _sondear_cambios_externos)
            return

        for clave, actualizador in actualizadores.items():
            if arrastrando.get(clave):
                continue
            defecto = next((c["defecto"] for c in esquema if c["clave"] == clave), None) if esquema else None
            valor_actual = _valor_obs_o_defecto(ajustes_actuales, clave, defecto)
            if valor_actual is None:
                continue
            if not _valores_equivalentes(valores_conocidos.get(clave), valor_actual):
                valores_conocidos[clave] = valor_actual
                actualizador(valor_actual)

        trabajo_sondeo["id"] = editor.after(500, _sondear_cambios_externos)

    def _cerrar_editor():
        if trabajo_sondeo["id"] is not None:
            try:
                editor.after_cancel(trabajo_sondeo["id"])
            except Exception:
                pass
            trabajo_sondeo["id"] = None
        editor.destroy()

    editor.protocol("WM_DELETE_WINDOW", _cerrar_editor)
    trabajo_sondeo["id"] = editor.after(500, _sondear_cambios_externos)


def cambiar_tamano_icono(nuevo_tamano):
    """Se llama desde el combobox de la barra superior. Como los botones
    circulares y los pads del soundboard se dibujan en un Canvas de un
    tamaño fijo, para cambiar su tamaño es más simple y confiable
    reconstruir todo (sin perder el estado de cada fuente ni la
    conexión) que tratar de estirar los dibujos existentes."""
    global tamano_icono_actual

    if nuevo_tamano not in TAMANOS_ICONO:
        return

    tamano_icono_actual = nuevo_tamano
    miniaturas_cargadas.clear()                                                            
    _reconstruir_interfaz_con_velo()
    guardar_config_interfaz({"tamano_icono": nuevo_tamano})


def sincronizar_fuente(nombre, vol_db, muted, tipo_monitor):
    """Actualiza los widgets de una fuente que ya existe, por si el estado
    cambió desde OBS directamente (otro control, otra escena, etc.)."""

    widgets = fuentes[nombre]

    widgets["fader"].set(vol_db)
    if vol_db <= UMBRAL_SILENCIO:
        widgets["db"].config(text="SILENCIO", fg="#828da6")
    else:
        widgets["db"].config(text=f"{vol_db:.1f} dB", fg="#2fd693")

    widgets["muted"] = muted
    _actualizar_boton_circular(
        widgets["mute"],
        texto_nuevo=("🔇" if muted else "🔊"),
        color_nuevo=("#ff5567" if muted else "#394151")
    )

    widgets["tipo_monitor"] = tipo_monitor
    _actualizar_boton_circular(
        widgets["monitor"],
        color_nuevo=COLORES_MONITOREO.get(tipo_monitor, "#394151")
    )

    _actualizar_estado_gris(nombre)



def cambiar_mute(nombre):
    if not conectado:
        return
    try:
        respuesta = cliente_obs.toggle_input_mute(nombre)
        nuevo_estado = respuesta.input_muted
        widgets = fuentes[nombre]
        widgets["muted"] = nuevo_estado
        _actualizar_boton_circular(
            widgets["mute"],
            texto_nuevo=("🔇" if nuevo_estado else "🔊"),
            color_nuevo=("#ff5567" if nuevo_estado else "#394151")
        )
        _actualizar_estado_gris(nombre)
    except Exception as e:
        print(f"Error cambiando mute de {nombre}: {e}")


def cambiar_monitor(nombre):
    if not conectado:
        return
    try:
        actual = cliente_obs.get_input_audio_monitor_type(nombre).monitor_type
        indice_actual = TIPOS_MONITOREO.index(actual) if actual in TIPOS_MONITOREO else 0
        siguiente = TIPOS_MONITOREO[(indice_actual + 1) % len(TIPOS_MONITOREO)]

        cliente_obs.set_input_audio_monitor_type(nombre, siguiente)

        widgets = fuentes[nombre]
        widgets["tipo_monitor"] = siguiente
        _actualizar_boton_circular(widgets["monitor"], color_nuevo=COLORES_MONITOREO[siguiente])
    except Exception as e:
        print(f"Error cambiando monitoreo de {nombre}: {e}")


def _renombrar_fuente_localmente(nombre_viejo, nombre_nuevo):
    """Mueve toda la información interna que está indexada por nombre
    (la tarjeta en pantalla, el color de etiqueta, si está marcada
    como 'principal', su posición en el orden, los niveles de audio
    que se venían graficando) de nombre_viejo a nombre_nuevo, y
    reconstruye la tarjeta desde cero con el nombre nuevo.

    Hace falta reconstruirla entera (no alcanza con cambiarle el
    texto): todos los botones de la tarjeta (mute, monitoreo, filtros,
    el fader, la estrella de 'principal') quedan atados al nombre real
    de la fuente en el momento en que se crean, así que si sólo se
    cambiara el texto visible, esos botones seguirían intentando
    controlar en OBS una fuente que ya no existe con ese nombre.

    Se usa tanto para un renombre hecho DESDE este programa (después
    de que OBS confirma el cambio) como para uno hecho desde OBS
    directamente (a través de on_input_name_changed): es indistinto
    de dónde vino, el resultado final tiene que ser el mismo."""
    if nombre_viejo == nombre_nuevo:
        return

    widgets = fuentes.get(nombre_viejo)
    if widgets is None:
        # O ya se migró por el otro camino (renombre local + eco del
        # evento de OBS llegando después), o esta fuente ni está
        # cargada en esta instancia. No hay nada que hacer.
        return

    try:
        vol_db = widgets["fader"].get()
    except Exception:
        vol_db = -60.0
    muted = widgets.get("muted", False)
    tipo_monitor = widgets.get("tipo_monitor", "OBS_MONITORING_TYPE_NONE")

    widgets["tarjeta_sombra"].destroy()
    del fuentes[nombre_viejo]

    niveles_actuales[nombre_nuevo] = niveles_actuales.pop(nombre_viejo, 0.0)
    niveles_crudos[nombre_nuevo] = niveles_crudos.pop(nombre_viejo, 0.0)
    niveles_entrada[nombre_nuevo] = niveles_entrada.pop(nombre_viejo, 0.0)
    niveles_antes_mute[nombre_nuevo] = niveles_antes_mute.pop(nombre_viejo, 0.0)
    ultima_actualizacion_nivel[nombre_nuevo] = ultima_actualizacion_nivel.pop(nombre_viejo, 0.0)
    if nombre_viejo in ultima_vez_saturado:
        ultima_vez_saturado[nombre_nuevo] = ultima_vez_saturado.pop(nombre_viejo)

    if nombre_viejo in colores_fuentes:
        colores_fuentes[nombre_nuevo] = colores_fuentes.pop(nombre_viejo)
        guardar_config_interfaz({"colores_fuentes": colores_fuentes})

    if nombre_viejo in fuentes_principales:
        fuentes_principales.discard(nombre_viejo)
        fuentes_principales.add(nombre_nuevo)
        guardar_config_interfaz({"fuentes_principales": sorted(fuentes_principales)})

    if nombre_viejo in orden_fuentes:
        orden_fuentes[orden_fuentes.index(nombre_viejo)] = nombre_nuevo

    if nombre_viejo in escena_actual_nombres:
        escena_actual_nombres.discard(nombre_viejo)
        escena_actual_nombres.add(nombre_nuevo)

    crear_fader_fuente(nombre_nuevo, vol_db, muted, tipo_monitor, nombre_visible=nombre_nuevo)
    _al_redimensionar_fuentes()
    _reubicar_fuentes()


def _iniciar_renombrar_fuente(nombre):
    """Doble clic en el nombre de una fuente: pide el nombre nuevo y,
    si es válido, se lo manda a OBS. La tarjeta en pantalla no se
    renombra en este momento -eso lo termina de hacer
    _renombrar_fuente_localmente, disparada por on_input_name_changed
    apenas OBS confirma el cambio- así que si OBS lo rechaza (por
    ejemplo, por nombre repetido) la interfaz nunca llega a mostrar
    algo que no sea cierto."""
    if not conectado:
        messagebox.showinfo("Sin conexión", "Conectate a OBS antes de renombrar una fuente.")
        return
    if nombre == NOMBRE_FUENTE_EFECTOS:
        messagebox.showinfo(
            "No se puede renombrar",
            "Esta es la fuente interna del soundboard y el programa depende de que se llame así. No se puede renombrar."
        )
        return

    nuevo_nombre = simpledialog.askstring(
        "Renombrar fuente",
        f"Nuevo nombre para \"{nombre}\":",
        initialvalue=nombre
    )
    if nuevo_nombre is None:
        return
    nuevo_nombre = nuevo_nombre.strip()
    if not nuevo_nombre or nuevo_nombre == nombre:
        return
    if nuevo_nombre in fuentes:
        messagebox.showerror("Nombre en uso", f"Ya existe una fuente llamada \"{nuevo_nombre}\".")
        return

    threading.Thread(target=_renombrar_fuente_en_hilo, args=(nombre, nuevo_nombre), daemon=True).start()


def _renombrar_fuente_en_hilo(nombre_viejo, nombre_nuevo):
    try:
        cliente_obs.set_input_name(nombre_viejo, nombre_nuevo)
    except Exception as e:
        ventana.after(0, lambda: messagebox.showerror(
            "Error al renombrar",
            f"No se pudo renombrar la fuente en OBS.\n\n{e}"
        ))



def actualizar():
    if not conectado:
        messagebox.showinfo(
            "Sin conexión",
            "Conectate a OBS antes de actualizar."
        )
        return

    boton_actualizar.config(state="disabled", text="ACTUALIZANDO...")

    threading.Thread(target=_actualizar_en_hilo, daemon=True).start()


def _actualizar_en_hilo():
    datos_fuentes = []
    error_general = None

    try:
        preparar_fuente_efectos()
    except Exception as e:
        print(f"No se pudo preparar la fuente de efectos: {e}")

    try:
        asegurar_fuentes_principales_en_todas_las_escenas()
    except Exception as e:
        print(f"No se pudo asegurar las fuentes principales en todas las escenas: {e}")

    try:
        cliente_obs.trigger_media_input_action(
            NOMBRE_FUENTE_EFECTOS, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
        )
    except Exception:
        pass

    try:
        entradas = cliente_obs.get_input_list().inputs
    except Exception as e:
        error_general = str(e)
        entradas = []

    for entrada in entradas:
        try:
            nombre = _valor(entrada, "input_name", "inputName")

            if not nombre:
                continue

            try:
                respuesta_volumen = cliente_obs.get_input_volume(nombre)
                vol_db = _valor(respuesta_volumen, "input_volume_db", "inputVolumeDb")
                if vol_db is None:
                    vol_db = -60.0
            except Exception as e:
                print(f"No se pudo leer el volumen de '{nombre}': {e}")
                vol_db = -60.0

            try:
                respuesta_mute = cliente_obs.get_input_mute(nombre)
                muted = _valor(respuesta_mute, "input_muted", "inputMuted")
                if muted is None:
                    muted = False
            except Exception as e:
                print(f"No se pudo leer el mute de '{nombre}': {e}")
                muted = False

            try:
                respuesta_monitor = cliente_obs.get_input_audio_monitor_type(nombre)
                tipo_monitor = _valor(respuesta_monitor, "monitor_type", "monitorType")
                if not tipo_monitor:
                    tipo_monitor = "OBS_MONITORING_TYPE_NONE"
            except Exception as e:
                print(f"No se pudo leer el monitoreo de '{nombre}': {e}")
                tipo_monitor = "OBS_MONITORING_TYPE_NONE"

            datos_fuentes.append({
                "nombre": nombre,
                "vol_db": vol_db,
                "muted": muted,
                "tipo_monitor": tipo_monitor
            })

        except Exception as e:
            print(f"No se pudo leer una fuente: {e}")
            continue

    nombres_en_escena = set()
    escena_leida_ok = False
    try:
        respuesta_escena = cliente_obs.get_current_program_scene()
        escena_actual = _valor(
            respuesta_escena,
            "current_program_scene_name", "currentProgramSceneName",
            "scene_name", "sceneName"
        )
        if escena_actual:
            items = cliente_obs.get_scene_item_list(escena_actual).scene_items
            for it in items:
                nombre_item = _valor(it, "source_name", "sourceName")
                habilitado = _valor(it, "scene_item_enabled", "sceneItemEnabled")
                if nombre_item and habilitado:
                    nombres_en_escena.add(nombre_item)
            escena_leida_ok = True
    except Exception as e:
        print(f"No se pudo leer la escena activa: {e}")

    try:
        nombres_en_escena |= _leer_fuentes_globales_obs()
    except Exception as e:
        print(f"No se pudo leer las fuentes de audio globales de OBS: {e}")

    ventana.after(0, lambda: _aplicar_actualizacion(datos_fuentes, error_general, nombres_en_escena, escena_leida_ok))


def _aplicar_actualizacion(datos_fuentes, error, nombres_en_escena=None, escena_leida_ok=False):
    global escena_actual_nombres, escena_actual_obtenida

    boton_actualizar.config(state="normal", text="ACTUALIZAR FUENTES")

    if error is not None:
        messagebox.showerror(
            "Error al actualizar",
            f"No se pudieron obtener las fuentes.\n\n{error}"
        )
        return

    if escena_leida_ok:
        escena_actual_nombres = nombres_en_escena or set()
        escena_actual_obtenida = True

    nombres_activos = [d["nombre"] for d in datos_fuentes]

    for nombre_existente in list(fuentes.keys()):
        if nombre_existente not in nombres_activos:
            fuentes[nombre_existente]["tarjeta_sombra"].destroy()
            del fuentes[nombre_existente]
            niveles_actuales.pop(nombre_existente, None)
            niveles_crudos.pop(nombre_existente, None)
            niveles_entrada.pop(nombre_existente, None)
            ultima_vez_saturado.pop(nombre_existente, None)
            if nombre_existente in orden_fuentes:
                orden_fuentes.remove(nombre_existente)

    for datos in datos_fuentes:
        nombre = datos["nombre"]
        try:
            if nombre in fuentes:
                sincronizar_fuente(
                    nombre, datos["vol_db"], datos["muted"], datos["tipo_monitor"]
                )
            else:
                nombre_visible = (
                    ETIQUETA_FUENTE_EFECTOS if nombre == NOMBRE_FUENTE_EFECTOS else nombre
                )
                crear_fader_fuente(
                    nombre, datos["vol_db"], datos["muted"], datos["tipo_monitor"],
                    nombre_visible=nombre_visible
                )
        except Exception as e:
            print(f"Error creando/actualizando '{nombre}': {e}")

    _al_redimensionar_fuentes()
    _reubicar_fuentes()



AJUSTES_FUENTE_EFECTOS = {
    "local_file": "",
    "is_local_file": True,
    "restart_on_activate": False,
    "close_when_inactive": False,
}


def preparar_fuente_efectos():
    """Crea (si hace falta) la fuente compartida del soundboard y se
    asegura de que esté presente en todas las escenas. Serializada con
    _lock_sincronizar_escenas: si esto se dispara dos veces casi al
    mismo tiempo (por ejemplo al crear una escena nueva, que dispara
    esta misma función Y, por separado, la de las fuentes "principales"
    — ver on_scene_created), sin este lock las dos podían preguntar "¿ya
    existe la fuente de efectos en la escena nueva?" al mismo tiempo,
    recibir "no" las dos, y terminar creándola dos veces: de ahí salían
    las dos "Soundboard_Efectos" duplicadas e idénticas en la escena
    recién creada, aunque esa fuente no esté (ni se pueda estar) marcada
    con la estrella. Con el lock, la segunda espera a que la primera
    termine y ya la encuentra creada."""
    if not conectado:
        return

    with _lock_sincronizar_escenas:
        try:
            escenas = [
                _valor(e, "scene_name", "sceneName")
                for e in cliente_obs.get_scene_list().scenes
            ]
            escenas = [e for e in escenas if e]

            if not escenas:
                return

            entradas = [
                _valor(i, "input_name", "inputName")
                for i in cliente_obs.get_input_list().inputs
            ]

            if NOMBRE_FUENTE_EFECTOS not in entradas:
                cliente_obs.create_input(
                    escenas[0],
                    NOMBRE_FUENTE_EFECTOS,
                    "ffmpeg_source",
                    AJUSTES_FUENTE_EFECTOS,
                    True
                )
                try:
                    cliente_obs.trigger_media_input_action(
                        NOMBRE_FUENTE_EFECTOS, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
                    )
                except Exception:
                    pass
            else:
                try:
                    cliente_obs.set_input_settings(
                        NOMBRE_FUENTE_EFECTOS,
                        {
                            "local_file": "",
                            "restart_on_activate": False,
                            "close_when_inactive": False,
                        },
                        True
                    )
                except Exception as e:
                    print(f"No se pudo ajustar 'restart_on_activate': {e}")

            for escena in escenas:
                try:
                    items = cliente_obs.get_scene_item_list(
                        escena
                    ).scene_items

                    nombres_en_escena = [
                        _valor(it, "source_name", "sourceName")
                        for it in items
                    ]

                    if NOMBRE_FUENTE_EFECTOS not in nombres_en_escena:
                        cliente_obs.create_scene_item(
                            escena,
                            NOMBRE_FUENTE_EFECTOS,
                            True
                        )
                        try:
                            cliente_obs.trigger_media_input_action(
                                NOMBRE_FUENTE_EFECTOS,
                                "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
                            )
                        except Exception:
                            pass

                except Exception as e:
                    print(
                        f"No se pudo agregar la fuente de efectos a "
                        f"'{escena}': {e}"
                    )

            try:
                cliente_obs.trigger_media_input_action(
                    NOMBRE_FUENTE_EFECTOS, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
                )
            except Exception:
                pass

        except Exception as e:
            print(
                f"No se pudo preparar la fuente de efectos: {e}"
            )


def preparar_fuentes_en_todas_las_escenas():
    """OBSOLETA a propósito: antes esto forzaba TODAS las fuentes de
    audio a existir en TODAS las escenas, lo cual era invasivo (tocaba
    escenas del usuario sin que lo pidiera explícitamente). Ahora sólo
    se muestran (en gris si no están en la escena al aire) y sólo se
    fuerzan a todas las escenas las que el usuario marca como
    'principales' — ver asegurar_fuentes_principales_en_todas_las_escenas().
    Se deja esta función vacía (en vez de borrarla) por si algo viejo
    todavía la llama."""
    pass



def asignar_sonido(indice):
    ruta = filedialog.askopenfilename(
        title="Elegí un archivo de audio",
        filetypes=[
            ("Archivos de audio", "*.mp3 *.wav *.ogg *.flac *.m4a"),
            ("Todos los archivos", "*.*")
        ]
    )
    if not ruta:
        return

    datos_previos = config_soundboard.get(str(indice), {})

    nombre_boton = simpledialog.askstring(
        "Nombre del sonido",
        "¿Cómo querés llamar a este sonido?",
        initialvalue=datos_previos.get("nombre", os.path.splitext(os.path.basename(ruta))[0])
    )
    if not nombre_boton:
        return

    config_soundboard[str(indice)] = {
        "nombre": nombre_boton,
        "archivo": ruta,
        "imagen": datos_previos.get("imagen"),
        "color": datos_previos.get("color"),
    }

    guardar_config_soundboard()
    construir_soundboard()


def _abrir_menu_contextual_pad(indice, event):
    """Menú de clic derecho de un pad del soundboard: agrupa acá todo lo
    que antes eran botones sueltos siempre visibles ('🔊 Sonido', '🖼
    Imagen') más el color de etiqueta y, ahora, también renombrar y
    quitar el sonido, para que la grilla se vea limpia."""
    datos = config_soundboard.get(str(indice)) or {}
    tiene_sonido = bool(datos.get("archivo"))

    menu = tk.Menu(ventana, tearoff=0, bg="#151a24", fg="white", activebackground="#323b4c", activeforeground="white")
    menu.add_command(
        label=("🔊  Cambiar sonido…" if tiene_sonido else "🔊  Asignar sonido…"),
        command=lambda: asignar_sonido(indice)
    )
    if tiene_sonido:
        menu.add_command(label="🖼  Asignar imagen…", command=lambda: asignar_imagen(indice))
        menu.add_command(label="✏  Renombrar…", command=lambda: _iniciar_renombrar_pad(indice))
    menu.add_separator()

    submenu_color = tk.Menu(menu, tearoff=0, bg="#151a24", fg="white", activebackground="#323b4c")
    submenu_color.add_command(label="Sin etiqueta", command=lambda: _asignar_color_pad(indice, None))
    submenu_color.add_separator()
    for color in PALETA_ETIQUETAS:
        if color is None:
            continue
        submenu_color.add_command(
            label="        ", background=color, activebackground=color,
            command=lambda c=color: _asignar_color_pad(indice, c)
        )
    menu.add_cascade(label="🏷  Color de etiqueta", menu=submenu_color)

    if tiene_sonido:
        menu.add_separator()
        menu.add_command(label="🗑  Quitar sonido", command=lambda: _quitar_pad(indice))

    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _iniciar_renombrar_pad(indice):
    datos = config_soundboard.get(str(indice))
    if not datos or not datos.get("archivo"):
        return
    nuevo_nombre = simpledialog.askstring(
        "Renombrar sonido", "Nuevo nombre para este botón:", initialvalue=datos.get("nombre", "")
    )
    if nuevo_nombre is None:
        return
    nuevo_nombre = nuevo_nombre.strip()
    if not nuevo_nombre:
        return
    datos["nombre"] = nuevo_nombre
    guardar_config_soundboard()
    construir_soundboard()


def _quitar_pad(indice):
    if not messagebox.askyesno("Quitar sonido", "¿Quitar el sonido asignado a este botón?"):
        return
    color = (config_soundboard.get(str(indice)) or {}).get("color")
    if color:
        config_soundboard[str(indice)] = {"nombre": None, "archivo": None, "imagen": None, "color": color}
    else:
        config_soundboard.pop(str(indice), None)
    for clave in [c for c in miniaturas_cargadas if c[0] == indice]:
        miniaturas_cargadas.pop(clave, None)
    guardar_config_soundboard()
    construir_soundboard()


_celdas_pads = {}
# Por cada pad, una función sin argumentos que vuelve a pintar su
# placa con el estado actual (mouse encima, presionado, y si está
# sonando de verdad). construir_soundboard() la llena de nuevo cada
# vez que reconstruye la grilla; el resto del programa la usa para
# poder prender/apagar el brillo de un pad puntual desde afuera (por
# ejemplo, cuando el chequeo periódico del estado de OBS detecta que
# un efecto terminó).
_refrescos_pads = {}
_arrastre_pad = {
    "indice": None, "arrastrando": False,
    "x_inicio": 0, "y_inicio": 0, "destino_resaltado": None,
}

# ------------------------------------------------------------------
# ESTADO DE REPRODUCCIÓN DEL SOUNDBOARD
# ------------------------------------------------------------------
# Como todos los pads comparten UNA sola fuente de OBS ("Soundboard_
# Efectos"), en un momento dado sólo puede estar sonando un pad a la
# vez. "indice" guarda cuál es (o None si no hay nada sonando), y
# "token" es un número que se incrementa cada vez que arranca una
# reproducción nueva -incluyendo un reinicio del mismo pad-. Un
# fundido en curso se fija en qué token nació: si ese token dejó de
# ser el vigente (porque se disparó otra reproducción mientras
# fundía), el fundido se cancela solo sin frenar el sonido nuevo.
_sesion_reproduccion = {"indice": None, "token": 0, "inicio": 0.0}


def _pad_bajo_puntero(x_root, y_root):
    try:
        widget = ventana.winfo_containing(x_root, y_root)
    except Exception:
        return None
    while widget is not None:
        indice = getattr(widget, "indice_pad", None)
        if indice is not None:
            return indice
        widget = widget.master
    return None


def _borde_normal_pad(indice):
    # La celda siempre es invisible (mismo color que el panel): el color
    # de la etiqueta ya no se pinta acá afuera, sino en el marco de la
    # propia placa de vidrio (ver "color_marco" en _imagen_placa), así
    # no queda un cuadrado de color suelto alrededor del pad.
    return COLOR_PANEL_SOUNDBOARD, 2


def _restablecer_borde_pad(indice):
    celda = _celdas_pads.get(indice)
    if not celda:
        return
    color, grosor = _borde_normal_pad(indice)
    celda.config(highlightbackground=color, highlightthickness=grosor)


def _resaltar_destino_pad(indice_nuevo):
    anterior = _arrastre_pad["destino_resaltado"]
    if anterior == indice_nuevo:
        return
    if anterior is not None:
        _restablecer_borde_pad(anterior)
    if indice_nuevo is not None:
        celda = _celdas_pads.get(indice_nuevo)
        if celda:
            celda.config(highlightbackground="#2fd693", highlightthickness=3)
    _arrastre_pad["destino_resaltado"] = indice_nuevo


def _limpiar_resaltado_pads():
    anterior = _arrastre_pad["destino_resaltado"]
    if anterior is not None:
        _restablecer_borde_pad(anterior)
    _arrastre_pad["destino_resaltado"] = None


def _iniciar_arrastre_pad(indice, event):
    _arrastre_pad["indice"] = indice
    _arrastre_pad["arrastrando"] = False
    _arrastre_pad["x_inicio"] = event.x_root
    _arrastre_pad["y_inicio"] = event.y_root


def _mover_arrastre_pad(indice, event):
    datos = _arrastre_pad
    if datos["indice"] != indice:
        return
    if not datos["arrastrando"]:
        dx = abs(event.x_root - datos["x_inicio"])
        dy = abs(event.y_root - datos["y_inicio"])
        if dx < UMBRAL_ARRASTRE_PX and dy < UMBRAL_ARRASTRE_PX:
            return
        datos["arrastrando"] = True
    _resaltar_destino_pad(_pad_bajo_puntero(event.x_root, event.y_root))


def _soltar_arrastre_pad(indice, event):
    datos = _arrastre_pad
    if datos["indice"] != indice:
        return
    fue_arrastre = datos["arrastrando"]
    datos["indice"] = None
    datos["arrastrando"] = False
    _limpiar_resaltado_pads()
    if not fue_arrastre:
        reproducir_sonido(indice)
        return
    destino = _pad_bajo_puntero(event.x_root, event.y_root)
    if destino is None or destino == indice:
        return
    _reordenar_pad(indice, destino)


def _reordenar_pad(indice_origen, indice_destino):
    """Intercambia el contenido de dos pads (arrastrar y soltar un pad
    sobre otro para acomodarlos donde el usuario quiera): a diferencia
    del orden de las fuentes, acá se hace un intercambio simple porque
    los pads ocupan siempre una grilla de posiciones fijas."""
    if indice_origen == indice_destino:
        return
    clave_o, clave_d = str(indice_origen), str(indice_destino)
    datos_o = config_soundboard.get(clave_o)
    datos_d = config_soundboard.get(clave_d)

    if datos_d is not None:
        config_soundboard[clave_o] = datos_d
    else:
        config_soundboard.pop(clave_o, None)
    if datos_o is not None:
        config_soundboard[clave_d] = datos_o
    else:
        config_soundboard.pop(clave_d, None)

    for indice in (indice_origen, indice_destino):
        for clave in [c for c in miniaturas_cargadas if c[0] == indice]:
            miniaturas_cargadas.pop(clave, None)

    guardar_config_soundboard()
    construir_soundboard()


def _asignar_color_pad(indice, color):
    datos = config_soundboard.setdefault(str(indice), {})
    datos.setdefault("nombre", None)
    datos.setdefault("archivo", None)
    datos.setdefault("imagen", None)
    if color is None:
        datos.pop("color", None)
    else:
        datos["color"] = color
    if not datos.get("archivo") and not datos.get("imagen") and not datos.get("color"):
        config_soundboard.pop(str(indice), None)
    guardar_config_soundboard()
    construir_soundboard()


def asignar_imagen(indice):
    if not config_soundboard.get(str(indice), {}).get("archivo"):
        messagebox.showwarning(
            "Asigná un sonido primero",
            "Elegí un archivo de audio para este botón antes de ponerle una imagen."
        )
        return

    ruta_imagen = filedialog.askopenfilename(
        title="Elegí una imagen para este sonido",
        filetypes=[
            ("Imágenes", "*.png *.jpg *.jpeg *.gif *.bmp"),
            ("Todos los archivos", "*.*")
        ]
    )
    if not ruta_imagen:
        return

    config_soundboard[str(indice)]["imagen"] = ruta_imagen
    for clave in [c for c in miniaturas_cargadas if c[0] == indice]:
        miniaturas_cargadas.pop(clave, None)

    guardar_config_soundboard()
    construir_soundboard()


def _geometria_cara_placa(ancho, alto):
    """Calcula la caja (x0, y0, x1, y1) y el radio de esquina de la CARA
    de un pad de 'ancho' x 'alto', con las mismas proporciones que usa
    _imagen_placa para dibujar marco/foso/cara. Se usa para que la
    miniatura del sonido encaje exactamente en ese hueco, en vez de
    quedar como un cuadrado más chico y descentrado."""
    ancho, alto = max(1, int(ancho)), max(1, int(alto))
    lado = min(ancho, alto)
    margen = max(1, round(lado * 0.035))
    grosor_marco = max(1, round(lado * 0.018))
    grosor_foso = max(1, round(lado * 0.009))
    radio = max(2, round(lado * 0.235))

    x0e, y0e = margen, margen
    x1e, y1e = ancho - 1 - margen, alto - 1 - margen
    x0f, y0f = x0e + grosor_marco, y0e + grosor_marco
    x1f, y1f = x1e - grosor_marco, y1e - grosor_marco
    x0c, y0c = x0f + grosor_foso, y0f + grosor_foso
    x1c, y1c = x1f - grosor_foso, y1f - grosor_foso

    radio_foso = max(2, radio - grosor_marco)
    radio_cara = max(2, radio_foso - grosor_foso)
    return (x0c, y0c, x1c, y1c), radio_cara


def _redimensionar_para_entrar(imagen, ancho_obj, alto_obj, radio=None):
    """Estira la imagen para que ocupe EXACTAMENTE el tamaño del pad
    (mismo ancho y alto que el recuadro), sin dejar bordes vacíos y sin
    recortar ninguna parte: se ve completa, de punta a punta. Si la
    imagen original no es cuadrada va a verse levemente estirada, pero
    eso es preferible a que le sobre marco vacío o que se le corte un
    pedazo.

    El remuestreo ahora es LANCZOS y no NEAREST. NEAREST copia el pixel
    más cercano y listo: cuando la imagen original es más chica que el
    pad (o no es un múltiplo exacto), eso se ve literalmente como
    bloques cuadrados y bordes en escalera -el "pixelado" de antes-.
    LANCZOS promedia el vecindario de cada pixel, así que las diagonales
    y las curvas de la imagen quedan suaves a cualquier tamaño de pad."""
    ancho_obj = max(1, round(ancho_obj))
    alto_obj = max(1, round(alto_obj))
    if radio is None:
        radio = max(2, round(min(ancho_obj, alto_obj) * 0.12))

    ancho_intermedio = ancho_obj * 4
    alto_intermedio = alto_obj * 4
    if imagen.width > ancho_intermedio or imagen.height > alto_intermedio:
        imagen.thumbnail((ancho_intermedio, alto_intermedio), Image.LANCZOS)

    imagen = imagen.resize((ancho_obj, alto_obj), Image.LANCZOS).convert("RGBA")

    # Las esquinas se redondean con una máscara suavizada (dibujada al
    # cuádruple y reducida) para que la miniatura acompañe la curva de
    # la cara del pad en vez de quedar como un recuadro pegado encima.
    mascara = Image.new("L", (ancho_obj * 4, alto_obj * 4), 0)
    ImageDraw.Draw(mascara).rounded_rectangle(
        [0, 0, ancho_obj * 4 - 1, alto_obj * 4 - 1], radius=max(1, radio) * 4, fill=255
    )
    imagen.putalpha(mascara.resize((ancho_obj, alto_obj), Image.LANCZOS))
    return imagen


def _obtener_imagen_decodificada(ruta_imagen):
    """Devuelve la imagen ya abierta y decodificada por Pillow (con
    exif_transpose y conversión de modo aplicados), cacheada por ruta
    de archivo. Esta es la parte cara (leer el archivo del disco y
    decodificarlo) y sólo se hace una vez por archivo, sin importar
    cuántas veces se pida después con distintos tamaños de pad."""
    if ruta_imagen in _imagenes_decodificadas_cache:
        return _imagenes_decodificadas_cache[ruta_imagen]

    imagen = Image.open(ruta_imagen)
    imagen = ImageOps.exif_transpose(imagen)
    if imagen.mode not in ("RGB", "RGBA"):
        imagen = imagen.convert("RGBA")

    # Si el archivo original es enorme (foto de celular, captura 4K),
    # la reducimos una sola vez acá a un tamaño "de sobra" para
    # cualquier pad, así las decodificaciones y resizes posteriores
    # trabajan siempre sobre una imagen chica en vez de la original.
    TAMANO_MAXIMO_CACHE = (900, 900)
    if imagen.width > TAMANO_MAXIMO_CACHE[0] or imagen.height > TAMANO_MAXIMO_CACHE[1]:
        imagen.thumbnail(TAMANO_MAXIMO_CACHE, Image.BOX)

    _imagenes_decodificadas_cache[ruta_imagen] = imagen
    return imagen


def cargar_miniatura(indice, ruta_imagen, tamano, radio=None):
    """Carga (con caché) la imagen del pad ``indice`` ya redimensionada
    para entrar completa dentro de ``tamano`` = (ancho, alto), sin
    recortarla. Funciona con imágenes de cualquier tamaño de origen: las
    grandes (fotos de celular, capturas 4K, etc.) se abren igual y se
    reducen acá mismo antes de mostrarlas.

    La apertura y decodificación del archivo (lo caro) está separada
    en _obtener_imagen_decodificada y cacheada por ruta; acá sólo se
    cachea, por (indice, tamano), el PhotoImage final ya al tamaño del
    pad, que es una operación barata de repetir."""
    clave_cache = (indice, tamano, radio)
    if clave_cache in miniaturas_cargadas:
        return miniaturas_cargadas[clave_cache]

    ancho_obj, alto_obj = tamano

    try:
        if HAY_PILLOW:
            imagen = _obtener_imagen_decodificada(ruta_imagen)
            imagen = _redimensionar_para_entrar(imagen, ancho_obj, alto_obj, radio)
            foto = ImageTk.PhotoImage(imagen)
        else:
            foto = tk.PhotoImage(file=ruta_imagen)

        miniaturas_cargadas[clave_cache] = foto
        return foto
    except Exception as e:
        print(f"No se pudo cargar la imagen del botón {indice}: {e}")
        return None



def _refrescar_iluminacion_pad(indice):
    """Vuelve a pintar UN pad puntual con su estado actual (llama a la
    función que construir_soundboard() dejó guardada en
    _refrescos_pads). No hace nada si ese pad no está construido en
    este momento (por ejemplo, si se acaba de borrar)."""
    refrescar = _refrescos_pads.get(indice)
    if refrescar is not None:
        refrescar()


def _fijar_pad_activo(indice):
    """Único lugar que cambia _sesion_reproduccion['indice']: además de
    guardar cuál es el pad "activo" (el que se apaga con fundido si se
    lo vuelve a tocar), prende o apaga el brillo del pad que
    corresponda en la interfaz. SIEMPRE hay que llamarla desde el hilo
    principal -si el aviso viene de un hilo de fondo, primero hay que
    pasarlo por ventana.after(0, ...)."""
    anterior = _sesion_reproduccion.get("indice")
    if anterior == indice:
        return
    _sesion_reproduccion["indice"] = indice
    if anterior is not None:
        _refrescar_iluminacion_pad(anterior)
    if indice is not None:
        _refrescar_iluminacion_pad(indice)


def _apagar_pad_si_token_vigente(indice, token):
    """Apaga el pad SOLO si el token que lo prendió sigue siendo el
    vigente -si mientras tanto arrancó otra reproducción (el mismo pad
    reiniciado, o cualquier otro), no tocamos nada: esa reproducción
    nueva es la que manda ahora."""
    if _sesion_reproduccion.get("token") == token:
        _fijar_pad_activo(None)


def _cargar_y_disparar(indice, accion, token):
    datos = config_soundboard.get(str(indice))
    if not datos or not datos.get("archivo"):
        return

    if not conectado:
        messagebox.showwarning("Sin conexión", "Conectate a OBS para poder reproducir los efectos.")
        ventana.after(0, lambda: _apagar_pad_si_token_vigente(indice, token))
        return

    try:
        cliente_obs.set_input_settings(
            NOMBRE_FUENTE_EFECTOS,
            {
                "local_file": datos["archivo"],
                "is_local_file": True,
                "restart_on_activate": False,
                "close_when_inactive": False,
            },
            True
        )
        cliente_obs.trigger_media_input_action(NOMBRE_FUENTE_EFECTOS, accion)
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo reproducir el efecto.\n\n{e}")
        ventana.after(0, lambda: _apagar_pad_si_token_vigente(indice, token))


def _iniciar_reproduccion(indice):
    """Arranca (o reinicia) la reproducción de un pad. Esto SIEMPRE crea
    una sesión nueva, así que si había un fundido en curso de una
    reproducción anterior, ese fundido se va a dar cuenta -por el
    token- de que ya no es el vigente y se va a cancelar solo sin
    tocar este sonido nuevo."""
    _sesion_reproduccion["token"] += 1
    token = _sesion_reproduccion["token"]
    _sesion_reproduccion["inicio"] = time.time()
    _fijar_pad_activo(indice)
    threading.Thread(
        target=_cargar_y_disparar,
        args=(indice, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART", token),
        daemon=True
    ).start()


def _fundido_y_detener(indice, token, duracion=1.0, pasos=20):
    """Baja el volumen de la fuente de efectos desde su nivel actual
    hasta silencio en 'duracion' segundos y, al llegar abajo, detiene
    el medio. Al final (llegue a terminar o se cancele en el camino)
    siempre deja el volumen tal cual estaba antes de fundir, para que
    la próxima reproducción de cualquier pad vuelva a sonar al nivel
    normal del fader. El pad se apaga recién en ese momento -mientras
    dura el fundido, el sonido técnicamente sigue activo en OBS, así
    que la luz se mantiene prendida hasta el STOP final."""
    if not conectado:
        ventana.after(0, lambda: _apagar_pad_si_token_vigente(indice, token))
        return

    try:
        respuesta = cliente_obs.get_input_volume(NOMBRE_FUENTE_EFECTOS)
        vol_inicial = _valor(respuesta, "input_volume_db", "inputVolumeDb")
        if vol_inicial is None:
            vol_inicial = 0.0
    except Exception as e:
        print(f"No se pudo leer el volumen para el fundido: {e}")
        vol_inicial = 0.0

    intervalo = duracion / pasos
    cancelado = False
    for paso in range(1, pasos + 1):
        if _sesion_reproduccion.get("token") != token:
            # Mientras fundía se disparó otra reproducción (de este
            # mismo pad o de otro): dejamos el sonido nuevo en paz.
            cancelado = True
            break
        fraccion = paso / pasos
        db = vol_inicial + (UMBRAL_SILENCIO - vol_inicial) * fraccion
        try:
            if db <= UMBRAL_SILENCIO:
                cliente_obs.set_input_volume(NOMBRE_FUENTE_EFECTOS, vol_mul=0)
            else:
                cliente_obs.set_input_volume(NOMBRE_FUENTE_EFECTOS, vol_db=db)
        except Exception as e:
            print(f"Error durante el fundido: {e}")
            cancelado = True
            break
        time.sleep(intervalo)

    if not cancelado:
        try:
            cliente_obs.trigger_media_input_action(
                NOMBRE_FUENTE_EFECTOS, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
            )
            # El STOP es async: OBS tarda un pelín en aplicarlo de
            # verdad. Si devolviéramos el volumen ya mismo, hay una
            # ventana breve en la que el sonido -que técnicamente
            # sigue reproduciéndose todavía- se escucha de golpe a
            # volumen normal antes de cortar. Por eso se espera a que
            # OBS confirme que ya se detuvo (o, como red de
            # contención, hasta un segundo) antes de restaurar.
            _esperar_a_que_se_detenga(token)
        except Exception as e:
            print(f"Error deteniendo el efecto tras el fundido: {e}")

    try:
        cliente_obs.set_input_volume(NOMBRE_FUENTE_EFECTOS, vol_db=vol_inicial)
    except Exception as e:
        print(f"No se pudo restaurar el volumen tras el fundido: {e}")

    ventana.after(0, lambda: _apagar_pad_si_token_vigente(indice, token))


def _esperar_a_que_se_detenga(token, tope_seg=1.0):
    """Sondea el estado real del efecto hasta que OBS confirme que ya
    se detuvo (o hasta 'tope_seg' como red de contención, por si el
    STOP nunca llega a reflejarse en el estado por algún motivo)."""
    limite = time.time() + tope_seg
    while time.time() < limite:
        if _sesion_reproduccion.get("token") != token:
            # Arrancó otra reproducción mientras esperábamos: ya no
            # tiene sentido seguir esperando a que ESTA se detenga.
            return
        try:
            respuesta = cliente_obs.get_media_input_status(NOMBRE_FUENTE_EFECTOS)
            estado = _valor(respuesta, "media_state", "mediaState")
        except Exception as e:
            print(f"No se pudo confirmar que el efecto se detuvo: {e}")
            return
        if estado in ESTADOS_MEDIA_DETENIDO:
            return
        time.sleep(0.05)


def reproducir_sonido(indice):
    if _sesion_reproduccion.get("indice") == indice:
        # Se volvió a apretar el mismo pad mientras sonaba: en vez de
        # reiniciarlo, se apaga con un fundido de volumen de 1 segundo.
        token = _sesion_reproduccion["token"]
        threading.Thread(
            target=_fundido_y_detener,
            args=(indice, token),
            daemon=True
        ).start()
        return

    _iniciar_reproduccion(indice)


def detener_sonido(indice):
    if _sesion_reproduccion.get("indice") == indice:
        _sesion_reproduccion["token"] += 1
        _fijar_pad_activo(None)
    if not conectado:
        return
    try:
        cliente_obs.trigger_media_input_action(
            NOMBRE_FUENTE_EFECTOS, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
        )
    except Exception as e:
        print(f"Error deteniendo el efecto: {e}")


def reiniciar_sonido(indice):
    # El botón de reinicio siempre vuelve a arrancar el sonido desde
    # cero, nunca lo apaga -a diferencia de tocar el pad de nuevo-.
    _iniciar_reproduccion(indice)


# ------------------------------------------------------------------
# CHEQUEO PERIÓDICO DEL ESTADO REAL DE REPRODUCCIÓN
# ------------------------------------------------------------------
# El brillo del pad no se apaga "a ojo" (por ejemplo, calculando
# cuánto dura el archivo): se le pregunta a OBS, cada
# INTERVALO_REFRESCO_REPRODUCCION_MS, si el medio todavía está
# reproduciéndose de verdad. Así, un efecto con una cola larga de
# silencio (o simplemente muy bajo) se sigue viendo iluminado mientras
# OBS lo siga teniendo activo, en vez de apagarse antes de tiempo.
INTERVALO_REFRESCO_REPRODUCCION_MS = 150
GRACIA_INICIO_REPRODUCCION_SEG = 0.35  # ignora el estado recién arrancado un
# instante: al reiniciar, OBS puede tardar un pestañeo en reportar
# "reproduciendo" y hasta entonces todavía contesta el estado viejo
# ("detenido"), que si le hiciéramos caso apagaría la luz apenas
# prendida.
ESTADOS_MEDIA_DETENIDO = {"OBS_MEDIA_STATE_STOPPED", "OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_ERROR"}


def _consultar_estado_reproduccion():
    indice = _sesion_reproduccion.get("indice")
    if indice is None:
        return
    token = _sesion_reproduccion.get("token")
    inicio = _sesion_reproduccion.get("inicio") or 0
    if time.time() - inicio < GRACIA_INICIO_REPRODUCCION_SEG:
        return
    try:
        respuesta = cliente_obs.get_media_input_status(NOMBRE_FUENTE_EFECTOS)
        estado = _valor(respuesta, "media_state", "mediaState")
    except Exception as e:
        print(f"No se pudo consultar el estado del efecto: {e}")
        return
    if estado in ESTADOS_MEDIA_DETENIDO:
        ventana.after(0, lambda: _apagar_pad_si_token_vigente(indice, token))


def _programar_refresco_reproduccion():
    if conectado and _sesion_reproduccion.get("indice") is not None:
        threading.Thread(target=_consultar_estado_reproduccion, daemon=True).start()
    ventana.after(INTERVALO_REFRESCO_REPRODUCCION_MS, _programar_refresco_reproduccion)



def _columnas_disponibles():
    ancho_disponible = canvas_sb.winfo_width()
    ancho_celda_con_padding = medida_actual()["pad_ancho"] + 20
    if ancho_disponible <= 1 or ancho_celda_con_padding <= 0:
        return columnas_soundboard
    columnas_actuales = max(1, columnas_soundboard)
    columnas_teoricas = max(1, ancho_disponible // ancho_celda_con_padding)
    if columnas_teoricas >= columnas_actuales:
        return columnas_teoricas
    # Mismo colchón que en la grilla de fuentes (ver
    # MARGEN_HISTERESIS_COLUMNAS): que el panel se achique "apenas un
    # poco" no debe tirar de golpe una columna entera de pads a la fila
    # de abajo.
    espacio_necesario = columnas_actuales * ancho_celda_con_padding
    if espacio_necesario - ancho_disponible <= MARGEN_HISTERESIS_COLUMNAS:
        return columnas_actuales
    return columnas_teoricas


_trabajo_redimension_soundboard = {"id": None}
# Ancho de canvas_sb que tenía el panel la última vez que se construyó
# la grilla de pads de verdad (construir_soundboard). Sirve para
# detectar el mismo problema que en la grilla de fuentes: si el panel
# se achica/agranda sin llegar a cambiar la cantidad de columnas, las
# celdas quedan con el ancho "estirado" viejo y la última puede terminar
# sobresaliendo del borde visible. A diferencia de fuentes, acá no
# conviene reajustar el ancho en cada asentamiento (esta grilla destruye
# y recrea todos los pads, es cara), así que sólo se fuerza una
# reconstrucción completa cuando el ancho cambió lo suficiente como para
# que de verdad haga falta.
_ultimo_ancho_soundboard = {"valor": None}
MARGEN_REAJUSTE_ANCHO_SOUNDBOARD = 0


def _arrastre_ventana_en_curso():
    """True mientras el usuario está arrastrando el borde de LA VENTANA
    (no el divisor entre paneles) y todavía no se soltó: es la ventana
    de tiempo en la que _al_redimensionar_ventana ya mostró el velo y
    está esperando a que el arrastre se quede quieto para reconstruir
    todo de una (ver _trabajo_redimension, más abajo en el archivo).
    Se consulta con try/except porque ese nombre se define más adelante
    en el archivo (mismo motivo que el try/except de velo_redimension
    en construir_cuerpo): a esta altura de la carga del módulo todavía
    no existe, pero para cuando esta función se llegue a llamar de
    verdad (el usuario ya movió el mouse) sí."""
    try:
        return _trabajo_redimension["id"] is not None
    except NameError:
        return False


def _al_redimensionar_soundboard(event=None):
    """Recalcula cuántas columnas entran en el ancho actual y sólo
    reconstruye la grilla si ese número cambió (o si el ancho se movió
    lo suficiente como para que las celdas ya no encajen bien, ver
    _ultimo_ancho_soundboard).

    Mientras el usuario está arrastrando el borde de LA VENTANA, este
    handler no programa nada: esta grilla destruye y vuelve a crear
    todos los pads, y hacerlo en cada pixel de arrastre (aunque sea con
    espera de 16ms) es trabajo pesado compitiendo por CPU con el propio
    arrastre, y ESO es lo que se veía como parpadeo/tironeo de la
    ventana. En vez de eso, se deja que el mecanismo de toda la ventana
    (_al_redimensionar_ventana/_aplicar_redimension, más abajo) haga UNA
    sola reconstrucción completa (construir_cuerpo → construir_soundboard)
    recién cuando el usuario suelta el borde, tapada por el velo. Fuera
    de un arrastre de ventana (por ejemplo, moviendo el divisor entre
    paneles) el comportamiento no cambia: se sigue esperando sólo el
    toque de calma de 16ms de siempre."""
    if _arrastre_ventana_en_curso():
        return
    if _trabajo_redimension_soundboard["id"] is not None:
        ventana.after_cancel(_trabajo_redimension_soundboard["id"])
    # Ídem comentario en el redimensionado del panel de fuentes: 16ms
    # es, en la práctica, "apenas se puede".
    _trabajo_redimension_soundboard["id"] = ventana.after(16, _aplicar_redimension_soundboard)


def _aplicar_redimension_soundboard():
    global columnas_soundboard
    _trabajo_redimension_soundboard["id"] = None
    if _reconstruccion_en_curso["activa"]:
        return
    nuevas_columnas = _columnas_disponibles()
    ancho_actual = canvas_sb.winfo_width()
    ancho_referencia = _ultimo_ancho_soundboard["valor"]
    ancho_se_corrio_de_mas = (
        ancho_actual > 1
        and ancho_referencia is not None
        and abs(ancho_actual - ancho_referencia) > MARGEN_REAJUSTE_ANCHO_SOUNDBOARD
    )
    if nuevas_columnas != columnas_soundboard or ancho_se_corrio_de_mas:
        columnas_soundboard = nuevas_columnas
        construir_soundboard()


COLOR_PANEL_SOUNDBOARD = "#10141b"


def construir_soundboard():
    _ultimo_ancho_soundboard["valor"] = canvas_sb.winfo_width()
    for widget in panel_soundboard.winfo_children():
        widget.destroy()

    columnas = max(1, columnas_soundboard)
    medida = medida_actual()

    ancho_preferido = medida["pad_ancho"]
    ancho_disponible = canvas_sb.winfo_width()
    padding_por_celda = 20
    if ancho_disponible > 1:
        ancho_celda = max(ancho_preferido, (ancho_disponible // columnas) - padding_por_celda)
    else:
        ancho_celda = ancho_preferido

    ancho_imagen_pad_base = ancho_celda - 12
    pie_celda = 76
    alto_celda = ancho_imagen_pad_base + pie_celda

    marco_grid = tk.Frame(panel_soundboard, bg=COLOR_PANEL_SOUNDBOARD)
    marco_grid.pack(fill="x")

    _celdas_pads.clear()
    _refrescos_pads.clear()

    for i in range(num_pads_soundboard):
        fila = i // columnas
        col = i % columnas

        datos = config_soundboard.get(str(i))
        tiene_sonido = bool(datos and datos.get("archivo"))

        def _detener(idx=i):
            detener_sonido(idx)

        def _reiniciar(idx=i):
            reiniciar_sonido(idx)

        color_etiqueta_pad = datos.get("color") if datos else None
        # La celda ya no es una "tarjeta" gris: es transparente contra el
        # panel, así lo único que se ve es la placa de vidrio del pad.
        # El color de la etiqueta YA NO se pinta acá afuera (eso se veía
        # como un cuadrado suelto rodeando la placa): ahora se aplica
        # directamente sobre el marco metálico de la propia placa, más
        # abajo, vía "color_marco".
        celda = tk.Frame(
            marco_grid, bg=COLOR_PANEL_SOUNDBOARD, width=ancho_celda, height=alto_celda,
            highlightbackground=COLOR_PANEL_SOUNDBOARD, highlightthickness=2
        )
        celda.grid(row=fila, column=col, padx=6, pady=6)
        celda.grid_propagate(False)
        celda.indice_pad = i
        _celdas_pads[i] = celda
        # Clic izquierdo sostenido y arrastrado = mover el pad de lugar
        # (soltarlo sobre otro pad los intercambia); clic simple sin
        # arrastre = reproducir. Clic derecho = menú con el resto de las
        # acciones.
        celda.bind("<ButtonPress-1>", lambda e, idx=i: _iniciar_arrastre_pad(idx, e))
        celda.bind("<B1-Motion>", lambda e, idx=i: _mover_arrastre_pad(idx, e))
        celda.bind("<ButtonRelease-1>", lambda e, idx=i: _soltar_arrastre_pad(idx, e))
        celda.bind("<Button-3>", lambda e, idx=i: _abrir_menu_contextual_pad(idx, e))

        ancho_imagen_pad = ancho_celda - 12
        alto_pad_principal = ancho_imagen_pad
        pad_canvas = tk.Canvas(
            celda, width=ancho_imagen_pad, height=alto_pad_principal,
            bg=COLOR_PANEL_SOUNDBOARD, highlightthickness=0, cursor="hand2"
        )
        pad_canvas.pack(pady=(8, 4))

        # Color de acento del pad: el que eligió el usuario si puso uno,
        # el verde de la consola si tiene sonido, y nada (gris de fábrica)
        # si está vacío.
        acento_pad = color_etiqueta_pad or ("#2fd693" if tiene_sonido else None)

        pad_esta_sonando = _sesion_reproduccion.get("indice") == i
        placa_normal = _placa_tk(ancho_imagen_pad, alto_pad_principal, acento_pad, tiene_sonido,
                                  color_marco=color_etiqueta_pad, reproduciendo=pad_esta_sonando)
        if placa_normal is not None:
            pad_canvas.imagen_placa = placa_normal
            id_placa = pad_canvas.create_image(0, 0, anchor="nw", image=placa_normal)

            def _pintar_placa(canvas=pad_canvas, id_img=id_placa, w=ancho_imagen_pad,
                              h=alto_pad_principal, acento=acento_pad, enc=tiene_sonido,
                              hover=False, presionado=False, reproduciendo=False,
                              color_marco=color_etiqueta_pad):
                foto = _placa_tk(w, h, acento, enc, hover, presionado, color_marco=color_marco,
                                  reproduciendo=reproduciendo)
                if foto is not None:
                    canvas.imagen_placa = foto
                    canvas.itemconfig(id_img, image=foto)
        else:
            # Respaldo sin Pillow: se sigue usando el dibujo viejo de Tk.
            _dibujar_boton_vidrio(
                pad_canvas, 1, 1, ancho_imagen_pad - 1, alto_pad_principal - 1,
                ("#146b58" if tiene_sonido else "#2a3243"), grosor=3
            )
            _pintar_placa = None

        # Estado del mouse sobre este pad puntual, para poder
        # combinarlo con "reproduciendo" (que puede cambiar en
        # cualquier momento, sin que el mouse se haya movido) cada vez
        # que se repinta la placa.
        _estado_mouse_pad = {"hover": False, "presionado": False}

        def _refrescar_pad(hover=None, presionado=None, f=_pintar_placa, idx=i, estado=_estado_mouse_pad):
            if f is None:
                return
            if hover is not None:
                estado["hover"] = hover
            if presionado is not None:
                estado["presionado"] = presionado
            f(hover=estado["hover"], presionado=estado["presionado"],
              reproduciendo=(_sesion_reproduccion.get("indice") == idx))

        _refrescos_pads[i] = _refrescar_pad

        if _pintar_placa is not None:
            pad_canvas.bind("<Enter>", lambda e, r=_refrescar_pad: r(hover=True))
            pad_canvas.bind("<Leave>", lambda e, r=_refrescar_pad: r(hover=False))

        ruta_imagen = datos.get("imagen") if datos else None
        # La imagen ocupa EXACTAMENTE la cara del pad (misma caja y mismo
        # radio de esquina que dibuja _imagen_placa), así llega de punta a
        # punta hasta el borde del foso sin quedar descentrada ni con las
        # esquinas cortadas en un radio distinto al del pad.
        caja_cara_img, radio_cara_img = _geometria_cara_placa(ancho_imagen_pad, alto_pad_principal)
        ancho_cara_img = max(1, round(caja_cara_img[2] - caja_cara_img[0]))
        alto_cara_img = max(1, round(caja_cara_img[3] - caja_cara_img[1]))
        miniatura = (
            cargar_miniatura(i, ruta_imagen, (ancho_cara_img, alto_cara_img), radio_cara_img)
            if ruta_imagen else None
        )

        if miniatura is not None:
            pad_canvas.create_image(caja_cara_img[0], caja_cara_img[1], anchor="nw", image=miniatura)
        else:
            # Antes se dibujaba el glifo de fuente "▶": la mayoría de las
            # tipografías no lo centran dentro de su propio cuadro de
            # letra (le sobra aire de un lado), así que a simple vista se
            # veía corrido. Acá el triángulo se arma a mano con un
            # polígono cuya caja envolvente sí queda perfectamente
            # centrada en el pad, sin depender de cómo cada fuente
            # decida dibujar el carácter.
            cx = ancho_imagen_pad / 2
            cy = alto_pad_principal / 2
            lado_pad = min(ancho_imagen_pad, alto_pad_principal)
            medio_alto_tri = lado_pad * 0.17
            medio_ancho_tri = medio_alto_tri * 0.85
            pad_canvas.create_polygon(
                cx - medio_ancho_tri, cy - medio_alto_tri,
                cx - medio_ancho_tri, cy + medio_alto_tri,
                cx + medio_ancho_tri, cy,
                fill=("#eaf4ff" if tiene_sonido else "#55637d"), outline=""
            )

        def _al_presionar(e, idx=i, r=_refrescar_pad):
            r(presionado=True)
            _iniciar_arrastre_pad(idx, e)

        def _al_soltar(e, idx=i, r=_refrescar_pad):
            r(presionado=False, hover=True)
            _soltar_arrastre_pad(idx, e)

        pad_canvas.bind("<ButtonPress-1>", _al_presionar)
        pad_canvas.bind("<B1-Motion>", lambda e, idx=i: _mover_arrastre_pad(idx, e))
        pad_canvas.bind("<ButtonRelease-1>", _al_soltar)
        pad_canvas.bind("<Button-3>", lambda e, idx=i: _abrir_menu_contextual_pad(idx, e))

        etiqueta_nombre_pad = tk.Label(
            celda,
            text=datos["nombre"] if tiene_sonido else "— VACÍO —",
            bg=COLOR_PANEL_SOUNDBOARD,
            fg="white" if tiene_sonido else "#79859f",
            font=(FUENTE_UI, medida["fuente_pad_texto"], "bold"),
            wraplength=ancho_celda - 16
        )
        etiqueta_nombre_pad.pack(pady=(0, 4))
        etiqueta_nombre_pad.bind("<ButtonPress-1>", lambda e, idx=i: _iniciar_arrastre_pad(idx, e))
        etiqueta_nombre_pad.bind("<B1-Motion>", lambda e, idx=i: _mover_arrastre_pad(idx, e))
        etiqueta_nombre_pad.bind("<ButtonRelease-1>", lambda e, idx=i: _soltar_arrastre_pad(idx, e))
        etiqueta_nombre_pad.bind("<Button-3>", lambda e, idx=i: _abrir_menu_contextual_pad(idx, e))

        fila_botones = tk.Frame(celda, bg=COLOR_PANEL_SOUNDBOARD)
        fila_botones.pack()

        boton_stop = _crear_boton_circular(
            fila_botones, "■", medida["diametro_pad_chico"], medida["fuente_pad_icono"],
            "#ff5567", _detener
        )
        boton_stop.pack(side="left", padx=4)

        boton_reiniciar = _crear_boton_circular(
            fila_botones, "↻", medida["diametro_pad_chico"], medida["fuente_pad_icono"],
            "#566070", _reiniciar
        )
        boton_reiniciar.pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # BOTÓN "AGREGAR PAD": misma placa de vidrio, en formato barra ancha
    # ------------------------------------------------------------------
    alto_barra_agregar = max(62, round(76 * factor_escala_ui()))

    marco_agregar = tk.Frame(panel_soundboard, bg=COLOR_PANEL_SOUNDBOARD)
    marco_agregar.pack(fill="x")

    celda_mas = tk.Frame(
        marco_agregar, bg=COLOR_PANEL_SOUNDBOARD, height=alto_barra_agregar,
        highlightthickness=0, cursor="hand2"
    )
    celda_mas.pack(fill="x", padx=6, pady=(4, 10))
    celda_mas.pack_propagate(False)

    canvas_mas = tk.Canvas(celda_mas, bg=COLOR_PANEL_SOUNDBOARD, highlightthickness=0, cursor="hand2")
    canvas_mas.pack(fill="both", expand=True)

    _estado_mas = {"hover": False, "ancho": 0, "alto": 0}

    def _redibujar_boton_mas(event=None, forzar=False):
        ancho_mas = canvas_mas.winfo_width() or 240
        alto_mas = canvas_mas.winfo_height() or alto_barra_agregar
        if not forzar and (ancho_mas, alto_mas) == (_estado_mas["ancho"], _estado_mas["alto"]):
            return
        _estado_mas["ancho"], _estado_mas["alto"] = ancho_mas, alto_mas
        canvas_mas.delete("all")

        # Misma placa que los pads: en reposo va sin color (gris
        # azulado, igual que un pad vacío) y sólo se tiñe de verde
        # cuando el mouse está encima, para que se lea como "acción".
        placa = _placa_tk(
            ancho_mas, alto_mas,
            "#2fd693" if _estado_mas["hover"] else None,
            False, _estado_mas["hover"]
        )
        if placa is not None:
            canvas_mas.imagen_placa = placa
            canvas_mas.create_image(0, 0, anchor="nw", image=placa)
        else:
            _dibujar_boton_vidrio(canvas_mas, 1, 1, ancho_mas - 1, alto_mas - 1, "#2a3243", grosor=3)

        canvas_mas.create_text(
            ancho_mas / 2 - 72, alto_mas / 2, text="+", fill="#4fe3ae",
            font=(FUENTE_UI, medida["fuente_pad_icono"] + 10, "bold")
        )
        canvas_mas.create_text(
            ancho_mas / 2 + 16, alto_mas / 2, text="AGREGAR PAD", fill="#c3cee5",
            font=(FUENTE_UI, medida["fuente_pad_texto"] + 1, "bold")
        )

    def _hover_mas(_e, encendido):
        _estado_mas["hover"] = encendido
        _redibujar_boton_mas(forzar=True)

    canvas_mas.bind("<Configure>", _redibujar_boton_mas)
    canvas_mas.bind("<Enter>", lambda e: _hover_mas(e, True))
    canvas_mas.bind("<Leave>", lambda e: _hover_mas(e, False))
    canvas_mas.bind("<Button-1>", lambda e: agregar_pad_soundboard())


def agregar_pad_soundboard():
    """Suma un pad vacío más al soundboard (botón '+'), y lo deja
    guardado para que la próxima vez que se abra el programa ya
    aparezca la misma cantidad."""
    global num_pads_soundboard
    num_pads_soundboard += 1
    guardar_config_interfaz({"num_pads_soundboard": num_pads_soundboard})
    construir_soundboard()


# INTERVALO_VU_MS, CAIDA_DB_POR_SEG, CAIDA_POR_CUADRO,
# UMBRAL_DATOS_VIEJOS_SEG y DEBUG_VU_FUENTE ahora están todos juntos en
# la "SECCIÓN DE CALIBRACIÓN DEL MEDIDOR DE VOLUMEN", cerca del
# principio del archivo, junto con el resto de los parámetros del
# medidor.
_ultimo_debug_vu = {"t": 0.0}


def _log_debug_vu(nombre, nivel_mul_crudo, datos_vigentes, saturado, ahora):
    if DEBUG_VU_FUENTE is None or nombre != DEBUG_VU_FUENTE:
        return
    if (ahora - _ultimo_debug_vu["t"]) < INTERVALO_DEBUG_VU_SEG:
        return
    _ultimo_debug_vu["t"] = ahora
    if nivel_mul_crudo <= 0.0001:
        db_txt = "-inf"
    else:
        db_txt = f"{20 * math.log10(nivel_mul_crudo):6.1f}"
    print(
        f"[DEBUG VU] {nombre!r}  nivel={nivel_mul_crudo:.4f}  db={db_txt}  "
        f"datos_vigentes={datos_vigentes}  saturado={saturado}"
    )


def actualizar_vu_meters_ui():
    ahora = time.monotonic()
    for nombre, widgets in list(fuentes.items()):
        atenuado = widgets.get("atenuado", False)
        datos_vigentes = (ahora - ultima_actualizacion_nivel.get(nombre, 0.0)) <= UMBRAL_DATOS_VIEJOS_SEG

        # Reflejo directo de OBS: usamos el mismo nivel que OBS ya
        # calcula y reporta para esta fuente (niveles_actuales, que
        # sale de 'valor_canal' en on_input_volume_meters) -es decir,
        # el nivel DESPUÉS de fader, mute Y CUALQUIER filtro (Ganancia,
        # Compresor, etc.)-, sin reconstruir nada a mano ni aplicar
        # ninguna calibración propia. Es la manera de garantizar que
        # el medidor de acá sea IDÉNTICO al de OBS en todo momento, sea
        # cual sea la fuente y tenga o no filtros puestos: en vez de
        # adivinar cuánta ganancia suman el fader y los filtros y
        # tratar de reconstruir el resultado, usamos el resultado que
        # OBS ya calculó.
        #
        # ÚNICA excepción: mientras la fuente está MUTEADA. La mayoría
        # de las veces, mutear SÍ corta 'niveles_actuales' a silencio
        # de verdad (el mute corta la señal antes de que OBS la
        # reporte), así que ahí se reconstruye el nivel con
        # 'niveles_crudos' (el nivel "de entrada" que manda OBS aparte,
        # que sigue llegando en vivo aunque la fuente esté muteada -es
        # el mismo dato que hace que el cuadradito de "input level" del
        # mixer de OBS se siga moviendo con una fuente muteada-). Ese
        # valor YA viene con los filtros aplicados (Ganancia,
        # Compresor, etc. -comprobado: con un filtro de Ganancia
        # activo, sumarle además la ganancia del filtro a mano contaba
        # esa ganancia DOS VECES, lo que hacía que la barra se pasara
        # ~20dB para arriba y quedara pegada contra el techo casi sin
        # moverse-), así que NO hay que aplicarle de nuevo
        # 'ganancia_filtros_db'. Lo único que sí le falta para quedar
        # comparable a la barra activa es la ganancia del fader actual
        # (el valor de OBS es de ANTES del fader) y el ajuste fino de
        # CALIBRACION_VU_MUTEADO_DB (más arriba en el archivo) para lo
        # que no se puede leer de OBS.
        #
        # IMPORTANTE: para esta cuenta se usa 'niveles_crudos', que
        # guarda el mismo dato SIN recortarlo a 1.0 (a diferencia de
        # 'niveles_entrada', que sí lo recorta y está pensado para otra
        # cosa -mostrar un número acotado-). Si acá se usara la versión
        # recortada, cualquier fuente cuyo filtro de Ganancia empuje la
        # señal por encima de 0dB (algo habitual en audio de
        # escritorio, que suele entrar más "caliente" que un
        # micrófono) quedaría SIEMPRE tope en 1.0 antes de multiplicar
        # por el fader, y el resultado nunca podría superar
        # exactamente la ganancia del fader -aunque el audio real siga
        # subiendo y bajando, el medidor se queda clavado justo en la
        # posición de la barra de volumen, sin reaccionar-. Usando el
        # valor sin recortar, un pasaje que satura de verdad puede dar,
        # por ejemplo, 2.5 o 3.0 (varios dB por encima de 0dB), y al
        # multiplicarlo por el fader el resultado también puede superar
        # 1.0 con toda razón; el recorte a [0.0, 1.0] para el dibujo se
        # hace más abajo igual que para cualquier otro nivel (ver
        # 'nivel_mul'), así que no se pierde nada, sólo se lo posterga
        # hasta después de la multiplicación en vez de hacerlo antes.
        #
        # PERO: cuando la fuente muteada tiene puesto un filtro que
        # toca la ganancia (Ganancia, Compresor), 'niveles_actuales'
        # NO cae a silencio -OBS lo sigue reportando en vivo igual que
        # con la fuente sin mutear-, y en ese caso hay que usar
        # DIRECTAMENTE ese valor (ver UMBRAL_NIVEL_MUTEADO_DIRECTO, más
        # arriba en el archivo) en vez de reconstruirlo a mano. En
        # cuanto 'niveles_actuales' vuelve a estar realmente en 0 (se
        # destilencia la fuente, o es una fuente sin este tipo de
        # filtro), se usa de nuevo el reflejo exacto de OBS de arriba.
        muted = widgets.get("muted", False)
        if not datos_vigentes:
            nivel_mul_crudo = 0.0
        elif muted:
            # Antes de reconstruir nada a mano, nos fijamos si
            # 'niveles_actuales' -el mismo dato que se usa dos líneas
            # más abajo para la fuente SIN mutear, y que ahí funciona
            # perfecto- ya viene vivo por su cuenta (ver
            # UMBRAL_NIVEL_MUTEADO_DIRECTO más arriba en el archivo).
            # Con algunas fuentes OBS sigue reportando ahí el nivel
            # real aunque la fuente esté muteada, y usar ese dato
            # directamente es estrictamente mejor que reconstruirlo: es
            # el valor exacto que ya calculó OBS, sin ninguna
            # aproximación de acá. Sólo si de verdad está en 0 (mute
            # que sí corta la señal) se cae a la reconstrucción manual
            # con 'niveles_crudos'.
            nivel_directo = niveles_actuales.get(nombre, 0.0)
            if nivel_directo > UMBRAL_NIVEL_MUTEADO_DIRECTO:
                nivel_mul_crudo = nivel_directo
            else:
                nivel_entrada_crudo = niveles_crudos.get(nombre, 0.0)
                vol_db_actual = widgets["fader"].get()
                if vol_db_actual <= UMBRAL_SILENCIO:
                    ganancia_fader = 0.0
                else:
                    ganancia_fader = 10 ** (vol_db_actual / 20.0)
                ganancia_calibracion = 10 ** (CALIBRACION_VU_MUTEADO_DB / 20.0)
                nivel_mul_crudo = nivel_entrada_crudo * ganancia_fader * ganancia_calibracion
        else:
            nivel_mul_crudo = niveles_actuales.get(nombre, 0.0)

        # Si el nivel que reporta OBS llega o supera 0 dB, la fuente
        # está saturando de verdad (así lo ve OBS) y el medidor se
        # pone todo en rojo (ver _actualizar_medidor_led). Esto es
        # redundante con la detección que ya hace
        # on_input_volume_meters sobre el mismo dato crudo de OBS
        # -cualquiera de las dos alcanza-, se deja así por las dudas
        # de que el ciclo de refresco de acá agarre un valor que el
        # otro todavía no procesó.
        if nivel_mul_crudo >= UMBRAL_SATURACION_MUL:
            ultima_vez_saturado[nombre] = ahora

        nivel_mul = max(0.0, min(1.0, nivel_mul_crudo))

        if nivel_mul <= 0.0001:
            db_objetivo = -60.0
        else:
            db_objetivo = max(-60.0, min(0.0, 20 * math.log10(nivel_mul)))

        db_visual = widgets.get("vu_visual_db", -60.0)
        if db_objetivo >= db_visual:
            db_visual = db_objetivo
        else:
            db_visual = max(db_objetivo, db_visual - CAIDA_POR_CUADRO)

        widgets["vu_visual_db"] = db_visual

        # Si la fuente está atenuada (fuera de escena / con el medidor
        # en gris) la saturación se sigue marcando igual -ver
        # _actualizar_medidor_led-, sólo que en gris en vez de rojo,
        # para no perder el aviso de saturación por estar en modo gris.
        saturado = (ahora - ultima_vez_saturado.get(nombre, -math.inf)) <= DURACION_SATURACION_SEG

        _log_debug_vu(nombre, nivel_mul_crudo, datos_vigentes, saturado, ahora)

        _actualizar_medidor_led(
            widgets["vu_canvas"], widgets["vu_segmentos"], db_visual,
            atenuado=atenuado, saturado=saturado
        )

    ventana.after(INTERVALO_VU_MS, actualizar_vu_meters_ui)


def _iniciar_arrastre_panel(nombre):
    _panel_en_arrastre["origen"] = nombre


def _widget_pertenece_a(widget, contenedor):
    while widget is not None:
        if widget == contenedor:
            return True
        widget = widget.master
    return False


def _soltar_panel(nombre_actual, event):
    origen = _panel_en_arrastre["origen"]
    _panel_en_arrastre["origen"] = None
    if origen is None or origen == nombre_actual:
        return

    try:
        widget_destino = ventana.winfo_containing(event.x_root, event.y_root)
    except Exception:
        widget_destino = None
    if widget_destino is None:
        return

    contenedor_destino = marco_derecho if origen == "fuentes" else marco_fuentes
    if _widget_pertenece_a(widget_destino, contenedor_destino):
        global orden_paneles
        orden_paneles = list(reversed(orden_paneles))
        _reconstruir_interfaz_con_velo()
        guardar_config_interfaz({
            "orientacion_paneles": orientacion_paneles,
            "orden_paneles": orden_paneles,
        })



def construir_cuerpo():
    global cuerpo, marco_fuentes, marco_canvas, canvas, scrollbar_v, scrollbar_h, panel_fuentes
    global marco_derecho, barra_soundboard, marco_soundboard_scroll
    global canvas_sb, scrollbar_sb, panel_soundboard, columnas_soundboard, columnas_fuentes

    estado_previo_fuentes = []
    if cuerpo is not None:
        for nombre, w in fuentes.items():
            estado_previo_fuentes.append({
                "nombre": nombre,
                "nombre_visible": w.get("nombre_visible", nombre),
                "vol_db": w["fader"].get(),
                "muted": w.get("muted", False),
                "tipo_monitor": w.get("tipo_monitor", "OBS_MONITORING_TYPE_NONE"),
            })
        fuentes.clear()
        cuerpo.destroy()

    columnas_soundboard = 4
    columnas_fuentes = 1
    # Las tarjetas se recrean de cero más abajo, ya con el ancho
    # correcto para el tamaño actual: si dejáramos el ancho "recordado"
    # de la reconstrucción anterior, el primer _reubicar_fuentes()
    # después de esto podría creer (por error) que el ancho no cambió y
    # saltarse el reacomodo que hace falta.
    _ultimo_ancho_celda_fuentes["valor"] = None

    cuerpo = tk.PanedWindow(ventana, orient=orientacion_paneles, bg="#0b0e13", sashwidth=8, sashrelief="flat")
    cuerpo.pack(fill="both", expand=True)
    # Los widgets nuevos se apilan por encima de los que ya existían;
    # si el velo de redimensionado está puesto (ver más abajo, cerca
    # del final del archivo), hay que volver a subirlo por encima de
    # este panel recién creado para que lo siga tapando mientras se
    # arma el resto. Si el velo no existe todavía (primera vez que se
    # arma la interfaz, al arrancar el programa) o no está puesto, esto
    # no hace nada.
    try:
        velo_redimension.lift()
    except NameError:
        pass


    marco_fuentes = tk.Frame(cuerpo, bg="#10141b")

    barra_titulo_fuentes = tk.Frame(marco_fuentes, bg="#151a24", height=40)
    barra_titulo_fuentes.pack(fill="x")
    barra_titulo_fuentes.pack_propagate(False)

    tk.Frame(barra_titulo_fuentes, bg="#2fd693", width=4).pack(side="left", fill="y")
    tk.Frame(barra_titulo_fuentes, bg="#17b8b0", height=2).pack(side="bottom", fill="x")

    titulo_fuentes = tk.Label(
        barra_titulo_fuentes, text="☰  FUENTES DE AUDIO   ·   arrastrá para mover el panel",
        bg="#151a24", fg="white", font=(FUENTE_UI, 11, "bold"), cursor="fleur"
    )
    titulo_fuentes.pack(side="left", padx=12)
    titulo_fuentes.bind("<ButtonPress-1>", lambda e: _iniciar_arrastre_panel("fuentes"))
    titulo_fuentes.bind("<ButtonRelease-1>", lambda e: _soltar_panel("fuentes", e))

    marco_canvas = tk.Frame(marco_fuentes, bg="#10141b")
    marco_canvas.pack(fill="both", expand=True)

    canvas = tk.Canvas(marco_canvas, bg="#10141b", highlightthickness=0)
    scrollbar_v = ttk.Scrollbar(
        marco_canvas, orient="vertical", command=canvas.yview, style="Discreta.Vertical.TScrollbar"
    )
    scrollbar_h = ttk.Scrollbar(
        marco_canvas, orient="horizontal", command=canvas.xview, style="Discreta.Horizontal.TScrollbar"
    )
    canvas.configure(yscrollcommand=scrollbar_v.set, xscrollcommand=scrollbar_h.set)

    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar_v.grid(row=0, column=1, sticky="ns")
    scrollbar_h.grid(row=1, column=0, sticky="ew")
    marco_canvas.grid_rowconfigure(0, weight=1)
    marco_canvas.grid_columnconfigure(0, weight=1)
    scrollbar_v.grid_remove()
    scrollbar_h.grid_remove()

    panel_fuentes = tk.Frame(canvas, bg="#10141b")
    canvas.create_window((0, 0), window=panel_fuentes, anchor="nw")

    panel_fuentes.bind("<Configure>", actualizar_scroll)
    canvas.bind("<Configure>", lambda e: (actualizar_scroll(e), _al_redimensionar_fuentes(e)))
    canvas.bind("<Enter>", _activar_rueda_fuentes)
    canvas.bind("<Leave>", _desactivar_rueda_fuentes)


    marco_derecho = tk.Frame(cuerpo, bg="#10141b")

    barra_soundboard = tk.Frame(marco_derecho, bg="#151a24", height=40)
    barra_soundboard.pack(fill="x")
    barra_soundboard.pack_propagate(False)

    tk.Frame(barra_soundboard, bg="#17b8b0", width=4).pack(side="left", fill="y")
    tk.Frame(barra_soundboard, bg="#2fd693", height=2).pack(side="bottom", fill="x")

    titulo_soundboard = tk.Label(
        barra_soundboard, text="☰  Efectos De Sonido",
        bg="#151a24", fg="white", font=(FUENTE_UI, 11, "bold"), cursor="fleur"
    )
    titulo_soundboard.pack(side="left", padx=12)
    titulo_soundboard.bind("<ButtonPress-1>", lambda e: _iniciar_arrastre_panel("soundboard"))
    titulo_soundboard.bind("<ButtonRelease-1>", lambda e: _soltar_panel("soundboard", e))

    marco_soundboard_scroll = tk.Frame(marco_derecho, bg="#10141b")
    marco_soundboard_scroll.pack(fill="both", expand=True)

    canvas_sb = tk.Canvas(marco_soundboard_scroll, bg="#10141b", highlightthickness=0)
    scrollbar_sb = ttk.Scrollbar(
        marco_soundboard_scroll, orient="vertical", command=canvas_sb.yview,
        style="Discreta.Vertical.TScrollbar"
    )
    canvas_sb.configure(yscrollcommand=scrollbar_sb.set)

    canvas_sb.pack(side="left", fill="both", expand=True)

    panel_soundboard = tk.Frame(canvas_sb, bg="#10141b")
    canvas_sb.create_window((0, 0), window=panel_soundboard, anchor="nw")

    panel_soundboard.bind("<Configure>", actualizar_scroll_soundboard)
    canvas_sb.bind("<Configure>", _al_redimensionar_soundboard)
    canvas_sb.bind("<Enter>", _activar_rueda_soundboard)
    canvas_sb.bind("<Leave>", _desactivar_rueda_soundboard)


    zonas = {"fuentes": marco_fuentes, "soundboard": marco_derecho}
    minsizes = {"fuentes": 140, "soundboard": 220}

    for nombre_zona in orden_paneles:
        cuerpo.add(zonas[nombre_zona], minsize=minsizes[nombre_zona], stretch="always")

    # --------------------------------------------------------------
    # IMPORTANTE: todo lo que sigue (restaurar el divisor y calcular
    # cuántas columnas entran) se hace ACÁ MISMO, de forma sincrónica,
    # antes de crear una sola tarjeta de canal o un solo pad.
    #
    # Antes esto se hacía con ventana.after(150/200, ...), es decir,
    # unos milisegundos DESPUÉS de que construir_cuerpo() ya había
    # terminado. El problema es que quien llama a construir_cuerpo()
    # al terminar de redimensionar (_aplicar_redimension) vuelve a
    # mostrar la ventana (alpha 1) apenas construir_cuerpo() retorna,
    # sin esperar esos after(). Entonces el usuario llegaba a ver, por
    # un instante, el divisor en la posición por defecto y la grilla
    # con la distribución "de fábrica" (1 columna de canales, 4 pads),
    # y recién 150-200ms más tarde todo "saltaba" a como debía verse.
    # Eso es lo que se percibía como "se rompe todo y después se
    # arregla solo" al soltar el borde de la ventana.
    #
    # Al forzar el cálculo de geometría con update_idletasks() y
    # resolver el divisor y las columnas antes de armar el contenido,
    # la interfaz queda bien armada desde el primer cuadro que el
    # usuario llega a ver.
    # --------------------------------------------------------------
    ventana.update_idletasks()

    clave_sash = f"posicion_divisor_{orientacion_paneles}"
    posicion_guardada = config_interfaz_previa.get(clave_sash)
    if posicion_guardada is not None:
        try:
            if cuerpo.panes():
                x_actual, y_actual = cuerpo.sash_coord(0)
                if orientacion_paneles == "vertical":
                    cuerpo.sash_place(0, x_actual, int(posicion_guardada))
                else:
                    cuerpo.sash_place(0, int(posicion_guardada), y_actual)
        except Exception as e:
            print(f"No se pudo restaurar la posición del divisor: {e}")
        # El divisor recién movido cambia el ancho real de cada panel;
        # hay que dejar que se recalcule antes de medir columnas.
        ventana.update_idletasks()

    columnas_fuentes = _columnas_disponibles_fuentes()
    columnas_soundboard = _columnas_disponibles()

    for datos in estado_previo_fuentes:
        crear_fader_fuente(
            datos["nombre"], datos["vol_db"], datos["muted"], datos["tipo_monitor"],
            nombre_visible=datos["nombre_visible"]
        )
    actualizar_scroll()

    construir_soundboard()

    # Red de seguridad: si por lo que sea (fuente distinta, scrollbar
    # que aparece y come unos píxeles, etc.) la medida cambia apenas la
    # ventana termina de asentarse, esto la corrige. Se llama a la
    # versión directa (no a la que espera un toque de calma) porque acá
    # ya no hace falta esperar nada: la ventana ya terminó de moverse,
    # sólo estamos chequeando que no haya quedado nada desalineado. Como
    # ya arrancamos bien acomodados, en el caso normal estas llamadas no
    # van a encontrar ningún cambio y no van a mover nada en pantalla.
    ventana.after(30, _aplicar_redimension_soundboard)
    ventana.after(30, _aplicar_redimension_fuentes)


def actualizar_scroll(event=None):
    bbox = canvas.bbox("all")
    canvas.configure(scrollregion=bbox)
    if not bbox:
        return

    ancho_contenido = bbox[2] - bbox[0]
    alto_contenido = bbox[3] - bbox[1]
    ancho_visible = canvas.winfo_width()
    alto_visible = canvas.winfo_height()

    if alto_contenido > alto_visible + 2:
        scrollbar_v.grid()
    else:
        scrollbar_v.grid_remove()

    if ancho_contenido > ancho_visible + 2:
        scrollbar_h.grid()
    else:
        scrollbar_h.grid_remove()


def _rueda_fuentes_vertical(event):
    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


def _rueda_fuentes_horizontal(event):
    canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")


def _activar_rueda_fuentes(event):
    canvas.bind_all("<MouseWheel>", _rueda_fuentes_vertical)
    canvas.bind_all("<Shift-MouseWheel>", _rueda_fuentes_horizontal)


def _desactivar_rueda_fuentes(event):
    canvas.unbind_all("<MouseWheel>")
    canvas.unbind_all("<Shift-MouseWheel>")


def actualizar_scroll_soundboard(event=None):
    bbox = canvas_sb.bbox("all")
    canvas_sb.configure(scrollregion=bbox)
    if not bbox:
        return

    alto_contenido = bbox[3] - bbox[1]
    alto_visible = canvas_sb.winfo_height()

    if alto_contenido > alto_visible + 2:
        if not scrollbar_sb.winfo_ismapped():
            scrollbar_sb.pack(side="right", fill="y")
    else:
        if scrollbar_sb.winfo_ismapped():
            scrollbar_sb.pack_forget()


def _rueda_soundboard(event):
    canvas_sb.yview_scroll(int(-1 * (event.delta / 120)), "units")


def _activar_rueda_soundboard(event):
    canvas_sb.bind_all("<MouseWheel>", _rueda_soundboard)


def _desactivar_rueda_soundboard(event):
    canvas_sb.unbind_all("<MouseWheel>")


def _nombre_diseno_actual():
    for nombre, (orient, orden) in DISENOS.items():
        if orient == orientacion_paneles and orden == orden_paneles:
            return nombre
    return "Fuentes arriba"


def cambiar_diseno(nombre_diseno):
    global orientacion_paneles, orden_paneles
    if nombre_diseno not in DISENOS:
        return
    orientacion_paneles, orden_paneles = DISENOS[nombre_diseno]
    _reconstruir_interfaz_con_velo()
    guardar_config_interfaz({
        "orientacion_paneles": orientacion_paneles,
        "orden_paneles": orden_paneles,
    })



cargar_config_soundboard()
config_previa = cargar_config_conexion()
config_interfaz_previa = cargar_config_interfaz()

tamano_icono_actual = config_interfaz_previa.get("tamano_icono", TAMANO_ICONO_POR_DEFECTO)
if tamano_icono_actual not in TAMANOS_ICONO:
    tamano_icono_actual = TAMANO_ICONO_POR_DEFECTO

orientacion_paneles = config_interfaz_previa.get("orientacion_paneles", "vertical")
if orientacion_paneles not in ("vertical", "horizontal"):
    orientacion_paneles = "vertical"

orden_paneles = config_interfaz_previa.get("orden_paneles", ["fuentes", "soundboard"])
if not isinstance(orden_paneles, list) or set(orden_paneles) != {"fuentes", "soundboard"}:
    orden_paneles = ["fuentes", "soundboard"]

_principales_guardadas = config_interfaz_previa.get("fuentes_principales", [])
if isinstance(_principales_guardadas, list):
    fuentes_principales = set(_principales_guardadas)

_colores_guardados = config_interfaz_previa.get("colores_fuentes", {})
if isinstance(_colores_guardados, dict):
    colores_fuentes = dict(_colores_guardados)

_orden_guardado = config_interfaz_previa.get("orden_fuentes", [])
if isinstance(_orden_guardado, list):
    orden_fuentes = [n for n in _orden_guardado if isinstance(n, str)]

num_pads_soundboard = config_interfaz_previa.get("num_pads_soundboard", NUM_BOTONES_SOUNDBOARD_INICIAL)
if not isinstance(num_pads_soundboard, int) or num_pads_soundboard < 1:
    num_pads_soundboard = NUM_BOTONES_SOUNDBOARD_INICIAL
if config_soundboard:
    indices_guardados = [int(k) for k in config_soundboard.keys() if k.isdigit()]
    if indices_guardados:
        num_pads_soundboard = max(num_pads_soundboard, max(indices_guardados) + 1)

ventana = tk.Tk()
ventana.title("Consola OBS — Panel de Audio Profesional")

# Ahora que el proceso es consciente del DPI (ver el bloque del
# principio del archivo), Tk nos entrega la pantalla a resolución
# nativa: si no le avisamos, dibujaría todo del tamaño que tendría a 96
# ppp y la interfaz se vería diminuta en un monitor escalado. Este
# 'tk scaling' le dice cuántos píxeles reales vale un punto tipográfico,
# así los textos salen del tamaño correcto Y nítidos (rasterizados a la
# resolución de verdad), en vez de chicos o agrandados por el sistema.
try:
    _ppp_pantalla = ventana.winfo_fpixels("1i")
    if _ppp_pantalla > 0:
        ventana.tk.call("tk", "scaling", max(1.0, min(2.5, _ppp_pantalla / 72.0)))
except Exception:
    pass

_familias_disponibles = set(tkfont.families())
# Para el look "consola de audio profesional" se priorizan las fuentes
# modernas del sistema (Segoe UI, etc.): son las que usan los programas
# de audio de verdad y las que mejor se ven en tamaños chicos. Las
# tipografías propias (.ttf/.otf puestas en assets/fuentes) siguen
# registrándose y funcionando igual que antes, sólo que ahora quedan
# como alternativas de respaldo después de las del sistema. La
# funcionalidad (conexión, audio, configuración) no cambia en nada.
_PREFERENCIAS_FUENTE_UI = [
    "Segoe UI", "Helvetica Neue", "SF Pro Display", "Ubuntu",
    "Noto Sans", "Roboto", "Arial", "Helvetica",
] + _NOMBRES_FUENTES_PERSONALIZADAS
_PREFERENCIAS_FUENTE_TITULO = [
    "Segoe UI Semibold", "Segoe UI", "Helvetica Neue Medium",
    "Helvetica Neue", "Ubuntu Medium", "Noto Sans Medium", "Arial",
] + _NOMBRES_FUENTES_PERSONALIZADAS
FUENTE_UI = next((f for f in _PREFERENCIAS_FUENTE_UI if f in _familias_disponibles), "TkDefaultFont")
FUENTE_TITULO = next((f for f in _PREFERENCIAS_FUENTE_TITULO if f in _familias_disponibles), FUENTE_UI)
FUENTE_ICONOS = next((f for f in ("Segoe UI Symbol", "Noto Sans Symbols 2", "Arial Unicode MS", FUENTE_UI) if f in _familias_disponibles), FUENTE_UI)
FUENTE_EMOJI = next((f for f in ("Segoe UI Emoji", "Noto Color Emoji", "Noto Emoji", FUENTE_UI) if f in _familias_disponibles), FUENTE_UI)

try:
    _ico = os.path.join(CARPETA_ICONOS, "app_icon.ico")
    _png = os.path.join(CARPETA_ICONOS, "app_icon.png")
    if os.path.exists(_ico):
        ventana.iconbitmap(_ico)
    elif os.path.exists(_png) and HAY_PILLOW:
        _foto_icono_ventana = ImageTk.PhotoImage(Image.open(_png))
        ventana.iconphoto(True, _foto_icono_ventana)
except Exception:
    pass

_estilo_scrollbar = ttk.Style()
try:
    _estilo_scrollbar.theme_use("clam")                                                         
except Exception:
    pass
for _orientacion in ("Vertical", "Horizontal"):
    _estilo_scrollbar.configure(
        f"Discreta.{_orientacion}.TScrollbar",
        background="#394151", troughcolor="#10161f", bordercolor="#10161f",
        arrowcolor="#394151", relief="flat", arrowsize=10,
    )
    _estilo_scrollbar.map(
        f"Discreta.{_orientacion}.TScrollbar",
        background=[("active", "#3f4a5e")]
    )

_estilo_scrollbar.configure(
    "Discreta.TCombobox",
    fieldbackground="#283040", background="#283040", foreground="white",
    arrowcolor="#566070", bordercolor="#394151", lightcolor="#283040",
    darkcolor="#283040", relief="flat", padding=4,
)
_estilo_scrollbar.map(
    "Discreta.TCombobox",
    fieldbackground=[("readonly", "#283040")],
    foreground=[("readonly", "white")],
    bordercolor=[("focus", "#566070")],
)
ventana.option_add("*TCombobox*Listbox.background", "#202633")
ventana.option_add("*TCombobox*Listbox.foreground", "white")
ventana.option_add("*TCombobox*Listbox.selectBackground", "#566070")
ventana.option_add("*TCombobox*Listbox.font", (FUENTE_UI, 9))

ventana.geometry(config_interfaz_previa.get("geometria_ventana", "1300x760"))
ventana.minsize(900, 520)
ventana.configure(bg="#10141b")
ventana.resizable(True, True)                                           

ventana.update_idletasks()

# Cambia, una sola vez, el pincel con el que Windows borra el fondo de
# la ventana (ver _fijar_color_fondo_nativo más arriba en el archivo)
# para que coincida con este mismo color: así, hasta el propio borrado
# que hace Windows por su cuenta al agrandar la ventana ya sale del
# color correcto, sin flash claro de por medio. No hace nada en Mac/
# Linux (ver la versión de la función para esos sistemas).
_fijar_color_fondo_nativo("#10141b")

_trabajo_redimension = {"id": None}
_ultimo_factor_escala = {"valor": factor_escala_ui()}
_reconstruccion_en_curso = {"activa": False}

# ------------------------------------------------------------------
# VELO DE REDIMENSIONADO
# ------------------------------------------------------------------
# Antes, para tapar el reacomodo de los paneles al cambiar el tamaño
# de la ventana, se hacía invisible la VENTANA ENTERA con
# ventana.attributes("-alpha", 0.0)/1.0. El problema es que esto
# depende de que el gestor de ventanas del sistema operativo soporte
# bien la transparencia en tiempo real MIENTRAS la ventana se está
# redimensionando activamente (dos cosas pasando a la vez: cambio de
# tamaño + cambio de opacidad), y en varios sistemas eso es
# justamente lo que producía el corte/glitch visual: el compositor no
# llega a dibujar un cuadro completo y consistente.
#
# El siguiente reemplazo fue un Frame opaco liso (un simple rectángulo
# del color de fondo) tapando toda la ventana durante el arrastre. Eso
# evitaba ver los paneles a medio armar, pero el propio rectángulo liso
# apareciendo y desapareciendo en cada racha de arrastre SE VEÍA, a su
# vez, como un parpadeo: la interfaz "desaparecía" (tapada) y volvía a
# "aparecer" de golpe al soltar.
#
# La versión actual, en cambio, no tapa con un color liso: le saca una
# "foto" (con Pillow) a cómo se ve la ventana apenas termina cada
# reconstrucción, y esa foto es lo que se muestra en el velo la
# PRÓXIMA vez que arranca un arrastre, escalada en vivo al tamaño que
# va teniendo la ventana en cada evento. Para el ojo, es la propia
# interfaz "estirándose" con la ventana (igual que la vista previa de
# redimensionado nativa de Windows/macOS), aunque en realidad esté
# congelada y sea sólo una imagen; los widgets de verdad no se tocan
# hasta que el usuario suelta y la ventana se queda quieta, momento en
# que se reconstruye todo de una sola vez y se destapa la foto. Si
# Pillow o su captura de pantalla (ImageGrab) no están disponibles
# —por ejemplo, en algunas instalaciones de Linux—, se cae de nuevo al
# rectángulo liso de antes: sigue sin verse nada a medio construir,
# sólo que sin el efecto de foto en vivo.
velo_redimension = tk.Label(ventana, bg="#10141b", bd=0, highlightthickness=0)

_captura_ventana = {"imagen_pil": None}   # última foto buena de la interfaz completa
_foto_velo = {"tk": None}                 # referencia viva de la imagen actualmente mostrada en el velo


def _capturar_snapshot_ventana():
    """Saca una foto de cómo se ve la ventana AHORA MISMO y la guarda
    para la próxima vez que arranque un arrastre. Se llama justo
    después de cada reconstrucción (con la interfaz ya destapada y
    dibujada), nunca durante un arrastre, así que no le cuesta nada de
    fluidez al usuario: es una operación puntual, no algo que se repita
    en cada evento."""
    if not HAY_PILLOW or ImageGrab is None:
        return
    try:
        ventana.update_idletasks()
        x, y = ventana.winfo_rootx(), ventana.winfo_rooty()
        ancho, alto = ventana.winfo_width(), ventana.winfo_height()
        if ancho <= 1 or alto <= 1:
            return
        _captura_ventana["imagen_pil"] = ImageGrab.grab(bbox=(x, y, x + ancho, y + alto))
    except Exception:
        # Cualquier falla acá (por ejemplo, ImageGrab sin soporte en
        # este Linux) simplemente nos deja sin foto para la próxima vez,
        # y _mostrar_velo_redimension ya sabe caer al rectángulo liso.
        pass


def _actualizar_imagen_velo():
    """Escala la última foto guardada al tamaño actual de la ventana y
    la deja puesta en el velo. Escalar una imagen ya capturada es
    barato (no reconstruye ningún widget), así que esto sí se puede
    llamar en cada evento de arrastre sin volver a generar el lag que
    se quería eliminar: es lo que da la sensación de que la interfaz
    "sigue" al mouse en tiempo real."""
    imagen = _captura_ventana["imagen_pil"]
    ancho, alto = max(1, ventana.winfo_width()), max(1, ventana.winfo_height())
    if imagen is None:
        # Sin foto (todavía no hubo ninguna reconstrucción, o Pillow no
        # está disponible): rectángulo liso de siempre, sin imagen.
        velo_redimension.config(image="")
        _foto_velo["tk"] = None
        return
    try:
        # BILINEAR en vez de NEAREST: escalar la foto del velo con
        # vecino más cercano hacía que, mientras se arrastra el borde,
        # la interfaz congelada se viera con escalones y bordes rotos.
        # BILINEAR suaviza y sigue siendo barato de hacer por cuadro.
        escalada = imagen.resize((ancho, alto), Image.BILINEAR)
        _foto_velo["tk"] = ImageTk.PhotoImage(escalada)
        velo_redimension.config(image=_foto_velo["tk"])
    except Exception:
        velo_redimension.config(image="")
        _foto_velo["tk"] = None


def _mostrar_velo_redimension():
    # El congelado nativo (WM_SETREDRAW) sólo frena el repintado de la
    # VENTANA como tal; en Windows cada pad/botón es su propia ventana
    # nativa hija (Tk crea un HWND por widget), así que cuando se
    # destruyen y recrean durante una reconstrucción, cada uno se pinta
    # solo apenas existe, sin que el freeze de la ventana lo tape (eso
    # es el parpadeo "por capas"). El velo de Tk sí lo tapa siempre,
    # sea cual sea la cantidad de ventanas nativas por debajo, porque
    # es un hermano posicionado ENCIMA dentro del mismo árbol de Tk. Por
    # eso en todas las plataformas usamos el velo como tapa real, y en
    # Windows además sumamos el freeze nativo como refuerzo (evita el
    # micro-destello que Windows pinta solo, por su cuenta, al
    # redimensionar el marco de la ventana).
    if _ES_WINDOWS:
        _congelar_pintado_ventana()
    _actualizar_imagen_velo()
    velo_redimension.place(x=0, y=0, relwidth=1, relheight=1)
    velo_redimension.lift()


def _ocultar_velo_redimension():
    velo_redimension.place_forget()
    if _ES_WINDOWS:
        _descongelar_pintado_ventana()
    # La interfaz de verdad ya está armada y visible: es el momento
    # justo para renovar la foto, así el PRÓXIMO arrastre arranca
    # mostrando este estado (y no uno viejo).
    _capturar_snapshot_ventana()


def _reconstruir_interfaz_con_velo():
    """Reconstruye toda la interfaz (construir_cuerpo) tapándola con el
    velo mientras dura el armado. Se usa en cualquier lugar que
    necesite reconstruir todo de golpe —cambiar el tamaño de ícono,
    cambiar el diseño de paneles, soltar un panel arrastrado a otro
    lado— y no sólo al redimensionar la ventana, para que ninguna de
    esas acciones deje ver un instante con la interfaz a medio armar."""
    _mostrar_velo_redimension()
    try:
        construir_cuerpo()
        ventana.update_idletasks()
    finally:
        _ocultar_velo_redimension()


def _al_redimensionar_ventana(event):
    """Cuando el usuario cambia el tamaño de la ventana, esperamos a
    que se quede quieta (cada nuevo evento reinicia la espera) y ahí
    sí recalculamos el factor de escala y, si cambió lo suficiente,
    reconstruimos toda la interfaz para que faders, botones y pads del
    soundboard queden proporcionados al nuevo tamaño.

    Mientras dura el arrastre, los widgets de verdad NO se tocan (nada
    se reacomoda ni se recrea): lo único que pasa en cada evento es que
    la foto congelada del velo se reescala al nuevo tamaño (ver
    _actualizar_imagen_velo), así que lo que el usuario ve moverse en
    vivo es esa foto siguiendo al borde de la ventana, no la interfaz
    real reconstruyéndose a los tirones."""
    if event.widget is not ventana:
        return
    if _trabajo_redimension["id"] is None:
        _mostrar_velo_redimension()
    else:
        ventana.after_cancel(_trabajo_redimension["id"])
        # El velo (con su foto) es ahora la tapa real en todas las
        # plataformas, así que en todas hay que ir reescalando la foto
        # en cada evento para que siga el borde de la ventana en vivo.
        _actualizar_imagen_velo()
    # Este delay es el que define qué tan "quieta" tiene que quedarse
    # la ventana antes de actualizar: cada evento nuevo lo reinicia,
    # así que mientras el usuario siga moviendo el borde esto nunca
    # llega a dispararse.
    _trabajo_redimension["id"] = ventana.after(180, _aplicar_redimension)


def _aplicar_redimension():
    _trabajo_redimension["id"] = None

    # Si ya hay una reconstrucción corriendo (poco probable, pero puede
    # pasar si el usuario suelta y vuelve a arrastrar muy rápido),
    # reprogramamos para más tarde en vez de superponerla: lanzar una
    # segunda reconstrucción a mitad de la primera es lo que producía
    # los "bugs visuales" (paneles a medio armar, sashes en posiciones
    # raras, tarjetas duplicadas un instante). El velo (con su foto)
    # sigue puesto mientras tanto, así que no se ve nada raro en el medio.
    if _reconstruccion_en_curso["activa"]:
        _trabajo_redimension["id"] = ventana.after(180, _aplicar_redimension)
        return

    nuevo_factor = factor_escala_ui()
    if abs(nuevo_factor - _ultimo_factor_escala["valor"]) < 0.03:
        # El arrastre terminó pero el cambio de tamaño fue chico y no
        # amerita reconstruir nada: destapamos y listo.
        _ocultar_velo_redimension()
        return
    _ultimo_factor_escala["valor"] = nuevo_factor

    _reconstruccion_en_curso["activa"] = True
    try:
        # El velo (con la foto congelada) ya está puesto desde que
        # arrancó el arrastre (ver _al_redimensionar_ventana), así que
        # durante toda esta reconstrucción el usuario sigue viendo esa
        # foto, nunca los paneles a medio armar.
        construir_cuerpo()
        ventana.update_idletasks()
    finally:
        _ocultar_velo_redimension()
        _reconstruccion_en_curso["activa"] = False


ventana.bind("<Configure>", _al_redimensionar_ventana)


def al_cerrar():
    try:
        desconectar_obs()
    except Exception:
        pass

    try:
        datos_a_guardar = {
            "geometria_ventana": ventana.geometry(),
            "orientacion_paneles": orientacion_paneles,
            "orden_paneles": orden_paneles,
            "tamano_icono": tamano_icono_actual,
            "num_pads_soundboard": num_pads_soundboard,
            "fuentes_principales": sorted(fuentes_principales),
            "colores_fuentes": colores_fuentes,
            "orden_fuentes": list(orden_fuentes),
        }
        if cuerpo is not None and cuerpo.panes():
            x, y = cuerpo.sash_coord(0)
            clave_sash = f"posicion_divisor_{orientacion_paneles}"
            datos_a_guardar[clave_sash] = y if orientacion_paneles == "vertical" else x
        guardar_config_interfaz(datos_a_guardar)
    except Exception as e:
        print(f"No se pudo guardar la configuración de interfaz: {e}")

    ventana.destroy()


ventana.protocol("WM_DELETE_WINDOW", al_cerrar)



ALTO_CABECERA = 80
COLOR_CABECERA_ARRIBA = "#1c2637"
COLOR_CABECERA_ABAJO = "#0c111b"

cabecera_fondo = tk.Canvas(ventana, height=ALTO_CABECERA, bg=COLOR_CABECERA_ARRIBA, highlightthickness=0)
cabecera_fondo.pack(fill="x")

cabecera = tk.Frame(cabecera_fondo, bg=COLOR_CABECERA_ARRIBA)

_imagen_logo_cabecera = {"foto": None}

_trabajo_redibujado_cabecera = {"id": None}


def _redibujar_cabecera(event=None):
    # Reposicionar el frame embebido (título, chip de estado, etc.) es
    # barato — no crea nada nuevo — así que esto se hace en cada evento,
    # sin esperar a nada, para que el ancho del contenido siga a la
    # ventana sin demora.
    ancho = cabecera_fondo.winfo_width() or ventana.winfo_width() or ANCHO_VENTANA_REFERENCIA
    cabecera_fondo.coords(_ventana_cabecera_id, 0, 0)
    cabecera_fondo.itemconfig(_ventana_cabecera_id, width=ancho, height=ALTO_CABECERA)

    # Lo caro es el degradado de fondo: borra y vuelve a crear ~108
    # rectángulos (franja del degradado vertical + franja de acento).
    # Antes esto se hacía en CADA evento <Configure>, sin ningún límite
    # — y mientras se arrastra el borde de la ventana, el sistema
    # operativo manda decenas de esos eventos por segundo. Cada uno
    # disparaba una recreación completa del degradado, compitiendo por
    # CPU justo en el momento en que la interfaz necesita responder
    # rápido: esa era una de las causas concretas del lag/tildado
    # durante el arrastre (además de que, al estar todo tapado por el
    # velo mientras se arrastra, redibujar el degradado en ese momento
    # ni siquiera se llega a ver). Ahora se throttlea igual que el
    # resto de los redimensionados de la app: se espera a que la
    # cabecera deje de cambiar de ancho antes de redibujar el
    # degradado, en vez de hacerlo en cada cuadro.
    if _trabajo_redibujado_cabecera["id"] is not None:
        ventana.after_cancel(_trabajo_redibujado_cabecera["id"])
    _trabajo_redibujado_cabecera["id"] = ventana.after(120, _redibujar_degradado_cabecera)


def _redibujar_degradado_cabecera():
    _trabajo_redibujado_cabecera["id"] = None
    cabecera_fondo.delete("fondo_cabecera")
    ancho = cabecera_fondo.winfo_width() or ventana.winfo_width() or ANCHO_VENTANA_REFERENCIA
    # pasos=14 en vez de 48/60: a este tamaño de franja (unos 74px de
    # alto) el ojo no distingue 14 escalones de color de 48, pero son
    # una fracción de los rectángulos a crear cada vez que se redibuja.
    ids = _gradiente_vertical(cabecera_fondo, 0, 0, ancho, ALTO_CABECERA, COLOR_CABECERA_ARRIBA, COLOR_CABECERA_ABAJO, pasos=14)
    for iid in ids:
        cabecera_fondo.addtag_withtag("fondo_cabecera", iid)
    ids_acento = _gradiente_horizontal(cabecera_fondo, 0, ALTO_CABECERA - 3, ancho, ALTO_CABECERA, "#2fd693", "#17b8b0", pasos=14)
    for iid in ids_acento:
        cabecera_fondo.addtag_withtag("fondo_cabecera", iid)
    cabecera_fondo.tag_lower("fondo_cabecera")


_ventana_cabecera_id = cabecera_fondo.create_window(0, 0, window=cabecera, anchor="nw")
cabecera_fondo.bind("<Configure>", _redibujar_cabecera)


marco_icono_cabecera = tk.Canvas(cabecera, width=48, height=48, bg=COLOR_CABECERA_ARRIBA, highlightthickness=0)
marco_icono_cabecera.pack(side="left", padx=(20, 12), pady=16)

_ruta_logo_cabecera = os.path.join(CARPETA_ICONOS, "logo_cabecera.png")
if HAY_PILLOW and os.path.exists(_ruta_logo_cabecera):
    try:
        _img_logo = Image.open(_ruta_logo_cabecera).convert("RGBA")
        _img_logo = ImageOps.contain(_img_logo, (44, 44))
        _imagen_logo_cabecera["foto"] = ImageTk.PhotoImage(_img_logo)
        marco_icono_cabecera.create_image(24, 24, image=_imagen_logo_cabecera["foto"])
    except Exception:
        _dibujar_icono_ecualizador(marco_icono_cabecera, 24, 24, 34, color="#2fd693")
else:
    _dibujar_rect_redondeado(marco_icono_cabecera, 2, 2, 46, 46, radio=12, fill="#283040", outline="#394151", width=1)
    _dibujar_icono_ecualizador(marco_icono_cabecera, 24, 24, 26, color="#2fd693")

marco_titulos_cabecera = tk.Frame(cabecera, bg=COLOR_CABECERA_ARRIBA)
marco_titulos_cabecera.pack(side="left", pady=10)

titulo = tk.Label(
    marco_titulos_cabecera, text="CONSOLA OBS", bg=COLOR_CABECERA_ARRIBA, fg="white",
    font=(FUENTE_TITULO, 19, "bold"), anchor="w"
)
titulo.pack(anchor="w")

subtitulo = tk.Label(
    marco_titulos_cabecera, text="Panel de control de audio para OBS Studio",
    bg=COLOR_CABECERA_ARRIBA, fg="#828da6", font=(FUENTE_UI, 9), anchor="w"
)
subtitulo.pack(anchor="w")

marco_engranaje = tk.Canvas(cabecera, width=52, height=52, bg=COLOR_CABECERA_ARRIBA, highlightthickness=0, cursor="hand2")
marco_engranaje.pack(side="right", padx=(0, 18), pady=14)
_imagen_engranaje = {"foto": None}


def _geometria_faders(cx, cy, radio):
    """Medidas del ícono de Ajustes: tres faders verticales (barras con
    puntas redondeadas) con su perilla anular, la del medio arriba y las
    de los costados abajo. Todo sale en proporción a 'radio' para que el
    ícono se vea igual a cualquier tamaño."""
    ancho_barra = radio * 0.27
    medio_alto = radio * 0.92
    radio_perilla = radio * 0.36
    radio_hueco = radio * 0.145
    separacion = radio * 0.62
    barras = []
    for desplazamiento_x, y_perilla in (
        (-separacion, cy + radio * 0.30),
        (0.0, cy - radio * 0.34),
        (separacion, cy + radio * 0.30),
    ):
        x = cx + desplazamiento_x
        barras.append({
            "x": x,
            "y0": cy - medio_alto,
            "y1": cy + medio_alto,
            "y_perilla": y_perilla,
        })
    return barras, ancho_barra, radio_perilla, radio_hueco


def _dibujar_engranaje(canvas, cx, cy, radio, color="#9fb0d8"):
    """Respaldo sin Pillow: el ícono de faders dibujado con primitivas de
    Tk (queda con algún escalón en las curvas, pero sólo se usa si Pillow
    no está disponible). Conserva el nombre viejo para no tener que tocar
    los lugares donde ya se llamaba."""
    barras, ancho_barra, radio_perilla, radio_hueco = _geometria_faders(cx, cy, radio)
    for barra_icono in barras:
        canvas.create_line(
            barra_icono["x"], barra_icono["y0"], barra_icono["x"], barra_icono["y1"],
            fill=color, width=max(2, round(ancho_barra)), capstyle="round"
        )
    for barra_icono in barras:
        x, y = barra_icono["x"], barra_icono["y_perilla"]
        canvas.create_oval(
            x - radio_perilla, y - radio_perilla, x + radio_perilla, y + radio_perilla,
            fill=color, outline=""
        )
        canvas.create_oval(
            x - radio_hueco, y - radio_hueco, x + radio_hueco, y + radio_hueco,
            fill=COLOR_CABECERA_ABAJO, outline=""
        )


_cache_engranajes = {}


def _imagen_boton_engranaje(lado, color_icono, hover=False, abierto=False):
    """Botón de Ajustes completo (placa de vidrio + ícono de faders) hecho con
    Pillow al cuádruple de resolución y reducido con LANCZOS: mismo
    tratamiento que los pads, para que la cabecera no desentone ni
    muestre bordes dentados."""
    clave = (lado, color_icono, hover, abierto)
    if clave in _cache_engranajes:
        return _cache_engranajes[clave]

    base = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))

    S = 4
    capa = Image.new("RGBA", (lado * S, lado * S), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(capa)
    centro = lado * S / 2
    radio = lado * S * 0.30
    barras, ancho_barra, radio_perilla, radio_hueco = _geometria_faders(centro, centro, radio)
    relleno = _hex_a_rgb(color_icono) + (255,)
    for barra_icono in barras:
        x = barra_icono["x"]
        # Barra con puntas redondeadas = rectángulo redondeado de radio
        # igual a la mitad de su ancho.
        dibujo.rounded_rectangle(
            [x - ancho_barra / 2, barra_icono["y0"], x + ancho_barra / 2, barra_icono["y1"]],
            radius=ancho_barra / 2, fill=relleno
        )
    for barra_icono in barras:
        x, y = barra_icono["x"], barra_icono["y_perilla"]
        dibujo.ellipse([x - radio_perilla, y - radio_perilla, x + radio_perilla, y + radio_perilla],
                       fill=relleno)
    # El agujero de cada perilla se perfora DESPUÉS de dibujar las tres,
    # pisando con alpha 0: así se ve la cara del botón a través del anillo
    # (igual que en el ícono de referencia) y no la barra que pasa detrás.
    for barra_icono in barras:
        x, y = barra_icono["x"], barra_icono["y_perilla"]
        dibujo.ellipse([x - radio_hueco, y - radio_hueco, x + radio_hueco, y + radio_hueco],
                       fill=(0, 0, 0, 0))
    capa = capa.resize((lado, lado), Image.LANCZOS)

    base.alpha_composite(capa)
    foto = ImageTk.PhotoImage(base)
    _cache_engranajes[clave] = foto
    return foto


_estado_engranaje = {"hover": False, "abierto": False}


def _redibujar_icono_engranaje(event=None):
    marco_engranaje.delete("all")
    lado = marco_engranaje.winfo_width() or 46
    marco_engranaje.config(bg=COLOR_CABECERA_ARRIBA)
    if HAY_PILLOW:
        color = "#0c111b" if _estado_engranaje["abierto"] else (
            "#d7e6ff" if _estado_engranaje["hover"] else "#aebbd8"
        )
        try:
            foto = _imagen_boton_engranaje(
                lado, color, _estado_engranaje["hover"], _estado_engranaje["abierto"]
            )
            _imagen_engranaje["foto"] = foto
            marco_engranaje.create_image(0, 0, anchor="nw", image=foto)
            return
        except Exception:
            pass
    color = "#2fd693" if (_estado_engranaje["hover"] or _estado_engranaje["abierto"]) else "#9fb0d8"
    margen = max(2, round(lado * 0.14))
    _dibujar_engranaje(marco_engranaje, lado / 2, lado / 2, lado / 2 - margen, color=color)


marco_engranaje.bind("<Configure>", _redibujar_icono_engranaje)
marco_engranaje.bind("<Button-1>", lambda e: _alternar_menu_ajustes(e))


def _hover_engranaje_dentro(event=None):
    _estado_engranaje["hover"] = True
    _redibujar_icono_engranaje()


def _hover_engranaje_fuera(event=None):
    _estado_engranaje["hover"] = False
    _redibujar_icono_engranaje()


marco_engranaje.bind("<Enter>", _hover_engranaje_dentro)
marco_engranaje.bind("<Leave>", _hover_engranaje_fuera)
boton_engranaje = marco_engranaje

estado_chip = tk.Frame(cabecera, bg="#141a26", highlightbackground="#3d4657", highlightthickness=1)
estado_chip.pack(side="right", padx=20, pady=20)

estado = tk.Label(
    estado_chip, text="● DESCONECTADO", bg="#141a26", fg="#ff5d6c",
    font=(FUENTE_UI, 10, "bold"), padx=14, pady=6
)
estado.pack()



# ------------------------------------------------------------------
# MENÚ DESPLEGABLE DE AJUSTES
# ------------------------------------------------------------------
# Antes todo esto era una BARRA fija cruzando la ventana: host, puerto,
# contraseña, botones y selectores siempre a la vista, robando alto útil
# a los faders y a los pads. Ahora es un panel que cuelga del botón de
# engranaje, organizado en secciones (Conexión / Apariencia) en vertical
# -como el menú de ajustes de cualquier programa moderno-, y que se
# cierra solo al hacer clic en cualquier otro lado, al apretar Escape o
# al mover la ventana.
ANCHO_MENU_AJUSTES = 340
COLOR_MENU_FONDO = "#121722"
COLOR_MENU_BORDE = "#2b3548"
COLOR_MENU_CAMPO = "#1b2230"
COLOR_MENU_TITULO = "#6f7d99"
COLOR_MENU_TEXTO = "#c3cee5"

ventana_ajustes = tk.Toplevel(ventana, bg=COLOR_MENU_BORDE)
ventana_ajustes.overrideredirect(True)
ventana_ajustes.withdraw()
try:
    ventana_ajustes.attributes("-topmost", True)
except Exception:
    pass

# El borde del menú es el propio Toplevel asomando 1px alrededor del
# marco interior: es la forma simple de tener un contorno prolijo en una
# ventana sin decoración.
barra = tk.Frame(ventana_ajustes, bg=COLOR_MENU_FONDO)
barra.pack(fill="both", expand=True, padx=1, pady=1)

_encabezado_menu = tk.Frame(barra, bg=COLOR_MENU_FONDO)
_encabezado_menu.pack(fill="x", padx=16, pady=(14, 8))

tk.Label(
    _encabezado_menu, text="AJUSTES", bg=COLOR_MENU_FONDO, fg="white",
    font=(FUENTE_TITULO, 12, "bold")
).pack(side="left")

_cerrar_menu = tk.Label(
    _encabezado_menu, text="✕", bg=COLOR_MENU_FONDO, fg="#6f7d99",
    font=(FUENTE_UI, 11, "bold"), cursor="hand2"
)
_cerrar_menu.pack(side="right")
_cerrar_menu.bind("<Button-1>", lambda e: _cerrar_menu_ajustes())
_cerrar_menu.bind("<Enter>", lambda e: _cerrar_menu.config(fg="#ff5d6c"))
_cerrar_menu.bind("<Leave>", lambda e: _cerrar_menu.config(fg="#6f7d99"))


def _seccion_menu(texto):
    """Título de sección + línea separadora, para que el menú se lea
    como un panel de ajustes y no como una lista suelta de campos."""
    contenedor = tk.Frame(barra, bg=COLOR_MENU_FONDO)
    contenedor.pack(fill="x", padx=16, pady=(10, 2))
    tk.Label(
        contenedor, text=texto, bg=COLOR_MENU_FONDO, fg=COLOR_MENU_TITULO,
        font=(FUENTE_UI, 8, "bold")
    ).pack(side="left")
    linea = tk.Frame(contenedor, bg="#232c3d", height=1)
    linea.pack(side="left", fill="x", expand=True, padx=(10, 0), pady=(6, 0))
    return contenedor


def _fila_menu(texto, widget_ancho=None):
    """Una fila 'etiqueta a la izquierda, control a la derecha'."""
    fila = tk.Frame(barra, bg=COLOR_MENU_FONDO)
    fila.pack(fill="x", padx=16, pady=4)
    tk.Label(
        fila, text=texto, bg=COLOR_MENU_FONDO, fg=COLOR_MENU_TEXTO,
        font=(FUENTE_UI, 9), width=11, anchor="w"
    ).pack(side="left")
    return fila


def _entrada_menu(padre, **extras):
    entrada = tk.Entry(
        padre, bg=COLOR_MENU_CAMPO, fg="white", insertbackground="white", relief="flat",
        highlightthickness=1, highlightbackground="#2b3548", highlightcolor="#2fd693",
        font=(FUENTE_UI, 10), **extras
    )
    entrada.pack(side="left", fill="x", expand=True, ipady=4)
    return entrada


_seccion_menu("CONEXIÓN")

entrada_host = _entrada_menu(_fila_menu("Host"))
entrada_host.insert(0, config_previa.get("host", "localhost"))

entrada_puerto = _entrada_menu(_fila_menu("Puerto"))
entrada_puerto.insert(0, config_previa.get("puerto", "4455"))

entrada_password = _entrada_menu(_fila_menu("Contraseña"), show="•")
entrada_password.insert(0, config_previa.get("password", ""))

_fila_acciones = tk.Frame(barra, bg=COLOR_MENU_FONDO)
_fila_acciones.pack(fill="x", padx=16, pady=(12, 4))

boton_conectar = tk.Button(
    _fila_acciones, text="CONECTAR", bg="#2fd693", fg="#0c111b",
    activebackground="#4fe3ae", activeforeground="#0c111b",
    relief="flat", bd=0, pady=7, font=(FUENTE_UI, 9, "bold"), cursor="hand2",
    command=conectar_obs
)
boton_conectar.pack(side="left", fill="x", expand=True)

boton_actualizar = tk.Button(
    barra, text="ACTUALIZAR FUENTES", bg="#242d3d", fg=COLOR_MENU_TEXTO,
    activebackground="#2f3a4d", activeforeground="white",
    relief="flat", bd=0, pady=7, font=(FUENTE_UI, 9, "bold"), cursor="hand2",
    command=actualizar
)
boton_actualizar.pack(fill="x", padx=16, pady=(6, 2))

_seccion_menu("APARIENCIA")

variable_tamano_icono = tk.StringVar(value=tamano_icono_actual)
selector_tamano_icono = ttk.Combobox(
    _fila_menu("Íconos"),
    textvariable=variable_tamano_icono,
    values=list(TAMANOS_ICONO.keys()),
    state="readonly",
    style="Discreta.TCombobox"
)
selector_tamano_icono.pack(side="left", fill="x", expand=True)
selector_tamano_icono.bind(
    "<<ComboboxSelected>>",
    lambda e: cambiar_tamano_icono(variable_tamano_icono.get())
)

variable_diseno = tk.StringVar(value=_nombre_diseno_actual())
selector_diseno = ttk.Combobox(
    _fila_menu("Diseño"),
    textvariable=variable_diseno,
    values=list(DISENOS.keys()),
    state="readonly",
    style="Discreta.TCombobox"
)
selector_diseno.pack(side="left", fill="x", expand=True)
selector_diseno.bind(
    "<<ComboboxSelected>>",
    lambda e: cambiar_diseno(variable_diseno.get())
)

tk.Frame(barra, bg=COLOR_MENU_FONDO, height=14).pack(fill="x")


def _posicionar_menu_ajustes():
    """Cuelga el menú justo debajo del engranaje, alineado a la derecha,
    y lo corre hacia adentro si se saldría de la pantalla."""
    ventana_ajustes.update_idletasks()
    alto = max(220, ventana_ajustes.winfo_reqheight())
    x = boton_engranaje.winfo_rootx() + boton_engranaje.winfo_width() - ANCHO_MENU_AJUSTES
    y = boton_engranaje.winfo_rooty() + boton_engranaje.winfo_height() + 10
    x = max(8, min(x, ventana.winfo_screenwidth() - ANCHO_MENU_AJUSTES - 8))
    y = max(8, min(y, ventana.winfo_screenheight() - alto - 8))
    ventana_ajustes.geometry(f"{ANCHO_MENU_AJUSTES}x{alto}+{int(x)}+{int(y)}")


def _abrir_menu_ajustes():
    _posicionar_menu_ajustes()
    ventana_ajustes.deiconify()
    ventana_ajustes.lift()
    _estado_engranaje["abierto"] = True
    _redibujar_icono_engranaje()
    ventana_ajustes.after(80, lambda: entrada_host.focus_set())


def _cerrar_menu_ajustes(event=None):
    ventana_ajustes.withdraw()
    _estado_engranaje["abierto"] = False
    _redibujar_icono_engranaje()


def _menu_ajustes_visible():
    try:
        return ventana_ajustes.state() != "withdrawn"
    except Exception:
        return False


def _alternar_menu_ajustes(event=None):
    if _menu_ajustes_visible():
        _cerrar_menu_ajustes()
    else:
        _abrir_menu_ajustes()


def _clic_fuera_del_menu(event=None):
    """Cierra el menú al tocar cualquier parte de la ventana principal,
    salvo el propio engranaje (ese ya alterna por su cuenta; si no lo
    exceptuáramos, abriría y cerraría en el mismo clic)."""
    if not _menu_ajustes_visible():
        return
    if event is not None and event.widget is marco_engranaje:
        return
    _cerrar_menu_ajustes()


ventana.bind("<Button-1>", _clic_fuera_del_menu, add="+")
ventana.bind("<Escape>", _cerrar_menu_ajustes, add="+")
ventana_ajustes.bind("<Escape>", _cerrar_menu_ajustes)


def _seguir_ventana_con_menu(event=None):
    """Si la ventana principal se mueve o cambia de tamaño con el menú
    abierto, el menú se reacomoda debajo del engranaje en vez de quedar
    flotando suelto en la pantalla."""
    if event is not None and event.widget is not ventana:
        return
    if _menu_ajustes_visible():
        _posicionar_menu_ajustes()


ventana.bind("<Configure>", _seguir_ventana_con_menu, add="+")



construir_cuerpo()
actualizar_vu_meters_ui()
_programar_refresco_ganancia()
_programar_refresco_reproduccion()
if not _ES_WINDOWS:
    # Foto inicial de la interfaz recién armada (sólo hace falta en el
    # respaldo de Mac/Linux; en Windows el congelado nativo no usa
    # ninguna foto), para que si el usuario redimensiona la ventana
    # como primer gesto el velo ya tenga una imagen real para mostrar,
    # en vez de arrancar en blanco la primera vez.
    ventana.after(200, _capturar_snapshot_ventana)

if not HAY_PILLOW:
    ventana.after(500, lambda: messagebox.showwarning(
        "Falta instalar Pillow",
        "No se encontró (ni se pudo instalar automáticamente) el paquete "
        "'Pillow', necesario para que las imágenes de los pads funcionen "
        "bien.\n\nSin Pillow, los archivos JPG no se pueden mostrar y "
        "ninguna imagen se va a achicar al tamaño del pad.\n\n"
        "Instalalo manualmente abriendo una terminal y ejecutando:\n"
        "pip install Pillow\n\nDespués volvé a abrir el programa."
    ))



if __name__ == "__main__":
    ventana.mainloop()
