"""
Módulo de Luz de Advertencia (Flash) Inteligente (Luz Blanca Óptica).
100% Óptico y Silencioso: Cero sirenas ni alarmas sonoras.
Controla el retardo de seguimiento (dwell time), duración del flash y disparo único por objetivo.
"""

import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("SpotlightManager")


class SpotlightManager:
    """
    Controlador modular de luz de advertencia (flash) óptica para cámaras PTZ / Fijas.
    - Soporta retardo de seguimiento continuo antes de disparar el flash (ej. 3 segundos).
    - Disparo único por objetivo ('one_shot_per_target'): la cámara continúa siguiendo
      al objetivo pero sin repetir el flash continuamente para la misma persona.
    - Clases configurables (personas, vehículos, animales, o todas).
    - Conmutación automática según visión nocturna IR (opcional).
    """

    def __init__(self, cam_name: str, config: Optional[Dict[str, Any]] = None):
        self.cam_name = cam_name
        self.target_history: Dict[int, Dict[str, Any]] = {}

        # Estado del foco
        self.is_flashing: bool = False
        self.flash_end_time: float = 0.0
        self.current_flashing_target_id: Optional[int] = None

        # Estado de telemetría de cuenta regresiva (dwell)
        self.is_dwelling: bool = False
        self.dwelling_target_id: Optional[int] = None
        self.dwell_remaining: float = 0.0

        # Historial de objetivos flasheados para métricas
        self.flashed_count: int = 0

        # Cargar configuración inicial
        self.update_config(config or {})

    def update_config(self, config: Dict[str, Any]):
        """Actualiza los parámetros de disuasión óptica en caliente."""
        self.enabled: bool = bool(config.get("enabled", False))
        self.night_only: bool = bool(config.get("night_only", True))

        raw_targets = config.get("trigger_classes", ["person"])
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        self.trigger_classes: List[str] = [str(t).lower() for t in raw_targets]

        # Retardo de seguimiento continuo antes del flash (por defecto: 3.0s)
        self.trigger_delay_sec: float = max(0.0, float(config.get("trigger_delay_sec", 3.0)))

        # Duración del flash encendido (por defecto: 3.0s)
        self.flash_duration_sec: float = max(0.5, float(config.get("flash_duration_sec", 3.0)))

        # Disparo único por persona / objetivo (por defecto: True)
        self.one_shot_per_target: bool = bool(config.get("one_shot_per_target", True))

        # Tiempo de cooldown o permanencia general (por compatibilidad)
        self.cooldown_sec: float = max(1.0, float(config.get("cooldown_sec", 8.0)))

    def _matches_trigger_classes(self, class_name: str) -> bool:
        """Determina si la clase detectada coincide con los objetivos a disuadir."""
        if not self.trigger_classes:
            return False
        cls = class_name.lower()
        if "all" in self.trigger_classes:
            return True
        if cls in self.trigger_classes:
            return True
        vehicle_classes = {"car", "truck", "bus", "motorcycle"}
        if "vehicles" in self.trigger_classes and cls in vehicle_classes:
            return True
        animal_classes = {"dog", "cat", "horse", "sheep", "cow"}
        if "animals" in self.trigger_classes and cls in animal_classes:
            return True
        return False

    def evaluate(
        self,
        detections: List[Dict[str, Any]],
        primary_target: Optional[Dict[str, Any]],
        is_night_vision: bool,
        ptz_manager: Any
    ) -> bool:
        """
        Evalúa en cada ciclo de video si se debe iniciar, mantener o apagar el flash óptico.
        """
        now = time.time()

        # 1. Comprobar si la función está habilitada y la cámara soporta control de luz
        if not self.enabled or not ptz_manager.has_hardware_ptz():
            if self.is_flashing:
                self._turn_off(ptz_manager)
            self.is_dwelling = False
            self.dwell_remaining = 0.0
            return False

        # 2. Comprobar condición de noche si está configurado 'night_only'
        if self.night_only and not is_night_vision:
            if self.is_flashing:
                self._turn_off(ptz_manager)
            self.is_dwelling = False
            self.dwell_remaining = 0.0
            return False

        # 3. Limpiar historial de objetivos que ya no están en la escena (>30s sin detección)
        stale_threshold = now - 30.0
        stale_ids = [
            tid for tid, data in self.target_history.items()
            if data["last_seen"] < stale_threshold
        ]
        for tid in stale_ids:
            del self.target_history[tid]

        # 4. Apagar el flash si su duración programada expiró
        if self.is_flashing and now >= self.flash_end_time:
            self._turn_off(ptz_manager)
            logger.info(
                f"[{self.cam_name}] Luz de advertencia (flash) finalizada ({self.flash_duration_sec}s). "
                f"Continuando seguimiento silencioso."
            )

        # 5. Identificar el objetivo relevante para luz de advertencia
        # Prioriza el objetivo que la cámara está siguiendo activamente (primary_target)
        target_to_eval = None
        if primary_target and self._matches_trigger_classes(primary_target.get("class_name", "")):
            target_to_eval = primary_target
        elif detections:
            # Si no hay primary_target seleccionado, buscar en las detecciones actuales
            for d in detections:
                if self._matches_trigger_classes(d.get("class_name", "")):
                    target_to_eval = d
                    break

        if not target_to_eval:
            self.is_dwelling = False
            self.dwell_remaining = 0.0
            return self.is_flashing

        track_id = target_to_eval.get("track_id", -1)
        cls_name = target_to_eval.get("class_name", "desconocido")

        # 6. Actualizar registro temporal del objetivo
        if track_id not in self.target_history:
            self.target_history[track_id] = {
                "first_seen": now,
                "last_seen": now,
                "flashed": False,
                "flashed_time": 0.0,
                "class_name": cls_name
            }
        else:
            self.target_history[track_id]["last_seen"] = now

        target_info = self.target_history[track_id]
        time_tracked = now - target_info["first_seen"]

        # 7. Si el objetivo ya fue flasheado y la regla es 'un solo flash por objetivo':
        if target_info["flashed"] and self.one_shot_per_target:
            self.is_dwelling = False
            self.dwell_remaining = 0.0
            # No volver a encender el flash para este objetivo mientras se le sigue
            return self.is_flashing

        # 8. Evaluar retardo de seguimiento (dwell time)
        if time_tracked >= self.trigger_delay_sec:
            # Cumplió el tiempo de seguimiento continuo -> DISPARAR FLASH
            self.is_dwelling = False
            self.dwell_remaining = 0.0

            if not self.is_flashing:
                success = ptz_manager.set_white_light(True)
                if success:
                    self.is_flashing = True
                    self.flash_end_time = now + self.flash_duration_sec
                    self.current_flashing_target_id = track_id
                    target_info["flashed"] = True
                    target_info["flashed_time"] = now
                    self.flashed_count += 1
                    logger.info(
                        f"[{self.cam_name}] ¡Luz de Advertencia (Flash) ACTIVADA! Foco encendido tras seguir a {cls_name} "
                        f"(ID: {track_id}) durante {time_tracked:.1f}s. "
                        f"(Duración: {self.flash_duration_sec}s, Disparo único: {self.one_shot_per_target})"
                    )
        else:
            # Todavía está en cuenta regresiva de seguimiento
            self.is_dwelling = True
            self.dwelling_target_id = track_id
            self.dwell_remaining = max(0.0, self.trigger_delay_sec - time_tracked)

        return self.is_flashing

    def _turn_off(self, ptz_manager: Any):
        """Apaga el foco óptico."""
        try:
            ptz_manager.set_white_light(False)
        except Exception as e:
            logger.debug(f"Error al apagar luz blanca: {e}")
        self.is_flashing = False
        self.current_flashing_target_id = None

    def trigger_test(self, duration_sec: float, ptz_manager: Any) -> bool:
        """Inicia una prueba manual de flash por un tiempo determinado."""
        if not ptz_manager.has_hardware_ptz():
            return False
        success = ptz_manager.set_white_light(True)
        if success:
            self.is_flashing = True
            self.flash_end_time = time.time() + duration_sec
        return success

    def get_telemetry(self) -> Dict[str, Any]:
        """Retorna telemetría completa para la interfaz web."""
        return {
            "enabled": self.enabled,
            "active": self.is_flashing,
            "dwelling": self.is_dwelling,
            "dwell_remaining": round(self.dwell_remaining, 1),
            "dwelling_target_id": self.dwelling_target_id if self.is_dwelling else None,
            "flashed_count": self.flashed_count,
            "night_only": self.night_only,
            "trigger_classes": self.trigger_classes,
            "trigger_delay_sec": self.trigger_delay_sec,
            "flash_duration_sec": self.flash_duration_sec,
            "one_shot_per_target": self.one_shot_per_target,
            "cooldown_sec": self.cooldown_sec
        }
