# ConsolaOBS v2

Panel de control de audio para **OBS Studio**, reconstruido desde cero (sucesor del `ConsolaOBS.exe` v1).
Python 3.13 + PySide6, conexión por **OBS WebSocket v5**, reproducción por **OBS** y/o **VB-Audio Virtual Matrix**.

## Características

- **Soundboard**: grilla de pads con nombre, sonido e imagen, drag & drop para reordenar,
  menú contextual (reproducir/detener, asignar sonido/imagen, renombrar, color, modo, eliminar),
  clic para reproducir / volver a cliquear para detener.
- **Mixer en tiempo real**: VU meters LED, fader de volumen (dB), mute, selector de monitoreo
  y renombrado de fuentes. Los eventos vienen de OBS en tiempo real.
- **Editor de filtros**: agregar/eliminar/habilitar filtros por fuente y editar sus settings (JSON).
- **Dos motores de reproducción** por pad:
  - `obs` → único sonido a la vez a través de la fuente media `Soundboard_Efectos` (el de siempre).
  - `local` → polifónico a través de un dispositivo virtual **VB-Audio Matrix**.
  - Cada pad puede fijar su modo (`global` = sigue al modo global del panel).

## Configuración centralizada (un solo archivo)

Todo vive en **`config.toml`** (en la raíz del proyecto, generado automáticamente al primer arranque):

```toml
[conexion]
host = "localhost"
puerto = 4455

[reproduccion]
modo_global = "obs"            # "obs" | "local"
dispositivo_local = "VB-Audio Matrix"

[interfaz]
geometria = "1200x800+100+50"
maximizada = false
tamano_iconos = "mediano"      # chico | mediano | grande
posicion_divisor = 0.4

[soundboard]
columnas = 4
volumen_local = 0.9

[[soundboard.pads]]
nombre = "pop"
archivo = "D:/Sonidos/pop.mp3"
imagen  = "D:/Imagenes/pop.jpg"
color   = "#43a047"
modo    = "global"             # global | obs | local
```

**La contraseña de OBS NO está en ningún archivo**: se guarda encriptada en el
**Windows Credential Manager** (Vault) vía `keyring`. Host y puerto sí van en `config.toml`.

## Requisitos

1. **OBS Studio** corriendo con el servidor WebSocket v5 habilitado.
2. **VB-Audio Virtual Matrix** (solo si querés usar el modo local): https://vb-audio.com/Matrix/
3. **Python 3.13+** (para ejecutar el proyecto).

## Configurar OBS WebSocket

1. Abrí **OBS Studio**.
2. Andá a **Configuración → Herramientas → WebSocket Server Settings**.
3. Habilitá **Enable WebSocket server**.
4. En **Port** dejalo en **4455** (o el que prefieras).
5. En **Server Password** poné una contraseña (la app la guarda en el Vault de Windows, nunca en un archivo).
6. Click **OK** y reiniciá OBS si lo pide.

La app se conecta automáticamente al host/puerto que figuran en `config.toml` (`[conexion]`).
Al conectar, te pide la contraseña y la guarda en el **Windows Credential Manager** para que no tengas que reingresarla.

## Modo OBS (por defecto)

No necesita VB-Matrix. Los pads reproducen a través de una fuente **ffmpeg_source** llamada
`Soundboard_Efectos` que ya debe existir en OBS (se crea automáticamente si no está).
Un solo sonido a la vez. Ideal para efectos simples.

## Modo local (VB-Matrix) — polifónico

Permite varios pads sonando simultáneamente a través de un dispositivo virtual.

### 1. Instalar VB-Matrix

Descargá e instalá **VB-Audio Virtual Matrix** desde https://vb-audio.com/Matrix/
(y reiniciá el PC si es la primera vez).

### 2. Configurar el enrutamiento en VBMatrix

Abrí la app **VBMatrix**. Vas a ver una grilla con entradas (columnas) y salidas (filas).

- **VAIO1 In** → conectá a **VAIO1 Out** (para que el audio de la app llegue a OBS).
- Si también querés escucharlo vos: conectá **VAIO1 In** → tu auricular/speakers.

### 3. Crear la fuente en OBS

1. En OBS, andá a **Fuentes → + → Audio Input Capture**.
2. Ponle un nombre (ej: "Panel de Sonidos").
3. En **Device** seleccioná **"VBMatrix In 1-8 (VB-Audio Matrix VAIO)"**.
4. Click **OK**.

Ahora cuando la app reproduce en modo local, el audio llega a OBS por esa fuente.
Agregala a tus escenas junto a las demás fuentes de audio.

### 4. Elegir el modo en la app

En `config.toml` → `[reproduccion]`:

```toml
modo_global = "local"          # "obs" o "local"
dispositivo_local = "VB-Audio Matrix"
```

O en la app: cada pad tiene su propio modo (global/obs/local) en el menú contextual.

## Ejecución en desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m src.main
```

La app es un proyecto **Python directamente ejecutable** (sin empaquetar a .exe), así que todo queda
modificable. Para que atasque doble clic, se puede crear un acceso directo que apunte a
`.venv\Scripts\pythonw.exe -m src.main` (directorio de trabajo = raíz del proyecto).

## Estructura

```
src/
├── main.py                # entry point (python -m src.main)
├── app.py                 # QApplication + carga de config
├── config/
│   ├── config_toml.py     # configuración centralizada (config.toml)
│   └── credenciales.py    # contraseña en Windows Credential Manager
├── obs/
│   ├── cliente.py         # cliente OBS WS v5 thread-safe
│   ├── eventos.py         # eventos OBS → señales Qt
│   └── canales.py         # (reservado) modelo de canales
├── audio/
│   ├── motor.py           # interfaz de motores
│   ├── motor_obs.py       # reproducción vía OBS (media source)
│   └── motor_local.py     # reproducción vía miniaudio → VB-Matrix
└── ui/
    ├── ventana.py         # ventana principal (splitter Mixer|Soundboard)
    ├── conexion_widget.py # host/puerto/contraseña + estado
    ├── panel_soundboard.py# grilla de pads
    ├── pad_widget.py      # pad individual (dibujado propio, drag&drop)
    ├── panel_mixer.py     # tarjetas de fuentes con VU/fader/mute/monitor
    └── dialogo_filtros.py # editor de filtros
```

## Migración desde el v1

El `config_soundboard.json` del v1 no se reutiliza. Al primer arranque se crea `config.toml` con
pads vacíos: reasigná sonido e imagen por pad. La contraseña de OBS del v1 quedó solo en el Vault
(debe reingresarse una vez si no se guardó).