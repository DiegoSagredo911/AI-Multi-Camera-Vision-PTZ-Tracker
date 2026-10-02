"""
Nodo Individual de Cámara en el Sistema Multi-Cámara.
Encapsula el stream de video, el controlador PTZ y el estado de tracking de una cámara.
Opera 100% en memoria RAM sin almacenar videos ni fotos en disco.
"""

import time
import threading
import logging
from typing import Optional, Dict, Any
import numpy as np
import cv2

from .stream import VideoStream
from .ptz_manager import PTZManager
from .spotlight_manager import SpotlightManager
from .geofence import GeofenceManager
from .event_recorder import EventRecorder

logger = logging.getLogger("CameraNode")


class CameraNode:
    """
    Representa una cámara individual dentro del sistema:
    - Gestiona su propia conexión RTSP/Webcam en segundo plano.
    - Gestiona su propio lazo PTZ (si cuenta con motores o si es fija).
    - Mantiene el frame más reciente anotado con su HUD.
    """

    def __init__(self, cam_config: Dict[str, Any], global_detection_config: Dict[str, Any]):
        self.config = cam_config
        self.cam_id = str(cam_config.get("id", "cam_0"))
        self.name = str(cam_config.get("name", self.cam_id))
        self.global_detection_config = global_detection_config

        # Resolver origen de video
        self.video_source = self._resolve_source(cam_config)
        self.reconnect_sec = int(cam_config.get("reconnect_interval_sec", 5))

        # Stream de video multihilo
        self.stream = VideoStream(self.video_source, reconnect_interval=self.reconnect_sec)

        # Configuración PTZ para este nodo
        ptz_cfg = cam_config.get("ptz", {})
        camera_auth = {
            "camera_ip": cam_config.get("camera_ip", "192.168.1.108"),
            "username": cam_config.get("username", "admin"),
            "password": cam_config.get("password", ""),
            "channel": cam_config.get("channel", 1)
        }
        self.ptz_manager = PTZManager(ptz_cfg, camera_auth)

        # Configuración de Objetos a Seguir con Orden de Prioridad PTZ
        raw_priority = ptz_cfg.get(
            "tracking_priority",
            cam_config.get("tracking_priority", global_detection_config.get("target_classes", ["person", "car", "motorcycle", "dog"]))
        )
        if isinstance(raw_priority, str):
            raw_priority = [raw_priority]
        self.tracking_priority: list = list(raw_priority)

        # Estado interno de tracking
        self.lock = threading.Lock()
        self.current_target_id: Optional[int] = None
        self.last_detections = []
        self.last_primary_target = None
        self.last_annotated_frame: Optional[np.ndarray] = None
        self.last_jpeg_bytes: Optional[bytes] = None
        self.fps: float = 0.0
        self.prev_time: float = time.time()

        # Frame Skipping (Optimización CPU para Mini PC / Servidores)
        # 1 = cada frame, 2 = 1 de cada 2 frames, etc.
        self.frame_skip: int = max(1, int(cam_config.get("frame_skip", global_detection_config.get("frame_skip", 1))))
        self.frame_count: int = 0

        # Controlador Modular de Luz de Advertencia (Flash) Inteligente (Luz Blanca Óptica)
        spotlight_cfg = cam_config.get("spotlight", {})
        self.spotlight = SpotlightManager(self.name, spotlight_cfg)
        self.is_night_vision: bool = False
        self.saturation_mean: float = 50.0

        # Gestor de Geocercas y Polígonos de Detección por Cámara
        geofence_cfg = cam_config.get("geofence", {})
        self.geofence = GeofenceManager(geofence_cfg)

        # Grabador Automático de Clips de Evidencia (RAM + MP4) por Cámara
        rec_cfg = cam_config.get("event_recording", {})
        self.event_recorder = EventRecorder(self.cam_id, self.name, rec_cfg)

    @property
    def spotlight_active(self) -> bool:
        return self.spotlight.is_flashing

    @spotlight_active.setter
    def spotlight_active(self, val: bool):
        self.spotlight.is_flashing = bool(val)

    @property
    def spotlight_enabled(self) -> bool:
        return self.spotlight.enabled

    @spotlight_enabled.setter
    def spotlight_enabled(self, val: bool):
        self.spotlight.enabled = bool(val)

    @property
    def spotlight_targets(self) -> list:
        return self.spotlight.trigger_classes

    @spotlight_targets.setter
    def spotlight_targets(self, val: list):
        self.spotlight.trigger_classes = list(val)

    @property
    def spotlight_night_only(self) -> bool:
        return self.spotlight.night_only

    @spotlight_night_only.setter
    def spotlight_night_only(self, val: bool):
        self.spotlight.night_only = bool(val)

    @property
    def spotlight_cooldown(self) -> float:
        return self.spotlight.cooldown_sec

    @spotlight_cooldown.setter
    def spotlight_cooldown(self, val: float):
        self.spotlight.cooldown_sec = float(val)

    def _resolve_source(self, cfg: Dict[str, Any]):
        """Construye la URL o fuente para este nodo de cámara."""
        source_type = cfg.get("type", "rtsp").lower()

        if source_type == "webcam":
            return int(cfg.get("webcam_index", 0))

        if source_type == "file":
            return str(cfg.get("file_path", "test.mp4"))

        # RTSP personalizado directo
        custom_url = cfg.get("custom_rtsp_url", "").strip()
        if custom_url:
            return custom_url

        # Generador de URL Dahua
        ip = cfg.get("camera_ip", "192.168.1.108")
        port = cfg.get("rtsp_port", 554)
        user = cfg.get("username", "admin")
        password = cfg.get("password", "")
        channel = cfg.get("channel", 1)
        subtype = cfg.get("subtype", 1)

        return f"rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}"

    def start(self) -> "CameraNode":
        """Inicia la captura de video del nodo."""
        logger.info(f"Iniciando nodo de cámara '{self.name}' ({self.cam_id})...")
        self.stream.start()
        return self

    def read_frame(self):
        """Retorna (éxito, frame) desde memoria RAM."""
        return self.stream.read()

    def _check_night_vision(self, frame: np.ndarray) -> bool:
        """
        Detecta en tiempo real (100% en RAM) si la cámara está en visión nocturna infrarroja.
        Cuando una cámara conmuta a IR/noche, la imagen es monocromática (saturación HSV cercana a 0).
        """
        try:
            # Muestrear a baja resolución para computar en < 0.1 ms sin retraso
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_NEAREST)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            sat = float(np.mean(hsv[:, :, 1]))
            self.saturation_mean = sat
            # Saturación < 18.0 indica inequívocamente modo nocturno infrarrojo
            self.is_night_vision = bool(sat < 18.0)
            return self.is_night_vision
        except Exception:
            return self.is_night_vision

    def _evaluate_spotlight(self, detections: list, primary_target: Optional[dict] = None):
        """
        Evalúa disuasión óptica modular: retardo de seguimiento (3s), destello y disparo único por objetivo.
        """
        target = primary_target if primary_target is not None else self.last_primary_target
        return self.spotlight.evaluate(detections, target, self.is_night_vision, self.ptz_manager)

    def update_cycle(self, frame: np.ndarray, detections: list, primary_target: Optional[dict], detector):
        """
        Actualiza el estado de la cámara tras la inferencia completa de IA.
        Aplica PTZ, actualiza FPS y dibuja el HUD de la cámara.
        """
        now = time.time()
        dt = now - self.prev_time
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self.prev_time = now

        self.last_detections = detections
        self.last_primary_target = primary_target
        if primary_target:
            self.current_target_id = primary_target["track_id"]
        else:
            self.current_target_id = None

        # Alimentar búfer circular RAM del grabador de evidencia
        self.event_recorder.push_frame(frame)

        # Detectar visión nocturna en RAM
        self._check_night_vision(frame)

        # Evaluar luz de advertencia (flash) modular
        was_flashing_before = self.spotlight.is_flashing
        self._evaluate_spotlight(detections, primary_target)
        # Si se acaba de disparar la luz de advertencia (flash), disparar clip de evidencia
        if not was_flashing_before and self.spotlight.is_flashing:
            self.event_recorder.trigger_event("ADVERTENCIA_FLASH", primary_target)

        # Actualizar lazo PTZ independiente con frame_shape para auto-zoom dinámico
        self.ptz_manager.update(primary_target, frame_shape=frame.shape[:2])

        # Aplicar PTZ digital si está configurado para esta cámara
        processed_frame = frame
        if self.ptz_manager.use_digital_ptz:
            target_center = primary_target["center"] if primary_target else None
            processed_frame = self.ptz_manager.digital_controller.apply(frame, target_center)

        # Anotar frame con HUD específico de esta cámara y geocerca
        annotated = self._draw_node_hud(
            processed_frame,
            detections,
            primary_target,
            detector
        )

        with self.lock:
            self.last_annotated_frame = annotated
            self.last_jpeg_bytes = None

    def update_skipped_cycle(self, frame: np.ndarray, detector):
        """
        Ciclo optimizado para frames donde se salta la inferencia de IA (Frame Skipping).
        Reutiliza las últimas detecciones en memoria, actualiza el lazo PTZ y renderiza
        el HUD sin gastar ciclos de CPU en el pase hacia adelante de la red neuronal.
        """
        now = time.time()
        dt = now - self.prev_time
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self.prev_time = now

        # Alimentar búfer circular RAM del grabador de evidencia
        self.event_recorder.push_frame(frame)

        # Mantener seguimiento PTZ activo con el objetivo prioritario actual
        self.ptz_manager.update(self.last_primary_target, frame_shape=frame.shape[:2])

        # Evaluar luz de advertencia (flash) también en frames saltados
        was_flashing_before = self.spotlight.is_flashing
        self._evaluate_spotlight(self.last_detections, self.last_primary_target)
        if not was_flashing_before and self.spotlight.is_flashing:
            self.event_recorder.trigger_event("ADVERTENCIA_FLASH", self.last_primary_target)

        # Aplicar PTZ digital si está activo
        processed_frame = frame
        if self.ptz_manager.use_digital_ptz:
            target_center = self.last_primary_target["center"] if self.last_primary_target else None
            processed_frame = self.ptz_manager.digital_controller.apply(frame, target_center)

        annotated = self._draw_node_hud(
            processed_frame,
            self.last_detections,
            self.last_primary_target,
            detector
        )

        with self.lock:
            self.last_annotated_frame = annotated
            self.last_jpeg_bytes = None

    def _draw_node_hud(self, frame: np.ndarray, detections: list, primary_target: Optional[dict], detector) -> np.ndarray:
        """Dibuja la información de la cámara, geocercas y detecciones."""
        # Dibujar polígono de geocerca sobre el frame si está habilitado
        frame_with_poly = self.geofence.draw_polygon(frame)

        canvas = detector.draw_hud(
            frame_with_poly,
            detections=detections,
            primary_target=primary_target,
            ptz_status=self.ptz_manager.status,
            fps=self.fps
        )

        # Añadir cintillo con el nombre del canal, estado Noche, Flash y cuenta regresiva
        h, w = canvas.shape[:2]
        badges = [f"[{self.name}]"]
        if self.is_night_vision:
            badges.append("[IR NOCHE]")
        if self.spotlight.is_flashing:
            badges.append("[FLASH ON]")
        elif self.spotlight.is_dwelling:
            badges.append(f"[FLASH EN {self.spotlight.dwell_remaining:.1f}s]")
        if self.geofence.enabled:
            badges.append(f"[GEO:{self.geofence.mode[:3].upper()}]")
        badge_text = " ".join(badges)

        cv2.putText(
            canvas, badge_text, (10, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA
        )
        return canvas

    def get_display_frame(self) -> np.ndarray:
        """
        Retorna el frame listo para la cuadrícula.
        Si la cámara está desconectada, genera una pantalla de aviso en memoria.
        """
        with self.lock:
            if self.last_annotated_frame is not None and self.stream.is_connected:
                return self.last_annotated_frame.copy()

        # Cuadro de señal no disponible / reconectando
        placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
        title = f"{self.name}: RECONECTANDO..."
        cv2.putText(placeholder, title, (80, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        cv2.putText(placeholder, f"Fuente: {self.stream._mask_credentials(str(self.video_source))}", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        return placeholder

    def get_jpeg_frame(self, quality: int = 75) -> bytes:
        """
        Retorna el frame actual codificado en JPEG 100% en memoria RAM para streaming web.
        """
        with self.lock:
            if self.last_jpeg_bytes is not None:
                return self.last_jpeg_bytes

        frame = self.get_display_frame()
        success, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        )
        if success:
            jpeg_bytes = buffer.tobytes()
            with self.lock:
                self.last_jpeg_bytes = jpeg_bytes
            return jpeg_bytes
        return b""

    def get_telemetry(self) -> Dict[str, Any]:
        """Retorna la telemetría en tiempo real del nodo para el frontend web."""
        with self.lock:
            dets = [
                {
                    "track_id": d["track_id"],
                    "class_name": d["class_name"],
                    "confidence": round(d["confidence"], 2),
                    "speed_px": round(d.get("speed_px", 0.0), 1),
                    "is_static": d.get("is_static", False)
                }
                for d in self.last_detections
            ]
            primary_id = self.current_target_id

        return {
            "cam_id": self.cam_id,
            "name": self.name,
            "is_connected": self.stream.is_connected,
            "fps": round(self.fps, 1),
            "frame_skip": self.frame_skip,
            "tracking_priority": self.tracking_priority,
            "ptz": self.ptz_manager.get_state_dict(),
            "spotlight": {
                **self.spotlight.get_telemetry(),
                "is_night": self.is_night_vision,
                "supported": self.ptz_manager.is_white_light_supported()
            },
            "geofence": self.geofence.get_config_dict(),
            "event_recording": self.event_recorder.get_config_dict(),
            "detections_count": len(dets),
            "detections": dets,
            "primary_target_id": primary_id
        }

    def stop(self):
        """Detiene la cámara y el PTZ."""
        logger.info(f"Deteniendo nodo de cámara '{self.name}'...")
        self.ptz_manager.stop()
        self.stream.stop()
