"""
Detector y Rastreador de Objetos en Tiempo Real basado en YOLO (Ultralytics).
Procesa frames en memoria RAM / GPU sin realizar escrituras a disco.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import cv2
import numpy as np

logger = logging.getLogger("Detector")


class ObjectDetector:
    """
    Motor de detección y seguimiento de objetos mediante IA.
    Filtra clases de interés (personas, perros, autos, etc.) y calcula
    las posiciones relativas para el lazo de control PTZ.
    """

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        target_classes: Optional[List[str]] = None,
        confidence: float = 0.40,
        device: str = "auto"
    ):
        """
        :param model_name: Nombre o ruta del modelo YOLO (ej. yolov8n.pt).
        :param target_classes: Lista de nombres de clases a detectar (ej. ['person', 'dog', 'car']).
        :param confidence: Umbral mínimo de confianza.
        :param device: "auto", "mps", "cuda", o "cpu".
        """
        self.model_name = model_name
        self.confidence = confidence
        self.target_classes = [c.lower() for c in (target_classes or ["person", "dog", "car"]) if c]
        self.device = self._resolve_device(device)

        logger.info(f"Cargando modelo YOLO '{self.model_name}' en dispositivo '{self.device}'...")
        
        # Import diferido de ultralytics para no penalizar si sólo se usa como módulo
        from ultralytics import YOLO
        self.model = YOLO(self.model_name)
        
        # Obtener mapa de nombres de clase del modelo (COCO)
        self.class_names = self.model.names  # dict: {0: 'person', 16: 'dog', 2: 'car', ...}
        self.target_class_ids = [
            cid for cid, name in self.class_names.items() if name.lower() in self.target_classes
        ]
        logger.info(f"Clases monitoreadas: {self.target_classes} (IDs COCO: {self.target_class_ids})")

        # Paleta de colores para visualización
        self.colors = {
            "person": (0, 255, 0),      # Verde
            "dog": (0, 165, 255),       # Naranja
            "cat": (255, 105, 180),     # Rosa / Magenta
            "car": (255, 100, 0),       # Azul
            "default": (255, 255, 0)    # Cyan
        }

        # Registro de posiciones anteriores para cálculo de velocidad/movimiento
        self.previous_positions = {}

    def update_target_classes(self, classes: List[str]):
        """Actualiza dinámicamente las clases monitoreadas por YOLO."""
        self.target_classes = [c.lower() for c in classes if c]
        self.target_class_ids = [
            cid for cid, name in self.class_names.items() if name.lower() in self.target_classes
        ]
        logger.info(f"Clases monitoreadas actualizadas: {self.target_classes} (IDs COCO: {self.target_class_ids})")

    def _resolve_device(self, device_pref: str) -> str:
        """Determina el dispositivo óptimo de cálculo disponible."""
        if device_pref != "auto":
            return device_pref

        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            # Soporte acelerado Metal en Mac Apple Silicon
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass

        return "cpu"

    def detect_and_track(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Ejecuta la inferencia y el seguimiento sobre el frame en memoria.
        Retorna una lista de detecciones enriquecidas con ID de tracking y coordenadas.
        """
        h, w = frame.shape[:2]
        detections = []

        # Inferencia con ByteTrack integrado en Ultralytics (sin sobrecarga de flujo óptico GMC)
        results = self.model.track(
            source=frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=self.confidence,
            classes=self.target_class_ids,
            device=self.device,
            verbose=False
        )

        if not results or len(results) == 0:
            return detections

        res = results[0]
        boxes = res.boxes

        if boxes is None or len(boxes) == 0:
            return detections

        for box in boxes:
            cls_id = int(box.cls[0].item())
            class_name = self.class_names.get(cls_id, str(cls_id))
            conf = float(box.conf[0].item())

            # ID de seguimiento persistente (si está disponible)
            track_id = int(box.id[0].item()) if box.id is not None else -1

            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            bw = x2 - x1
            bh = y2 - y1
            cx = x1 + bw // 2
            cy = y1 + bh // 2

            # Desviación normalizada del centro (-1.0 a 1.0)
            norm_error_x = (cx - (w / 2.0)) / (w / 2.0)
            norm_error_y = (cy - (h / 2.0)) / (h / 2.0)

            # Calcular velocidad instantánea / movimiento del objetivo
            prev_pos = self.previous_positions.get(track_id)
            if prev_pos is not None:
                dx = cx - prev_pos[0]
                dy = cy - prev_pos[1]
                speed_px = (dx**2 + dy**2)**0.5
            else:
                speed_px = 0.0
            if track_id != -1:
                self.previous_positions[track_id] = (cx, cy)

            detections.append({
                "track_id": track_id,
                "class_name": class_name,
                "confidence": conf,
                "bbox": (x1, y1, x2, y2),
                "center": (cx, cy),
                "width": bw,
                "height": bh,
                "area": bw * bh,
                "speed_px": speed_px,
                "norm_error": (norm_error_x, norm_error_y)
            })

        # Limpiar registros de posiciones viejas
        if len(self.previous_positions) > 60:
            self.previous_positions = {k: v for k, v in list(self.previous_positions.items())[-30:]}

        return detections

    def select_primary_target(
        self,
        detections: List[Dict[str, Any]],
        current_target_id: Optional[int] = None,
        priority_classes: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Selecciona el objetivo más adecuado para el seguimiento PTZ:
        - Si se define 'priority_classes' (ej. ['person', 'car', 'dog']), solo evalúa
          esas clases y prioriza estrictamente las que aparecen primero en la lista.
        - Si aparece un objetivo de mayor prioridad (ej. persona vs auto), conmuta
          inmediatamente al objetivo prioritario.
        - Dentro del mismo rango de prioridad:
          * Si el objetivo actual se mueve, mantiene el lock.
          * Si el objetivo actual se detiene y otro se mueve, cambia al que se mueve.
          * Prioriza objetivos activos en movimiento y más cercanos al centro.
        """
        if not detections:
            return None

        # 1. Filtrar por clases permitidas si se especifican
        candidates = detections
        if priority_classes:
            allowed = [c.lower() for c in priority_classes if c]
            if allowed:
                candidates = [d for d in detections if d.get("class_name", "").lower() in allowed]
                if not candidates:
                    return None

                # Obtener el mejor rango de prioridad presente (0 = máxima prioridad)
                def get_rank(d):
                    cls = d.get("class_name", "").lower()
                    return allowed.index(cls) if cls in allowed else 9999

                min_rank = min(get_rank(d) for d in candidates)
                # Filtrar solo los candidatos que pertenecen al nivel de mayor prioridad presente
                candidates = [d for d in candidates if get_rank(d) == min_rank]

        # 2. Separar objetivos con movimiento claro (speed >= 5px y no marcados estáticos)
        moving_targets = [
            d for d in candidates
            if d.get("speed_px", 0.0) >= 5.0 and not d.get("is_static", False)
        ]

        # 3. Comprobar el estado del objetivo actual que se estaba siguiendo
        current_det = None
        if current_target_id is not None and current_target_id != -1:
            current_det = next((d for d in candidates if d["track_id"] == current_target_id), None)

        # REGLA CLAVE: Si el objetivo actual está quieto o estático, pero hay otro objetivo en movimiento:
        # ¡Priorizar inmediatamente el objetivo en movimiento dentro de este nivel de prioridad!
        if moving_targets:
            if current_det is None or current_det.get("is_static", False) or current_det.get("speed_px", 0.0) < 4.0:
                # Seleccionar el objetivo en movimiento más cercano al centro
                return min(moving_targets, key=lambda d: d["norm_error"][0]**2 + d["norm_error"][1]**2)

        # Si el objetivo actual sigue presente en el rango prioritario y no está estático, mantener lock
        if current_det is not None and not current_det.get("is_static", False):
            return current_det

        # En caso general: priorizar objetivos en movimiento si los hay; sino, el más centrado
        pool = moving_targets if moving_targets else [d for d in candidates if not d.get("is_static", False)]
        if not pool:
            pool = candidates

        return min(pool, key=lambda d: d["norm_error"][0]**2 + d["norm_error"][1]**2)

    def draw_hud(
        self,
        frame: np.ndarray,
        detections: List[Dict[str, Any]],
        primary_target: Optional[Dict[str, Any]] = None,
        ptz_status: str = "IDLE",
        fps: float = 0.0
    ) -> np.ndarray:
        """
        Dibuja la información en pantalla (Bounding boxes, etiquetas, vector de seguimiento y estado).
        Opera directamente sobre una copia o sobre el frame en memoria.
        """
        h, w = frame.shape[:2]
        canvas = frame.copy()

        # Dibujar retícula central
        cx_frame, cy_frame = w // 2, h // 2
        cv2.drawMarker(
            canvas, (cx_frame, cy_frame), (200, 200, 200),
            markerType=cv2.MARKER_CROSS, markerSize=20, thickness=1
        )

        # Dibujar cada detección
        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            cls = det["class_name"]
            conf = det["confidence"]
            tid = det["track_id"]
            color = self.colors.get(cls, self.colors["default"])

            is_primary = (primary_target is not None and det["track_id"] == primary_target["track_id"])
            thickness = 3 if is_primary else 2

            # Cuadro delimitador
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)

            # Etiqueta
            label = f"#{tid} {cls} {conf:.2f}"
            if is_primary:
                if primary_target.get("is_static", False):
                    label += " [ESTATICO]"
                else:
                    label += " [TRACKING]"

            # Fondo para el texto
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(canvas, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(
                canvas, label, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA
            )

        # Dibujar vector de guía hacia el objetivo primario solo si está en movimiento activo
        if primary_target is not None and not primary_target.get("is_static", False):
            tx, ty = primary_target["center"]
            cv2.line(canvas, (cx_frame, cy_frame), (tx, ty), (0, 0, 255), 2)
            cv2.circle(canvas, (tx, ty), 6, (0, 0, 255), -1)

        # Barra superior de estado (HUD)
        header_text = f"FPS: {fps:.1f} | Detecciones: {len(detections)} | Estado PTZ: {ptz_status}"
        cv2.rectangle(canvas, (0, 0), (w, 18), (30, 30, 30), -1)
        cv2.putText(
            canvas, header_text, (6, 13),
            cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 255), 1, cv2.LINE_AA
        )

        return canvas
