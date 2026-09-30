"""
Controlador de PTZ Digital / Smart Framing en Tiempo Real.
Permite emular paneo, inclinación y zoom digital centrado en el objetivo
cuando se utiliza una cámara fija (como la Dahua IPC-HFW1320S-W).
"""

from typing import Optional, Tuple
import cv2
import numpy as np


class DigitalPTZController:
    """
    Realiza encuadre digital suave (Smart Framing) centrado en el objetivo detectado.
    Todo el procesamiento ocurre en memoria sin escribir a disco.
    """

    def __init__(self, smoothing: float = 0.15, zoom_factor: float = 1.6):
        """
        :param smoothing: Factor de interpolación suave (0.05 a 0.3) para evitar sacudidas.
        :param zoom_factor: Nivel de aumento digital sobre el objetivo.
        """
        self.smoothing = smoothing
        self.zoom_factor = zoom_factor

        self.current_center_x: Optional[float] = None
        self.current_center_y: Optional[float] = None

    def apply(self, frame: np.ndarray, target_center: Optional[Tuple[int, int]] = None) -> np.ndarray:
        """
        Recorta y escala el frame alrededor del objetivo con suavizado temporal.
        """
        h, w = frame.shape[:2]

        if target_center is None:
            # Si no hay objetivo, vuelve suavemente al centro completo de la imagen
            target_cx, target_cy = w / 2.0, h / 2.0
        else:
            target_cx, target_cy = float(target_center[0]), float(target_center[1])

        # Inicialización del centro suavizado
        if self.current_center_x is None:
            self.current_center_x = target_cx
            self.current_center_y = target_cy
        else:
            # Filtro exponencial de suavizado (EMA)
            self.current_center_x += (target_cx - self.current_center_x) * self.smoothing
            self.current_center_y += (target_cy - self.current_center_y) * self.smoothing

        # Dimensiones de la ventana de recorte
        crop_w = int(w / self.zoom_factor)
        crop_h = int(h / self.zoom_factor)

        # Coordenadas límites asegurando no salir del frame
        x1 = int(self.current_center_x - crop_w // 2)
        y1 = int(self.current_center_y - crop_h // 2)

        x1 = max(0, min(x1, w - crop_w))
        y1 = max(0, min(y1, h - crop_h))
        x2 = x1 + crop_w
        y2 = y1 + crop_h

        # Recorte y redimensionado de vuelta al tamaño original
        cropped = frame[y1:y2, x1:x2]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
