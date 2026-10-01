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

        # Configuración de Disuasión Activa / Flash Inteligente (Luz Blanca Óptica)
        spotlight_cfg = cam_config.get("spotlight", {})
        self.spotlight_enabled: bool = bool(spotlight_cfg.get("enabled", False))
        self.spotlight_night_only: bool = bool(spotlight_cfg.get("night_only", True))
        raw_targets = spotlight_cfg.get("trigger_classes", ["person"])
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        self.spotlight_targets: list = list(raw_targets)
        self.spotlight_cooldown: float = float(spotlight_cfg.get("cooldown_sec", 8.0))
        self.spotlight_active: bool = False
        self.spotlight_off_time: float = 0.0
        self.is_night_vision: bool = False
        self.saturation_mean: float = 50.0

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

    def _evaluate_spotlight(self, detections: list):
        """
        Evalúa si las detecciones actuales deben disparar o mantener la luz blanca / flash óptico.
        Respeta la condición de visión nocturna y el filtro de objetivos (personas, vehículos, etc.).
        Garantizado: Cero alarmas de sonido ni sirenas.
        """
        now = time.time()
        if not self.spotlight_enabled or not self.ptz_manager.has_hardware_ptz():
            if self.spotlight_active:
                self.ptz_manager.set_white_light(False)
                self.spotlight_active = False
            return

        # 1. Comprobar condición de noche
        night_condition_met = (not self.spotlight_night_only) or self.is_night_vision

        # 2. Comprobar si hay algún objetivo que coincida con el filtro
        matching_target = False
        if night_condition_met and detections:
            vehicle_classes = {"car", "truck", "bus", "motorcycle"}
            for d in detections:
                cls_name = d.get("class_name", "")
                if "all" in self.spotlight_targets:
                    matching_target = True
                    break
                if cls_name in self.spotlight_targets:
                    matching_target = True
                    break
                if "vehicles" in self.spotlight_targets and cls_name in vehicle_classes:
                    matching_target = True
                    break

        # 3. Disparar encendido o prolongar cooldown
        if matching_target:
            self.spotlight_off_time = now + self.spotlight_cooldown
            if not self.spotlight_active:
                success = self.ptz_manager.set_white_light(True)
                if success:
                    self.spotlight_active = True
                    logger.info(f"[{self.name}] Disuasión Óptica ACTIVADA por detección.")
        else:
            # No hay objetivos válidos presentes
            if self.spotlight_active and now >= self.spotlight_off_time:
                self.ptz_manager.set_white_light(False)
                self.spotlight_active = False
                logger.info(f"[{self.name}] Disuasión Óptica APAGADA tras cooldown de {self.spotlight_cooldown}s.")

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

        # Detectar visión nocturna en RAM
        self._check_night_vision(frame)

        # Evaluar disuasión óptica / flash
        self._evaluate_spotlight(detections)

        # Actualizar lazo PTZ independiente
        self.ptz_manager.update(primary_target)

        # Aplicar PTZ digital si está configurado para esta cámara
        processed_frame = frame
        if self.ptz_manager.use_digital_ptz:
            target_center = primary_target["center"] if primary_target else None
            processed_frame = self.ptz_manager.digital_controller.apply(frame, target_center)

        # Anotar frame con HUD específico de esta cámara
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

        # Mantener seguimiento PTZ activo con el objetivo prioritario actual
        self.ptz_manager.update(self.last_primary_target)

        # Evaluar disuasión óptica / flash también en frames saltados
        self._evaluate_spotlight(self.last_detections)

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
        """Dibuja la información de la cámara y las detecciones."""
        canvas = detector.draw_hud(
            frame,
            detections=detections,
            primary_target=primary_target,
            ptz_status=self.ptz_manager.status,
            fps=self.fps
        )

        # Añadir cintillo con el nombre del canal, estado Noche y Flash
        h, w = canvas.shape[:2]
        badges = [f"[{self.name}]"]
        if self.is_night_vision:
            badges.append("[IR NOCHE]")
        if self.spotlight_active:
            badges.append("[FLASH ON]")
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
            "ptz": self.ptz_manager.get_state_dict(),
            "spotlight": {
                "enabled": self.spotlight_enabled,
                "active": self.spotlight_active,
                "is_night": self.is_night_vision,
                "night_only": self.spotlight_night_only,
                "trigger_classes": self.spotlight_targets,
                "cooldown_sec": self.spotlight_cooldown,
                "supported": self.ptz_manager.is_white_light_supported()
            },
            "detections_count": len(dets),
            "detections": dets,
            "primary_target_id": primary_id
        }

    def stop(self):
        """Detiene la cámara y el PTZ."""
        logger.info(f"Deteniendo nodo de cámara '{self.name}'...")
        self.ptz_manager.stop()
        self.stream.stop()
