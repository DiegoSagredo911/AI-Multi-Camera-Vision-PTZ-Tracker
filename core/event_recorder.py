"""
Grabador Automático de Clips Cortos de Evidencia (MP4) en Alertas.
Mantiene un búfer circular en memoria RAM de los últimos segundos (pre-alarma).
Al dispararse una alerta (ej. disuasión flash activada), graba en segundo plano
el período previo y continúa capturando el período posterior (post-alarma) para
guardar el clip MP4 en disco junto a su metadata JSON.
"""

import os
import time
import json
import logging
import threading
from collections import deque
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import cv2
import numpy as np

logger = logging.getLogger("EventRecorder")


class EventRecorder:
    """
    Gestiona la grabación de clips de evidencia sin penalizar el rendimiento:
    - Búfer circular en RAM para segundos previos al evento (pre-alarma).
    - Hilo secundario para ensamblar y codificar el MP4 en segundo plano.
    - Metadatos del evento guardados en formato JSON.
    """

    def __init__(
        self,
        cam_id: str,
        cam_name: str,
        recording_config: Optional[Dict[str, Any]] = None,
        output_dir: str = "events"
    ):
        self.cam_id = cam_id
        self.cam_name = cam_name
        cfg = recording_config or {}

        self.enabled: bool = bool(cfg.get("enabled", False))
        self.pre_alarm_sec: float = max(1.0, float(cfg.get("pre_alarm_sec", 3.0)))
        self.post_alarm_sec: float = max(1.0, float(cfg.get("post_alarm_sec", 5.0)))
        self.output_dir = Path(output_dir)

        # Búfer circular en memoria RAM: tuplas de (timestamp, frame_bgr)
        # Estimando ~25 FPS máximo, pre_alarm_sec * 30 da capacidad suficiente
        max_buffer_frames = int((self.pre_alarm_sec + 2.0) * 30)
        self._ram_buffer: deque = deque(maxlen=max_buffer_frames)
        self._buffer_lock = threading.Lock()

        # Control de grabación activa en curso
        self._is_recording: bool = False
        self._record_end_time: float = 0.0
        self._active_record_frames: List[np.ndarray] = []
        self._active_event_meta: Dict[str, Any] = {}
        self._record_lock = threading.Lock()

    def update_config(self, cfg: Dict[str, Any]):
        """Actualiza parámetros de grabación en caliente."""
        if "enabled" in cfg:
            self.enabled = bool(cfg["enabled"])
        if "pre_alarm_sec" in cfg:
            self.pre_alarm_sec = max(1.0, float(cfg["pre_alarm_sec"]))
            with self._buffer_lock:
                max_buffer_frames = int((self.pre_alarm_sec + 2.0) * 30)
                # Reinstanciar deque con nuevo tamaño preservando datos
                self._ram_buffer = deque(self._ram_buffer, maxlen=max_buffer_frames)
        if "post_alarm_sec" in cfg:
            self.post_alarm_sec = max(1.0, float(cfg["post_alarm_sec"]))

    def push_frame(self, frame: np.ndarray):
        """
        Almacena el cuadro actual en el búfer circular RAM.
        Si hay una grabación en progreso, añade el cuadro a la secuencia post-alarma.
        """
        if not self.enabled:
            return

        now = time.time()
        # Copia superficial o copia directa para evitar que mutaciones de HUD afecten el clip
        frame_copy = frame.copy()

        with self._buffer_lock:
            self._ram_buffer.append((now, frame_copy))

        # Acumular cuadros en la grabación activa
        with self._record_lock:
            if self._is_recording:
                self._active_record_frames.append(frame_copy)
                if now >= self._record_end_time:
                    # Finalizó el período post-alarma: exportar MP4 en segundo plano
                    self._is_recording = False
                    frames_to_write = list(self._active_record_frames)
                    meta_to_write = dict(self._active_event_meta)
                    self._active_record_frames.clear()

                    writer_thread = threading.Thread(
                        target=self._write_mp4_async,
                        args=(frames_to_write, meta_to_write),
                        daemon=True
                    )
                    writer_thread.start()

    def trigger_event(self, trigger_reason: str, target_info: Optional[Dict[str, Any]] = None) -> bool:
        """
        Dispara la captura de un clip de evento cuando ocurre una alarma (ej. disuasión flash).
        Retorna True si la grabación se inició con éxito.
        """
        if not self.enabled:
            return False

        now = time.time()
        with self._record_lock:
            if self._is_recording:
                # Ya hay una grabación en curso; extender el tiempo de finalización
                self._record_end_time = max(self._record_end_time, now + self.post_alarm_sec)
                return True

            self._is_recording = True
            self._record_end_time = now + self.post_alarm_sec
            self._active_record_frames.clear()

            # Extraer cuadros de pre-alarma del búfer circular RAM
            cutoff_time = now - self.pre_alarm_sec
            with self._buffer_lock:
                for ts, f in self._ram_buffer:
                    if ts >= cutoff_time:
                        self._active_record_frames.append(f)

            self._active_event_meta = {
                "cam_id": self.cam_id,
                "cam_name": self.cam_name,
                "timestamp": now,
                "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "reason": trigger_reason,
                "target": {
                    "track_id": target_info.get("track_id", -1) if target_info else -1,
                    "class_name": target_info.get("class_name", "desconocido") if target_info else "desconocido",
                    "confidence": target_info.get("confidence", 0.0) if target_info else 0.0
                } if target_info else {}
            }

        logger.info(f"[{self.cam_name}] Grabación de clip de evidencia iniciada ({trigger_reason}).")
        return True

    def _write_mp4_async(self, frames: List[np.ndarray], metadata: Dict[str, Any]):
        """Codifica y escribe los cuadros en MP4 y genera el archivo JSON de metadata."""
        if not frames:
            logger.warning(f"[{self.cam_name}] Clip de evidencia descartado: 0 frames.")
            return

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            timestamp_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(metadata["timestamp"]))
            file_base = f"event_{self.cam_id}_{timestamp_str}_{int(time.time() * 1000) % 1000:03d}"
            video_filename = f"{file_base}.mp4"
            json_filename = f"{file_base}.json"

            video_path = self.output_dir / video_filename
            json_path = self.output_dir / json_filename

            h, w = frames[0].shape[:2]
            fps = 15.0  # FPS estándar para clips compactos

            # Probar códec H264 o mp4v
            fourcc = cv2.VideoWriter_fourcc(*"avc1")
            writer = cv2.VideoWriter(str(video_path), fourcc, fps, (w, h))
            if not writer.isOpened():
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(video_path), fourcc, fps, (w, h))

            for f in frames:
                # Asegurar dimensiones homogéneas
                if f.shape[0] != h or f.shape[1] != w:
                    f = cv2.resize(f, (w, h))
                writer.write(f)
            writer.release()

            # Guardar metadata
            metadata["filename"] = video_filename
            metadata["duration_sec"] = round(len(frames) / fps, 2)
            metadata["frame_count"] = len(frames)
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump(metadata, jf, indent=2, ensure_ascii=False)

            logger.info(f"[{self.cam_name}] Clip de evidencia guardado: {video_path} ({metadata['duration_sec']}s)")

        except Exception as e:
            logger.error(f"[{self.cam_name}] Error guardando clip de evidencia: {e}")

    def get_config_dict(self) -> Dict[str, Any]:
        """Retorna la configuración en formato diccionario."""
        return {
            "enabled": self.enabled,
            "pre_alarm_sec": self.pre_alarm_sec,
            "post_alarm_sec": self.post_alarm_sec
        }
