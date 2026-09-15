"""Panel de mixer: vistas compacta y detalle de fuentes de audio.

Vista compacta: filas delgadas (VU, nombre, volumen, mute).
Vista detalle: tarjetas actuales + panel derecho con parametros de la fuente
seleccionada (volumen, monitoreo, filtros y ecualizador).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .dialogo_filtros import WidgetFiltros

_NUM_SEGMENTOS = 24
_DB_MIN = -60.0
_DB_MAX = 0.0

_ICONO_VIEWS = {"compacta": "compacta", "detalle": "detalle"}


# ── VU Meter (vertical y horizontal) ────────────────────────────────────────

class VUMeter(QWidget):
    def __init__(self, horizontal: bool = False, parent=None):
        super().__init__(parent)
        self._horizontal = horizontal
        self._nivel_db: float = _DB_MIN
        self._peak_db: float = _DB_MIN
        if horizontal:
            self.setFixedHeight(14)
            self.setMinimumWidth(80)
        else:
            self.setFixedWidth(14)
            self.setMinimumHeight(120)

    def set_nivel(self, nivel_db: float):
        self._nivel_db = max(_DB_MIN, min(_DB_MAX, nivel_db))
        self.update()

    def set_peak(self, peak_db: float):
        self._peak_db = max(_DB_MIN, min(_DB_MAX, peak_db))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        margen = 3
        gap = 2
        if self._horizontal:
            w, h = self.width(), self.height()
            largo_seg = (w - margen * 2) / _NUM_SEGMENTOS
            for i in range(_NUM_SEGMENTOS):
                db = _DB_MIN + (_DB_MAX - _DB_MIN) * i / _NUM_SEGMENTOS
                x = margen + i * largo_seg
                color = self._color_segmento(db)
                if db <= self._nivel_db:
                    pass
                elif db <= self._peak_db:
                    color = color.lighter(130)
                else:
                    color = QColor("#333333")
                p.fillRect(int(x), margen, max(int(largo_seg - gap), 1), h - margen * 2, color)
        else:
            w, h = self.width(), self.height()
            alto_seg = (h - margen * 2) / _NUM_SEGMENTOS
            for i in range(_NUM_SEGMENTOS):
                db = _DB_MIN + (_DB_MAX - _DB_MIN) * i / _NUM_SEGMENTOS
                y = h - margen - (i + 1) * alto_seg
                color = self._color_segmento(db)
                if db <= self._nivel_db:
                    pass
                elif db <= self._peak_db:
                    color = color.lighter(130)
                else:
                    color = QColor("#333333")
                p.fillRect(margen, int(y), w - margen * 2, max(int(alto_seg - gap), 1), color)
        p.end()

    @staticmethod
    def _color_segmento(db: float) -> QColor:
        if db < -18:
            return QColor("#2e7d32")
        if db < -6:
            return QColor("#f9a825")
        return QColor("#e53935")


# ── Tarjeta de Fuente (vista detalle) ───────────────────────────────────────

class TarjetaFuente(QWidget):
    senial_volumen_cambiado = Signal(str, float)   # nombre, vol_db
    senial_mudo_cambiado = Signal(str, bool)        # nombre, muted
    senial_monitor_cambiado = Signal(str, str)      # nombre, tipo
    senial_seleccion = Signal(str)                  # nombre fuente
    senial_renombrar = Signal(str, str)             # viejo, nuevo

    def __init__(self, nombre: str, parent=None):
        super().__init__(parent)
        self.nombre = nombre
        self._mudo = False
        self._monitor = "OBS_MONITORING_TYPE_NONE"
        self._db_actual = 0.0
        self._seleccionada = False
        self._construir_ui()

    def _construir_ui(self):
        self.setMinimumWidth(150)
        self.setMaximumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._aplicar_estilo()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._lbl_nombre = QLabel(self.nombre)
        self._lbl_nombre.setStyleSheet("color:#eee;font-weight:bold;font-size:12px;")
        self._lbl_nombre.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_nombre.mouseDoubleClickEvent = self._doble_clic_nombre
        layout.addWidget(self._lbl_nombre)

        fila = QHBoxLayout()
        fila.setSpacing(6)
        self._vu = VUMeter()
        fila.addWidget(self._vu)

        self._fader = QSlider(Qt.Orientation.Vertical)
        self._fader.setRange(-600, 120)   # dB * 10
        self._fader.setValue(0)
        self._fader.setFixedWidth(28)
        self._fader.setStyleSheet(
            "QSlider::groove:vertical{background:#444;width:6px;border-radius:3px;}"
            "QSlider::handle:vertical{background:#4a9eff;width:16px;height:16px;"
            "margin:-5px;border-radius:8px;}"
            "QSlider::sub-page:vertical{background:#4a9eff;border-radius:3px;}"
        )
        self._fader.valueChanged.connect(self._fader_cambio)
        fila.addWidget(self._fader)
        layout.addLayout(fila)

        self._lbl_db = QLabel("0.0 dB")
        self._lbl_db.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_db.setStyleSheet("color:#aaa;font-size:10px;")
        layout.addWidget(self._lbl_db)

        self._btn_mute = QPushButton("M")
        self._btn_mute.setFixedSize(28, 28)
        self._btn_mute.setCheckable(True)
        self._btn_mute.clicked.connect(self._toggle_mute)
        self._btn_mute.setStyleSheet(
            "QPushButton{background:#444;color:#aaa;border:none;border-radius:4px;"
            "font-weight:bold;font-size:11px;}"
            "QPushButton:checked{background:#e53935;color:#fff;}"
            "QPushButton:hover{background:#555;}"
        )
        layout.addWidget(self._btn_mute, alignment=Qt.AlignmentFlag.AlignCenter)

        self._combo_monitor = QComboBox()
        self._combo_monitor.addItems(["Sin monitoreo", "Solo yo", "Yo + stream"])
        self._combo_monitor.currentIndexChanged.connect(self._monitor_cambio)
        self._combo_monitor.setFixedHeight(24)
        self._combo_monitor.setStyleSheet(
            "QComboBox{background:#1e1e1e;color:#ccc;border:1px solid #555;"
            "border-radius:3px;padding:2px;font-size:10px;}"
            "QComboBox::drop-down{border:none;width:16px;}"
            "QComboBox QAbstractItemView{background:#1e1e1e;color:#ccc;}"
        )
        layout.addWidget(self._combo_monitor)
        layout.addStretch()

    # -- actualizaciones externas ---------------------------------------------

    def actualizar_nivel(self, nivel_db: float, peak_db: float = 0.0):
        self._vu.set_nivel(nivel_db)
        self._vu.set_peak(peak_db)

    def actualizar_volumen(self, vol_db: float):
        self._db_actual = vol_db
        self._fader.blockSignals(True)
        self._fader.setValue(int(vol_db * 10))
        self._fader.blockSignals(False)
        self._lbl_db.setText(f"{vol_db:.1f} dB")

    def actualizar_mudo(self, muted: bool):
        self._mudo = muted
        self._btn_mute.blockSignals(True)
        self._btn_mute.setChecked(muted)
        self._btn_mute.blockSignals(False)
        self._lbl_nombre.setStyleSheet(
            ("color:#777;font-weight:bold;font-size:12px;" if muted else "color:#eee;font-weight:bold;font-size:12px;")
        )

    def actualizar_monitor(self, tipo: str):
        self._monitor = tipo
        mapa = {"OBS_MONITORING_TYPE_NONE": 0, "OBS_MONITORING_TYPE_MONITOR_ONLY": 1, "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT": 2}
        self._combo_monitor.blockSignals(True)
        self._combo_monitor.setCurrentIndex(mapa.get(tipo, 0))
        self._combo_monitor.blockSignals(False)

    def actualizar_estado_escena(self, activa: bool):
        self.setEnabled(activa)
        self._aplicar_estilo()

    def marcar_seleccionada(self, seleccionada: bool):
        self._seleccionada = seleccionada
        self._aplicar_estilo()

    def _aplicar_estilo(self):
        if self._seleccionada:
            base = "TarjetaFuente{background:#26415e;border:2px solid #4a9eff;border-radius:8px;padding:4px;}"
        else:
            base = "TarjetaFuente{background:#2d2d2d;border-radius:8px;padding:6px;}"
        self.setStyleSheet(base + "QLabel{color:#ccc;}")

    # -- interaccion ----------------------------------------------------------

    def mousePressEvent(self, event):
        self.senial_seleccion.emit(self.nombre)
        super().mousePressEvent(event)

    def _doble_clic_nombre(self, event):
        from PySide6.QtWidgets import QInputDialog
        nuevo, ok = QInputDialog.getText(self, "Renombrar fuente", "Nombre:", text=self.nombre)
        if ok and nuevo.strip() and nuevo.strip() != self.nombre:
            self.senial_renombrar.emit(self.nombre, nuevo.strip())

    def _fader_cambio(self, valor: int):
        db = valor / 10.0
        self._lbl_db.setText(f"{db:.1f} dB")
        self.senial_volumen_cambiado.emit(self.nombre, db)

    def _toggle_mute(self):
        self._mudo = self._btn_mute.isChecked()
        self.senial_mudo_cambiado.emit(self.nombre, self._mudo)

    def _monitor_cambio(self, idx: int):
        tipos = ["OBS_MONITORING_TYPE_NONE", "OBS_MONITORING_TYPE_MONITOR_ONLY", "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"]
        self._monitor = tipos[idx]
        self.senial_monitor_cambiado.emit(self.nombre, self._monitor)


# ── Fila compacta ────────────────────────────────────────────────────────────

class FilaCompacta(QWidget):
    senial_volumen_cambiado = Signal(str, float)   # nombre, vol_db
    senial_mudo_cambiado = Signal(str, bool)
    senial_seleccion = Signal(str)
    senial_renombrar = Signal(str, str)

    def __init__(self, nombre: str, parent=None):
        super().__init__(parent)
        self.nombre = nombre
        self._mudo = False
        self.setMinimumHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        self._vu = VUMeter(horizontal=True)
        self._vu.setMinimumWidth(64)
        layout.addWidget(self._vu)

        self._lbl_nombre = QLabel(nombre)
        self._lbl_nombre.setStyleSheet("color:#ddd;font-size:11px;font-weight:bold;")
        self._lbl_nombre.setMinimumWidth(120)
        self._lbl_nombre.mouseDoubleClickEvent = self._doble_clic_nombre
        layout.addWidget(self._lbl_nombre)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(-600, 120)   # dB * 10
        self._slider.setValue(0)
        self._slider.setFixedWidth(110)
        self._slider.setStyleSheet(
            "QSlider::groove:horizontal{background:#444;height:4px;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#4a9eff;width:12px;height:12px;"
            "margin:-4px;border-radius:6px;}"
            "QSlider::sub-page:horizontal{background:#4a9eff;border-radius:2px;}"
        )
        self._slider.valueChanged.connect(self._slider_cambio)
        layout.addWidget(self._slider)

        self._lbl_db = QLabel("0.0 dB")
        self._lbl_db.setStyleSheet("color:#aaa;font-size:10px;")
        self._lbl_db.setMinimumWidth(52)
        layout.addWidget(self._lbl_db)

        self._btn_mute = QPushButton("M")
        self._btn_mute.setFixedSize(24, 24)
        self._btn_mute.setCheckable(True)
        self._btn_mute.clicked.connect(self._toggle_mute)
        self._btn_mute.setStyleSheet(
            "QPushButton{background:#444;color:#aaa;border:none;border-radius:3px;"
            "font-weight:bold;font-size:10px;}"
            "QPushButton:checked{background:#e53935;color:#fff;}"
            "QPushButton:hover{background:#555;}"
        )
        layout.addWidget(self._btn_mute)

    def actualizar_nivel(self, nivel_db: float, peak_db: float = 0.0):
        self._vu.set_nivel(nivel_db)
        self._vu.set_peak(peak_db)

    def actualizar_volumen(self, vol_db: float):
        self._slider.blockSignals(True)
        self._slider.setValue(int(vol_db * 10))
        self._slider.blockSignals(False)
        self._lbl_db.setText(f"{vol_db:.1f} dB")

    def actualizar_mudo(self, muted: bool):
        self._mudo = muted
        self._btn_mute.blockSignals(True)
        self._btn_mute.setChecked(muted)
        self._btn_mute.blockSignals(False)
        self._lbl_nombre.setStyleSheet(
            ("color:#777;font-size:11px;font-weight:bold;" if muted else "color:#ddd;font-size:11px;font-weight:bold;")
        )

    def actualizar_estado_escena(self, activa: bool):
        self._lbl_nombre.setStyleSheet(
            ("color:#666;font-size:11px;font-weight:bold;" if not activa else "color:#ddd;font-size:11px;font-weight:bold;")
        )

    def mousePressEvent(self, event):
        self.senial_seleccion.emit(self.nombre)
        super().mousePressEvent(event)

    def _slider_cambio(self, valor: int):
        db = valor / 10.0
        self._lbl_db.setText(f"{db:.1f} dB")
        self.senial_volumen_cambiado.emit(self.nombre, db)

    def _toggle_mute(self):
        self._mudo = self._btn_mute.isChecked()
        self.senial_mudo_cambiado.emit(self.nombre, self._mudo)

    def _doble_clic_nombre(self, event):
        from PySide6.QtWidgets import QInputDialog
        nuevo, ok = QInputDialog.getText(self, "Renombrar fuente", "Nombre:", text=self.nombre)
        if ok and nuevo.strip() and nuevo.strip() != self.nombre:
            self.senial_renombrar.emit(self.nombre, nuevo.strip())


# ── Panel de detalle (parametros de la fuente seleccionada) ─────────────────

_COLORES_BANDAS = ["#e53935", "#f9a825", "#2e7d32"]   # bajo, medio, alto
_NOMBRES_BANDAS = ["Bajo", "Medio", "Alto"]
_FREQ_DEFECTOS = {"f1": 80, "f2": 3000, "f3": 10000}
_KIND_EQ = "basic_eq_filter"


class PanelDetalle(QWidget):
    """Panel derecho: parametros (audio, ecualizador, filtros) de la fuente activa."""

    def __init__(self, configuracion, parent=None):
        super().__init__(parent)
        self._config = configuracion
        self._cliente = None
        self._señales = None
        self._fuente = ""
        self._kind = ""
        self._eq_nombre: str | None = None
        self._eq_values = {"f1": 80, "f2": 3000, "f3": 10000, "g1": 0.0, "g2": 0.0, "g3": 0.0}
        self._timer_eq = QTimer(self)
        self._timer_eq.setSingleShot(True)
        self._timer_eq.setInterval(150)
        self._timer_eq.timeout.connect(self._aplicar_eq)

        self.setMinimumWidth(300)
        self._construir_ui()
        self._mostrar_placeholder()

    def _construir_ui(self):
        self.setStyleSheet("QWidget#panelDetalle{background:#242424;border-radius:8px;}")
        self.setObjectName("panelDetalle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._lbl_placeholder = QLabel("Selecciona una fuente\npara ver sus parámetros")
        self._lbl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_placeholder.setStyleSheet("color:#777;background:transparent;font-size:13px;")
        layout.addWidget(self._lbl_placeholder, 1)

        self._contenido = QWidget()
        self._contenido.setStyleSheet("background:transparent;")
        col = QVBoxLayout(self._contenido)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        # Cabecera
        cab = QHBoxLayout()
        self._lbl_nombre = QLabel("")
        self._lbl_nombre.setStyleSheet("color:#eee;font-weight:bold;font-size:14px;background:transparent;")
        self._lbl_kind = QLabel("")
        self._lbl_kind.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        cab.addWidget(self._lbl_nombre)
        cab.addStretch()
        cab.addWidget(self._lbl_kind)
        col.addLayout(cab)

        # ── Grupo AUDIO ──
        grupo_audio = QGroupBox("AUDIO")
        grupo_audio.setStyleSheet(
            "QGroupBox{color:#aaa;font-weight:bold;font-size:11px;border:1px solid #444;border-radius:6px;"
            "margin-top:10px;} QGroupBox::title{subcontrol-origin:margin;left:8px;top:2px;}"
            "QLabel{background:transparent;}"
        )
        ga = QHBoxLayout(grupo_audio)
        self._vu_detalle = VUMeter()
        self._vu_detalle.setMinimumHeight(90)
        ga.addWidget(self._vu_detalle)

        self._fader_detalle = QSlider(Qt.Orientation.Vertical)
        self._fader_detalle.setRange(-600, 120)
        self._fader_detalle.setValue(0)
        self._fader_detalle.setFixedWidth(30)
        self._fader_detalle.setStyleSheet(
            "QSlider::groove:vertical{background:#444;width:6px;border-radius:3px;}"
            "QSlider::handle:vertical{background:#4a9eff;width:18px;height:18px;"
            "margin:-6px;border-radius:9px;}"
            "QSlider::sub-page:vertical{background:#4a9eff;border-radius:3px;}"
        )
        self._fader_detalle.valueChanged.connect(self._fader_detalle_cambio)
        ga.addWidget(self._fader_detalle)

        col_aux = QVBoxLayout()
        self._lbl_db_detalle = QLabel("0.0 dB")
        self._lbl_db_detalle.setStyleSheet("color:#aaa;font-size:12px;font-weight:bold;background:transparent;")
        self._lbl_db_detalle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_aux.addWidget(self._lbl_db_detalle)

        self._btn_mute_detalle = QPushButton("MUTE")
        self._btn_mute_detalle.setCheckable(True)
        self._btn_mute_detalle.clicked.connect(self._mute_detalle_cambio)
        self._btn_mute_detalle.setStyleSheet(
            "QPushButton{background:#444;color:#aaa;border:none;border-radius:4px;padding:6px;font-weight:bold;}"
            "QPushButton:checked{background:#e53935;color:#fff;}"
        )
        col_aux.addWidget(self._btn_mute_detalle)

        self._combo_monitor_detalle = QComboBox()
        self._combo_monitor_detalle.addItems(["Sin monitoreo", "Solo yo", "Yo + stream"])
        self._combo_monitor_detalle.currentIndexChanged.connect(self._monitor_detalle_cambio)
        self._combo_monitor_detalle.setStyleSheet(
            "QComboBox{background:#1e1e1e;color:#ccc;border:1px solid #555;border-radius:3px;padding:3px;font-size:11px;}"
            "QComboBox::drop-down{border:none;width:16px;}"
            "QComboBox QAbstractItemView{background:#1e1e1e;color:#ccc;}"
        )
        col_aux.addWidget(self._combo_monitor_detalle)
        col_aux.addStretch()
        ga.addLayout(col_aux, 1)
        col.addWidget(grupo_audio)

        # ── Grupo EQ ──
        grupo_eq = QGroupBox("ECUALIZADOR (3 BANDAS)")
        grupo_eq.setStyleSheet(
            "QGroupBox{color:#aaa;font-weight:bold;font-size:11px;border:1px solid #444;border-radius:6px;"
            "margin-top:10px;} QGroupBox::title{subcontrol-origin:margin;left:8px;top:2px;}"
            "QLabel{background:transparent;}"
        )
        geq = QVBoxLayout(grupo_eq)
        row_eq = QHBoxLayout()
        self._chk_eq = QCheckBox("Activar EQ")
        self._chk_eq.toggled.connect(self._eq_togleado)
        self._chk_eq.setStyleSheet("color:#ccc;background:transparent;")
        row_eq.addWidget(self._chk_eq)
        row_eq.addStretch()
        self._btn_eq_quitar = QPushButton("Quitar EQ")
        self._btn_eq_quitar.clicked.connect(self._eq_quitar)
        self._btn_eq_quitar.setEnabled(False)
        self._btn_eq_quitar.setStyleSheet(
            "QPushButton{background:#333;color:#aaa;border:none;border-radius:3px;padding:3px 8px;}"
            "QPushButton:hover{background:#444;color:#ddd;}"
        )
        row_eq.addWidget(self._btn_eq_quitar)
        geq.addLayout(row_eq)

        self._sliders_eq: dict[str, QSlider] = {}
        self._lbl_db_banda: dict[str, QLabel] = {}
        for i, banda in enumerate(_NOMBRES_BANDAS):
            fila = QHBoxLayout()
            lbl = QLabel(banda)
            lbl.setStyleSheet(f"color:{_COLORES_BANDAS[i]};font-weight:bold;font-size:11px;background:transparent;")
            lbl.setFixedWidth(46)
            fila.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-40, 40)   # dB * 2
            slider.setValue(0)
            slider.valueChanged.connect(self._eq_slider_cambio)
            slider.setStyleSheet(
                "QSlider::groove:horizontal{background:#333;height:4px;border-radius:2px;}"
                "QSlider::handle:horizontal{background:#f9a825;width:12px;height:14px;"
                "margin:-5px;border-radius:7px;}"
                "QSlider::sub-page:horizontal{background:#555;border-radius:2px;}"
                "QSlider::add-page:horizontal{background:#333;border-radius:2px;}"
            )
            fila.addWidget(slider, 1)
            lblv = QLabel("0.0 dB")
            lblv.setFixedWidth(52)
            lblv.setStyleSheet("color:#ccc;font-size:10px;background:transparent;")
            fila.addWidget(lblv)
            geq.addLayout(fila)
            self._sliders_eq[f"g{i + 1}"] = slider
            self._lbl_db_banda[f"g{i + 1}"] = lblv

        # Frecuencias de corte
        row_freq = QHBoxLayout()
        self._spin_freq: dict[str, QSpinBox] = {}
        etiquetas = [("Bajo", "f1", 20, 2000), ("Medio", "f2", 200, 12000), ("Alto", "f3", 1000, 20000)]
        for txt, key, lo, hi in etiquetas:
            sub = QVBoxLayout()
            lblf = QLabel(txt)
            lblf.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lblf.setStyleSheet("color:#888;font-size:10px;background:transparent;")
            sub.addWidget(lblf)
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSuffix(" Hz")
            spin.setValue(_FREQ_DEFECTOS[key])
            spin.setFixedHeight(24)
            spin.setStyleSheet(
                "QSpinBox{background:#1e1e1e;color:#ccc;border:1px solid #555;border-radius:3px;padding:2px;}"
            )
            spin.valueChanged.connect(lambda _v, k=key: self._eq_freq_cambio(k))
            sub.addWidget(spin)
            row_freq.addLayout(sub)
            self._spin_freq[key] = spin
        geq.addLayout(row_freq)
        col.addWidget(grupo_eq)

        # ── Filtros (widget reutilizable) ──
        self._widget_filtros = WidgetFiltros(None)
        col.addWidget(self._widget_filtros, 1)

        self._contenido.hide()
        layout.addWidget(self._contenido, 1)

    # -- enlace / estado ------------------------------------------------------

    def enlazar(self, cliente, señales):
        self._cliente = cliente
        self._señales = señales
        self._widget_filtros.asignar_cliente(cliente)
        señales.volumen_cambiado.connect(self._on_volumen_evento)
        señales.mudo_cambiado.connect(self._on_mudo_evento)
        señales.fuente_creada.connect(lambda _n: self._refrescar())
        señales.fuente_eliminada.connect(lambda _n: self._refrescar())
        señales.fuente_renombrada.connect(self._on_fuente_renombrada)

    def _refrescar(self):
        if self._fuente:
            self._recargar_fuente()

    def _on_fuente_renombrada(self, viejo: str, nuevo: str):
        if self._fuente == viejo:
            self._fuente = nuevo
            self._lbl_nombre.setText(nuevo)
            self._recargar_fuente()

    def _on_volumen_evento(self, nombre: str, _vol_mul: float, vol_db: float):
        if nombre == self._fuente:
            self._fader_detalle.blockSignals(True)
            self._fader_detalle.setValue(int(vol_db * 10))
            self._fader_detalle.blockSignals(False)
            self._lbl_db_detalle.setText(f"{vol_db:.1f} dB")

    def _on_mudo_evento(self, nombre: str, muted: bool):
        if nombre == self._fuente:
            self._btn_mute_detalle.blockSignals(True)
            self._btn_mute_detalle.setChecked(muted)
            self._btn_mute_detalle.blockSignals(False)

    # -- mostrar fuente -------------------------------------------------------

    def _mostrar_placeholder(self):
        self._lbl_placeholder.show()
        self._contenido.hide()

    def mostrar_fuente(self, nombre: str, kind: str = ""):
        self._fuente = nombre
        self._kind = kind
        self._lbl_placeholder.hide()
        self._contenido.show()
        self._lbl_nombre.setText(nombre)
        self._lbl_kind.setText(kind)
        self._widget_filtros.mostrar_fuente(nombre)
        self._recargar_audio()
        self._recargar_eq()

    def _recargar_fuente(self):
        if self._fuente:
            self._widget_filtros.mostrar_fuente(self._fuente)
            self._recargar_audio()
            self._recargar_eq()

    def _recargar_audio(self):
        if not self._cliente or not self._fuente:
            return
        try:
            vol = self._cliente.volumen_fuente(self._fuente)
            db = vol["input_volume_db"]
            self._fader_detalle.blockSignals(True)
            self._fader_detalle.setValue(int(db * 10))
            self._fader_detalle.blockSignals(False)
            self._lbl_db_detalle.setText(f"{db:.1f} dB")
            mudo = self._cliente.mudo_fuente(self._fuente)
            self._btn_mute_detalle.blockSignals(True)
            self._btn_mute_detalle.setChecked(mudo)
            self._btn_mute_detalle.blockSignals(False)
            monitor = self._cliente.monitor_fuente(self._fuente)
            mapa = {"OBS_MONITORING_TYPE_NONE": 0, "OBS_MONITORING_TYPE_MONITOR_ONLY": 1, "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT": 2}
            self._combo_monitor_detalle.blockSignals(True)
            self._combo_monitor_detalle.setCurrentIndex(mapa.get(monitor, 0))
            self._combo_monitor_detalle.blockSignals(False)
        except Exception:
            pass

    def actualizar_nivel(self, nivel_db: float, peak_db: float = 0.0):
        self._vu_detalle.set_nivel(nivel_db)
        self._vu_detalle.set_peak(peak_db)

    # -- audios de detalle ----------------------------------------------------

    def _fader_detalle_cambio(self, valor: int):
        db = valor / 10.0
        self._lbl_db_detalle.setText(f"{db:.1f} dB")
        if self._cliente and self._fuente:
            try:
                self._cliente.volumen_fuente_set(self._fuente, vol_db=db)
            except Exception:
                pass

    def _mute_detalle_cambio(self):
        if self._cliente and self._fuente:
            try:
                self._cliente.mudo_fuente_set(self._fuente, self._btn_mute_detalle.isChecked())
            except Exception:
                pass

    def _monitor_detalle_cambio(self, idx: int):
        tipos = ["OBS_MONITORING_TYPE_NONE", "OBS_MONITORING_TYPE_MONITOR_ONLY", "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"]
        if self._cliente and self._fuente:
            try:
                self._cliente.monitor_fuente_set(self._fuente, tipos[idx])
            except Exception:
                pass

    # -- ecualizador ----------------------------------------------------------

    def _buscar_eq(self) -> dict | None:
        try:
            for f in self._cliente.filtros_fuente(self._fuente):
                if f.get("filterKind") == _KIND_EQ:
                    self._eq_nombre = f.get("filterName")
                    return f
        except Exception:
            pass
        self._eq_nombre = None
        return None

    def _recargar_eq(self):
        for k, s in self._sliders_eq.items():
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        for k, l in self._lbl_db_banda.items():
            l.setText("0.0 dB")
        self._chk_eq.blockSignals(True)
        self._chk_eq.setChecked(False)
        self._chk_eq.blockSignals(False)
        self._btn_eq_quitar.setEnabled(False)
        eq = self._buscar_eq() if self._cliente else None
        if not eq:
            return
        settings = eq.get("filterSettings", {})
        self._eq_values = {
            "f1": settings.get("f1", 80),
            "f2": settings.get("f2", 3000),
            "f3": settings.get("f3", 10000),
            "g1": settings.get("g1", 0.0),
            "g2": settings.get("g2", 0.0),
            "g3": settings.get("g3", 0.0),
        }
        self._chk_eq.blockSignals(True)
        self._chk_eq.setChecked(bool(eq.get("filterEnabled", True)))
        self._chk_eq.blockSignals(False)
        self._btn_eq_quitar.setEnabled(True)
        for i in range(3):
            k = f"g{i + 1}"
            self._sliders_eq[k].blockSignals(True)
            self._sliders_eq[k].setValue(int(self._eq_values[k] * 2))
            self._sliders_eq[k].blockSignals(False)
            self._lbl_db_banda[k].setText(f"{self._eq_values[k]:.1f} dB")
        self._spin_freq["f1"].blockSignals(True)
        self._spin_freq["f1"].setValue(int(self._eq_values["f1"]))
        self._spin_freq["f1"].blockSignals(False)
        self._spin_freq["f2"].blockSignals(True)
        self._spin_freq["f2"].setValue(int(self._eq_values["f2"]))
        self._spin_freq["f2"].blockSignals(False)
        self._spin_freq["f3"].blockSignals(True)
        self._spin_freq["f3"].setValue(int(self._eq_values["f3"]))
        self._spin_freq["f3"].blockSignals(False)

    def _eq_slider_cambio(self, _valor: int):
        for i in range(3):
            k = f"g{i + 1}"
            self._eq_values[k] = self._sliders_eq[k].value() / 2.0
            self._lbl_db_banda[k].setText(f"{self._eq_values[k]:+.1f} dB")
        self._programar_aplicar_eq()

    def _eq_freq_cambio(self, key: str):
        self._eq_values[key] = self._spin_freq[key].value()
        self._programar_aplicar_eq()

    def _programar_aplicar_eq(self):
        if not self._cliente or not self._fuente:
            return
        self._timer_eq.start()

    def _aplicar_eq(self):
        if not self._cliente or not self._fuente:
            return
        ajustes = {
            "f1": self._eq_values["f1"],
            "f2": self._eq_values["f2"],
            "f3": self._eq_values["f3"],
            "g1": self._eq_values["g1"],
            "g2": self._eq_values["g2"],
            "g3": self._eq_values["g3"],
        }
        try:
            if self._eq_nombre:
                nombre_filtro = self._eq_nombre
            else:
                nombre_filtro = "EQ"
            self._cliente.ajustes_filtro_set(self._fuente, nombre_filtro, ajustes)
            self._btn_eq_quitar.setEnabled(True)
        except Exception:
            # El filtro no existe: crearlo
            try:
                self._cliente.configurar_filtro(self._fuente, "EQ", _KIND_EQ, ajustes)
                self._eq_nombre = "EQ"
                self._btn_eq_quitar.setEnabled(True)
            except Exception:
                pass

    def _eq_togleado(self, activo: bool):
        if not self._cliente or not self._fuente or not self._eq_nombre:
            return
        try:
            self._cliente.habilitar_filtro(self._fuente, self._eq_nombre, activo)
        except Exception:
            pass

    def _eq_quitar(self):
        if not self._cliente or not self._fuente or not self._eq_nombre:
            return
        try:
            self._cliente.eliminar_filtro(self._fuente, self._eq_nombre)
        except Exception:
            pass
        self._eq_nombre = None
        self._recargar_eq()


# ── Panel Mixer ──────────────────────────────────────────────────────────────

class PanelMixer(QWidget):
    """Panel con vista compacta / detalle de las fuentes de audio."""

    def __init__(self, configuracion, parent=None):
        super().__init__(parent)
        self._config = configuracion
        self._cliente = None
        self._señales = None
        self._tarjetas: dict[str, TarjetaFuente] = {}
        self._filas: dict[str, FilaCompacta] = {}
        self._fuentes_obs: list[str] = []
        self._kinds: dict[str, str] = {}
        self._niveles: dict[str, float] = {}
        self._fuente_seleccionada = ""
        self._vista = self._config.interfaz.get("vista_mixer", "detalle")

        self._construir_ui()
        self._aplicar_vista()

        # Timer para repintar VU meters (los datos llegan por evento OBS)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._repintar_vu)
        self._timer.start()

    def _construir_ui(self):
        self._vistas_btn = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Barra de titulo + selector de vista
        barra = QHBoxLayout()
        self._titulo = QLabel("MEZCLADOR")
        self._titulo.setStyleSheet("color:#aaa;font-size:12px;font-weight:bold;letter-spacing:1px;")
        barra.addWidget(self._titulo)
        barra.addStretch()

        self._grupo_vistas = QButtonGroup(self)
        self._grupo_vistas.setExclusive(True)
        for key, label in _ICONO_VIEWS.items():
            btn = QPushButton(label.capitalize())
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:#333;color:#aaa;border:none;border-radius:4px;padding:0 10px;font-size:11px;}"
                "QPushButton:checked{background:#4a9eff;color:#fff;}"
                "QPushButton:hover{background:#444;color:#ddd;}"
            )
            self._grupo_vistas.addButton(btn)
            self._vistas_btn[key] = btn
            barra.addWidget(btn)
        self._grupo_vistas.buttonClicked.connect(self._vista_cambio)
        layout.addLayout(barra)

        # Area compacta
        self._area_compacta = QScrollArea()
        self._area_compacta.setWidgetResizable(True)
        self._area_compacta.setStyleSheet("QScrollArea{background:#1e1e1e;border:none;}")
        self._contenedor_compacta = QWidget()
        self._contenedor_compacta.setStyleSheet("background:#1e1e1e;")
        self._lista_compacta = QVBoxLayout()
        self._lista_compacta.setContentsMargins(4, 4, 4, 4)
        self._lista_compacta.setSpacing(2)
        self._lista_compacta.addStretch()
        self._contenedor_compacta.setLayout(self._lista_compacta)
        self._area_compacta.setWidget(self._contenedor_compacta)
        layout.addWidget(self._area_compacta, 1)

        # Area detalle: tarjetas a la izquierda, panel de parametros a la derecha
        self._area_tarjetas = QScrollArea()
        self._area_tarjetas.setWidgetResizable(True)
        self._area_tarjetas.setStyleSheet("QScrollArea{background:#1e1e1e;border:none;}")
        self._contenedor_tarjetas = QWidget()
        self._contenedor_tarjetas.setStyleSheet("background:#1e1e1e;")
        self._lista_tarjetas = QVBoxLayout()
        self._lista_tarjetas.setContentsMargins(4, 4, 4, 4)
        self._lista_tarjetas.setSpacing(6)
        self._lista_tarjetas.addStretch()
        self._contenedor_tarjetas.setLayout(self._lista_tarjetas)
        self._area_tarjetas.setWidget(self._contenedor_tarjetas)

        self._detalle = PanelDetalle(self._config)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._area_tarjetas)
        self._splitter.addWidget(self._detalle)
        self._splitter.setSizes([280, 340])
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        layout.addWidget(self._splitter, 1)

    # -- enlace al cliente ----------------------------------------------------

    def enlazar(self, cliente, señales):
        self._cliente = cliente
        self._señales = señales
        señales.escena_programa_cambiada.connect(self._on_escena_cambiada)
        señales.mudo_cambiado.connect(self._on_mudo_evento)
        señales.volumen_cambiado.connect(self._on_volumen_evento)
        señales.vu_meters.connect(self._on_vu)
        señales.fuente_renombrada.connect(self._on_fuente_renombrada)
        señales.fuente_creada.connect(lambda _n: self.cargar_fuentes())
        señales.fuente_eliminada.connect(lambda _n: self.cargar_fuentes())
        self._detalle.enlazar(cliente, señales)

    def cargar_fuentes(self):
        if not self._cliente or not self._cliente.conectado:
            return
        try:
            fuentes = self._cliente.obtener_fuentes_con_audio()
            self._fuentes_obs = [f["inputName"] for f in fuentes]
            self._kinds = {f["inputName"]: f.get("inputKind", "") for f in fuentes}
            self._construir_tarjetas()
            self._construir_filas()
            self._aplicar_vista()
        except Exception as e:
            print(f"Error cargando fuentes: {e}")

    # -- construir widgets ----------------------------------------------------

    def _construir_tarjetas(self):
        for i in reversed(range(self._lista_tarjetas.count())):
            w = self._lista_tarjetas.itemAt(i).widget()
            if w and isinstance(w, TarjetaFuente):
                self._lista_tarjetas.removeWidget(w)
                w.deleteLater()
        self._tarjetas.clear()

        for nombre in sorted(self._fuentes_obs):
            tarjeta = TarjetaFuente(nombre)
            tarjeta.senial_volumen_cambiado.connect(self._on_fader_cambiado)
            tarjeta.senial_mudo_cambiado.connect(self._on_mudo_cambiado)
            tarjeta.senial_monitor_cambiado.connect(self._on_monitor_cambiado)
            tarjeta.senial_seleccion.connect(self._on_seleccion)
            tarjeta.senial_renombrar.connect(self._on_renombrar)
            self._lista_tarjetas.insertWidget(self._lista_tarjetas.count() - 1, tarjeta)
            self._tarjetas[nombre] = tarjeta
            self._cargar_estado_tarjeta(tarjeta)
        self._actualizar_estado_escena()

    def _construir_filas(self):
        for i in reversed(range(self._lista_compacta.count())):
            w = self._lista_compacta.itemAt(i).widget()
            if w and isinstance(w, FilaCompacta):
                self._lista_compacta.removeWidget(w)
                w.deleteLater()
        self._filas.clear()

        for nombre in sorted(self._fuentes_obs):
            fila = FilaCompacta(nombre)
            fila.senial_volumen_cambiado.connect(self._on_fader_cambiado)
            fila.senial_mudo_cambiado.connect(self._on_mudo_cambiado)
            fila.senial_seleccion.connect(self._on_seleccion_compacta)
            fila.senial_renombrar.connect(self._on_renombrar)
            self._lista_compacta.insertWidget(self._lista_compacta.count() - 1, fila)
            self._filas[nombre] = fila
            self._cargar_estado_tarjeta(fila)
        self._actualizar_estado_escena()

    def _cargar_estado_tarjeta(self, widget):
        nombre = widget.nombre
        try:
            vol = self._cliente.volumen_fuente(nombre)
            widget.actualizar_volumen(vol["input_volume_db"])
            widget.actualizar_mudo(self._cliente.mudo_fuente(nombre))
            if hasattr(widget, "actualizar_monitor"):
                widget.actualizar_monitor(self._cliente.monitor_fuente(nombre))
        except Exception:
            pass

    def _actualizar_estado_escena(self):
        if not self._cliente or not self._cliente.conectado:
            return
        try:
            escena = self._cliente.escena_actual()
            items = self._cliente.lista_items_escena(escena)
            nombres_items = {it.get("sourceName") for it in items}
            for nombre, widget in {**self._tarjetas, **self._filas}.items():
                widget.actualizar_estado_escena(nombre in nombres_items)
        except Exception:
            pass

    # -- vistas ---------------------------------------------------------------

    def _vista_cambio(self, btn):
        for key, b in self._vistas_btn.items():
            if b is btn:
                self._vista = key
                break
        self._config.interfaz["vista_mixer"] = self._vista
        self._aplicar_vista()

    def _aplicar_vista(self):
        compacta = self._vista == "compacta"
        self._area_compacta.setVisible(compacta)
        self._splitter.setVisible(not compacta)
        for key, b in self._vistas_btn.items():
            b.blockSignals(True)
            b.setChecked(key == self._vista)
            b.blockSignals(False)
        if not compacta:
            # Restaurar seleccion si aplica
            if self._fuente_seleccionada in self._fuentes_obs:
                self._on_seleccion(self._fuente_seleccionada)

    def _on_seleccion_compacta(self, nombre: str):
        # Al cliquear una fila compacta pasamos a detalle con esa fuente
        self._vista = "detalle"
        self._config.interfaz["vista_mixer"] = self._vista
        self._aplicar_vista()
        self._on_seleccion(nombre)

    def _on_seleccion(self, nombre: str):
        self._fuente_seleccionada = nombre
        for n, tarjeta in self._tarjetas.items():
            tarjeta.marcar_seleccionada(n == nombre)
        for n, fila in self._filas.items():
            fila.setProperty("seleccionada", n == nombre)
        self._detalle.mostrar_fuente(nombre, self._kinds.get(nombre, ""))

    # -- callbacks de senales -------------------------------------------------

    def _on_escena_cambiada(self, _escena: str):
        self._actualizar_estado_escena()

    def _on_fuente_renombrada(self, _viejo: str, nuevo: str):
        self.cargar_fuentes()
        if self._fuente_seleccionada == _viejo:
            self._fuente_seleccionada = nuevo

    def _on_mudo_evento(self, nombre: str, muted: bool):
        if nombre in self._tarjetas:
            self._tarjetas[nombre].actualizar_mudo(muted)
        if nombre in self._filas:
            self._filas[nombre].actualizar_mudo(muted)

    def _on_volumen_evento(self, nombre: str, _vol_mul: float, vol_db: float):
        if nombre in self._tarjetas:
            self._tarjetas[nombre].actualizar_volumen(vol_db)
        if nombre in self._filas:
            self._filas[nombre].actualizar_volumen(vol_db)

    def _on_vu(self, datos):
        if not hasattr(datos, "inputs"):
            return
        for entrada in datos.inputs:
            nombre = entrada.get("inputName") or getattr(entrada, "input_name", None)
            niveles = entrada.get("inputLevelsMul") or getattr(entrada, "input_levels_mul", None)
            if not nombre or not niveles:
                continue
            max_peak = -100
            for _canal, picos in niveles.items():
                if picos:
                    max_peak = max(max_peak, max(picos))
            if max_peak > 0.0001:
                db = 20 * math.log10(max_peak)
            else:
                db = _DB_MIN
            self._niveles[nombre] = db

    def _repintar_vu(self):
        for nombre, db in self._niveles.items():
            if self._vista == "compacta":
                if nombre in self._filas:
                    self._filas[nombre].actualizar_nivel(db, db)
            else:
                if nombre in self._tarjetas:
                    self._tarjetas[nombre].actualizar_nivel(db, db)
                if nombre == self._fuente_seleccionada:
                    self._detalle.actualizar_nivel(db, db)

    # -- interaccion del usuario ----------------------------------------------

    def _on_fader_cambiado(self, nombre: str, vol_db: float):
        if self._cliente and self._cliente.conectado:
            try:
                self._cliente.volumen_fuente_set(nombre, vol_db=vol_db)
            except Exception:
                pass

    def _on_mudo_cambiado(self, nombre: str, muted: bool):
        if self._cliente and self._cliente.conectado:
            try:
                self._cliente.mudo_fuente_set(nombre, muted)
            except Exception:
                pass

    def _on_monitor_cambiado(self, nombre: str, tipo: str):
        if self._cliente and self._cliente.conectado:
            try:
                self._cliente.monitor_fuente_set(nombre, tipo)
            except Exception:
                pass

    def _on_renombrar(self, viejo: str, nuevo: str):
        if self._cliente and self._cliente.conectado:
            try:
                self._cliente.cambiar_nombre_fuente(viejo, nuevo)
            except Exception:
                pass