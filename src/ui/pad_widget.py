"""Widget de un pad individual del soundboard."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal, QTimer
from PySide6.QtGui import QBrush, QColor, QDrag, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

log = logging.getLogger(__name__)

TAMANO_DEFAULT = 128
_TAMANO_ICONOS = {"chico": 96, "mediano": 128, "grande": 160}


def _path_redondeado(rect, radio=12):
    p = QPainterPath()
    p.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), radio, radio)
    return p


class PadWidget(QWidget):
    """Un boton visual del soundboard."""

    senial_play = Signal(int)     # indice del pad
    senial_stop = Signal(int)
    senial_menu = Signal(int, QPoint)  # indice, posicion global
    senial_swap = Signal(int, int)     # indice origen, indice destino

    MIME_TYPE = "application/x-pad-index"

    def __init__(self, indice: int, pad_config: dict, tamano: int = TAMANO_DEFAULT, parent=None):
        super().__init__(parent)
        self.indice = indice
        self.config = pad_config
        self.tamano = tamano
        self.setFixedSize(tamano, tamano)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)

        # Imagen
        self._pixmap: QPixmap | None = None
        self._cargar_imagen(pad_config.get("imagen", ""))

        # Estado visual
        self._hover = False
        self._presionado = False
        self._sonando = False

        # Timer para animacion de "sonando"
        self._anim_timer = QTimer(self)
        self._anim_tick = 0
        self._anim_timer.timeout.connect(self._anim_tick_handler)
        self._anim_timer.setInterval(100)

    def _cargar_imagen(self, ruta: str):
        if not ruta or not Path(ruta).exists():
            self._pixmap = None
            return
        try:
            pm = QPixmap(ruta)
            if not pm.isNull():
                self._pixmap = pm.scaled(
                    self.tamano - 16, self.tamano - 16,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        except Exception:
            self._pixmap = None

    def actualizar_config(self, pad_config: dict, tamano: int):
        self.config = pad_config
        self.tamano = tamano
        self.setFixedSize(tamano, tamano)
        self._cargar_imagen(pad_config.get("imagen", ""))
        self.update()

    def iniciar_sonido(self):
        self._sonando = True
        self._anim_tick = 0
        self._anim_timer.start()
        self.update()

    def detener_sonido(self):
        self._sonando = False
        self._anim_timer.stop()
        self.update()

    def _anim_tick_handler(self):
        self._anim_tick += 1
        self.update()

    # -- eventos pintura -----------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)

        # Fondo del pad
        color_fondo = QColor("#3c3c3c")
        if self._hover:
            color_fondo = QColor("#4a4a4a")
        if self._presionado:
            color_fondo = QColor("#555555")
        if self._sonando:
            # Glow pulsante
            alpha = 120 + int(80 * abs(self._anim_tick % 10 - 5) / 5)
            color_fondo = QColor(74, 158, 255, alpha)
            p.setPen(Qt.PenStyle.NoPen)
            glow_color = QColor(74, 158, 255, 80)
            p.setBrush(QBrush(glow_color))
            p.drawEllipse(rect.adjusted(-6, -6, 6, 6))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color_fondo))
        p.drawPath(_path_redondeado(rect))

        # Borde del pad
        borde_color = QColor(self.config.get("color", "#43a047"))
        if self._sonando:
            borde_color = QColor("#4a9eff")
        pen = p.pen()
        pen.setColor(borde_color)
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_path_redondeado(rect))

        # Icono / imagen
        if self._pixmap:
            x = (self.width() - self._pixmap.width()) // 2
            y = (self.height() - self._pixmap.height()) // 2 - 6
            p.drawPixmap(x, y, self._pixmap)
        else:
            # Icono por defecto: nota musical simple
            p.setPen(QColor("#888"))
            font = QFont("Segoe UI", max(self.tamano // 4, 16))
            p.setFont(font)
            p.drawText(rect.adjusted(0, -4, 0, 0), Qt.AlignmentFlag.AlignCenter, "\u266b")

        # Nombre
        nombre = self.config.get("nombre", "?")
        p.setPen(QColor("#ddd"))
        font = QFont("Segoe UI", max(self.tamano // 12, 9))
        p.setFont(font)
        name_rect = rect.adjusted(4, self.tamano - 28, -4, 4)
        p.drawText(name_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, nombre)

        p.end()

    # -- eventos de entrada --------------------------------------------------

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self._presionado = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._presionado = True
            self.update()
            self._press_pos = event.position().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            self.senial_menu.emit(self.indice, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._presionado = False
            self.update()
            # Si fue click (no drag), emitir play
            pos = event.position().toPoint()
            if not hasattr(self, "_press_pos") or (pos - self._press_pos).manhattanLength() < 10:
                self.senial_play.emit(self.indice)

    # -- drag & drop ---------------------------------------------------------

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            dist = (event.position().toPoint() - self._press_pos).manhattanLength()
            if dist > 10:
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData(self.MIME_TYPE, str(self.indice).encode())
                drag.setMimeData(mime)
                drag.setPixmap(self.grab())
                drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat(self.MIME_TYPE):
            origen = int(mime.data(self.MIME_TYPE).data().decode())
            destino = self.indice
            if origen != destino:
                self.senial_swap.emit(origen, destino)
            event.acceptProposedAction()