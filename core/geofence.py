"""
Gestor de Zonas de Interés y Geocercas (Polígonos de Detección).
Permite definir polígonos de inclusión o exclusión por cámara, evaluando
si el punto base o centro de un objetivo cae dentro de la zona.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import cv2
import numpy as np

logger = logging.getLogger("GeofenceManager")


class GeofenceManager:
    """
    Administra la geocerca o polígono de interés para una cámara.
    Coordenadas normalizadas en [0.0, 1.0] para que la zona sea independiente
    de la resolución del sensor o del redimensionamiento del frame.
    """

    def __init__(self, geofence_config: Optional[Dict[str, Any]] = None):
        cfg = geofence_config or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        # Modo: "inclusion" (solo detecta dentro) o "exclusion" (ignora lo que está dentro)
        self.mode: str = str(cfg.get("mode", "inclusion")).lower()
        # Lista de puntos normalizados: [{"x": 0.1, "y": 0.1}, ...]
        self.points: List[Dict[str, float]] = list(cfg.get("points", []))

    def update_config(self, cfg: Dict[str, Any]):
        """Actualiza los parámetros de la geocerca en caliente."""
        if "enabled" in cfg:
            self.enabled = bool(cfg["enabled"])
        if "mode" in cfg:
            self.mode = str(cfg["mode"]).lower()
        if "points" in cfg:
            self.points = list(cfg["points"])

    def is_target_allowed(self, target: Dict[str, Any], frame_width: int, frame_height: int) -> bool:
        """
        Evalúa si un objetivo está permitido según la geocerca.
        Utiliza el punto base del objetivo (cx, y2) para representar la posición en el suelo.
        """
        if not self.enabled or len(self.points) < 3 or frame_width <= 0 or frame_height <= 0:
            return True

        # Convertir puntos normalizados a píxeles
        poly_pts = np.array(
            [[int(p["x"] * frame_width), int(p["y"] * frame_height)] for p in self.points],
            dtype=np.int32
        )

        # Usar punto base (pies / suelo) si existe bbox, o center como fallback
        if "bbox" in target:
            x1, y1, x2, y2 = target["bbox"]
            test_point = (float(x1 + (x2 - x1) / 2.0), float(y2))
        elif "center" in target:
            test_point = (float(target["center"][0]), float(target["center"][1]))
        else:
            return True

        # cv2.pointPolygonTest retorna: > 0 (dentro), == 0 (borde), < 0 (fuera)
        inside = cv2.pointPolygonTest(poly_pts, test_point, False) >= 0

        if self.mode == "inclusion":
            return inside
        elif self.mode == "exclusion":
            return not inside
        return True

    def filter_detections(self, detections: List[Dict[str, Any]], frame_width: int, frame_height: int) -> List[Dict[str, Any]]:
        """Filtra una lista de detecciones descartando las que no cumplen con la geocerca."""
        if not self.enabled or len(self.points) < 3:
            return detections
        return [d for d in detections if self.is_target_allowed(d, frame_width, frame_height)]

    def draw_polygon(self, frame: np.ndarray) -> np.ndarray:
        """Dibuja visualmente la geocerca sobre el frame para feedback en tiempo real."""
        if not self.enabled or len(self.points) < 3:
            return frame

        h, w = frame.shape[:2]
        pts = np.array(
            [[int(p["x"] * w), int(p["y"] * h)] for p in self.points],
            dtype=np.int32
        )

        overlay = frame.copy()
        color = (0, 165, 255) if self.mode == "inclusion" else (0, 0, 255)  # Naranja o Rojo
        cv2.fillPoly(overlay, [pts], color)
        # Mezcla translúcida (alpha = 0.20)
        cv2.addWeighted(overlay, 0.20, frame, 0.80, 0, frame)
        # Borde del polígono
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)

        # Etiqueta de modo
        label_pos = pts[0]
        label_text = f"GEOCERCA: {self.mode.upper()}"
        cv2.putText(
            frame, label_text, (label_pos[0] + 5, max(20, label_pos[1] - 5)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA
        )
        return frame

    def get_config_dict(self) -> Dict[str, Any]:
        """Retorna la configuración en formato diccionario serializable."""
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "points": self.points
        }
