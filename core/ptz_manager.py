"""
Orquestador de Control PTZ, Patrullaje Periódico y Seguimiento Inteligente.
"""

import time
import math
import logging
from typing import Optional, Dict, Any
from .ptz.base import BasePTZController
from .ptz.onvif_controller import OnvifPTZController
from .ptz.dahua_cgi_controller import DahuaCgiPTZController
from .ptz.digital_ptz import DigitalPTZController

logger = logging.getLogger("PTZManager")


class PTZManager:
    """
    Gestiona la máquina de estados PTZ:
    - IDLE: Esperando eventos sin movimiento.
    - TRACKING: Siguiendo activamente al objetivo en el encuadre.
    - PATROLLING: Barrido periódico de búsqueda si no hay actividad en X segundos.
    - FIXED: Modo cámara fija (sin PTZ mecánico).
    """

    def __init__(
        self,
        ptz_config: Dict[str, Any],
        camera_auth: Dict[str, Any],
        custom_controller: Optional[BasePTZController] = None
    ):
        self.config = ptz_config
        self.auth = camera_auth

        self.enabled = ptz_config.get("enabled", "auto")
        self.driver_type = ptz_config.get("driver", "onvif")
        self.patrol_interval = float(ptz_config.get("patrol_interval_sec", 12.0))
        self.sweep_duration = float(ptz_config.get("patrol_sweep_duration_sec", 2.0))
        self.patrol_speed = float(ptz_config.get("patrol_speed", 0.25))
        self.tracking_speed = float(ptz_config.get("tracking_speed", 0.45))
        self.deadzone_x = float(ptz_config.get("deadzone_x", 0.12))
        self.deadzone_y = float(ptz_config.get("deadzone_y", 0.12))
        self.use_digital_ptz = bool(ptz_config.get("digital_ptz", False))

        # Controles Modulares Independientes por Cámara
        self.patrol_enabled = bool(ptz_config.get("patrol_enabled", True))
        self.tracking_enabled = bool(ptz_config.get("tracking_enabled", True))
        self.positioning_mode = str(ptz_config.get("positioning_mode", "native_preset")).lower()  # "native_preset" o "time_based"
        self.home_preset = str(ptz_config.get("home_preset", "1"))

        # Tipo de Patrullaje: "panoramic" (Barrido 355°) o "preset_tour" (Ronda de Presets)
        self.patrol_type = str(ptz_config.get("patrol_type", "panoramic")).lower()
        # Lista de presets con tiempo de permanencia individual: [{"preset": "1", "dwell_time_sec": 8.0}, ...]
        default_tour = [{"preset": "1", "dwell_time_sec": 8.0}, {"preset": "2", "dwell_time_sec": 8.0}]
        self.preset_tour = list(ptz_config.get("preset_tour", default_tour))
        self.tour_current_idx = 0
        self.tour_substate = "MOVING"  # "MOVING" o "DWELLING"
        self.tour_dwell_start_time = 0.0
        self.tour_cmd_sent_time = 0.0

        self.hardware_controller: Optional[BasePTZController] = custom_controller
        self.digital_controller = DigitalPTZController()

        # Configuración de Patrullaje Panorámico 3D (Horizontal + Vertical)
        self.panoramic_patrol = bool(ptz_config.get("panoramic_patrol", True))
        self.sweep_pan_time = float(ptz_config.get("sweep_pan_time_sec", 14.0))
        self.tilt_transition_time = float(ptz_config.get("tilt_transition_time_sec", 3.0))
        self.tilt_speed = float(ptz_config.get("patrol_tilt_speed", 0.35))


        # Calibración Inicial / Homing
        self.initial_homing = bool(ptz_config.get("initial_homing", True))
        self.homing_pan_duration = float(ptz_config.get("homing_pan_duration_sec", 14.0))
        self.homing_tilt_duration = float(ptz_config.get("homing_tilt_duration_sec", 2.5))
        self.is_homing = False
        self.homing_stage = "PAN_RIGHT"  # "NATIVE", "PAN_RIGHT", "TILT_UP", "DONE"
        self.homing_start_time = 0.0

        # Estado del sistema
        self.status = "INITIALIZING"
        self.last_target_time = time.time()
        self.is_patrolling = False
        self.patrol_direction = 1  # 1: Derecha, -1: Izquierda
        self.patrol_tilt_direction = -1  # 1: Arriba (Horizonte), -1: Abajo (Calle)
        self.patrol_substate = "IDLE"  # "IDLE", "PANNING", "TILT_SHIFT"
        self.substate_start_time = 0.0

        # Control de Objetos Estáticos (ej. autos estacionados o personas quietas)
        self.static_target_timeout = float(ptz_config.get("static_target_timeout_sec", 3.0))
        self.movement_threshold = float(ptz_config.get("movement_threshold_norm", 0.08))
        self.target_states: Dict[int, Dict[str, Any]] = {}

        # Memoria de patrullaje para reanudar la dirección exacta tras un objetivo estático
        self.was_patrol_interrupted = False
        self.saved_patrol_direction = 1
        self.saved_patrol_tilt_direction = -1
        self.saved_patrol_substate = "PANNING"

        # Control manual desde la Web y pausa temporal parametrizable
        self.manual_override_pause_sec = float(ptz_config.get("manual_override_pause_sec", 1.0))
        self.manual_override_until = 0.0

        # Control de frecuencia de comandos ONVIF (evita saturar la cámara)
        self.last_cmd_time = 0.0
        self.cmd_interval = 0.12  # Máximo 8 comandos por segundo
        self.last_sent_pan = 0.0
        self.last_sent_tilt = 0.0

        self._setup_controller()

    def _start_homing_sequence(self):
        """Inicia el proceso de posicionamiento en Home (Nativo Preset o Secuencial por Tiempos)."""
        if self.positioning_mode == "native_preset":
            self.is_homing = True
            self.homing_stage = "NATIVE"
            self.homing_start_time = time.time()
            self.status = f"CALIBRACIÓN NATIVA: Yendo a Preset {self.home_preset}..."
            logger.info(f"Iniciando Posicionamiento Nativo: GotoPreset({self.home_preset}) de fábrica...")
            success = self.hardware_controller.goto_preset(self.home_preset)
            if not success:
                logger.warning(f"Fallo al invocar GotoPreset({self.home_preset}). Pasando a calibración por tiempos como respaldo.")
                self.positioning_mode = "time_based"
                self._start_homing_sequence()
            return

        # Modo Temporizado: Giro físico a tope derecho y luego arriba
        self.is_homing = True
        self.homing_stage = "PAN_RIGHT"
        self.homing_start_time = time.time()
        self.status = "CALIBRACIÓN: 1/2 A Tope Derecho..."
        logger.info("Iniciando Calibración Inicial Secuencial: [Paso 1/2] Llevando cámara a tope DERECHO (Pan solo)...")
        self.hardware_controller.continuous_move(pan_velocity=0.45, tilt_velocity=0.0)
        self.last_sent_pan = 0.45
        self.last_sent_tilt = 0.0
        self.last_cmd_time = self.homing_start_time

    def _setup_controller(self):
        """Inicializa el controlador PTZ físico o pasa a modo cámara fija."""
        if self.hardware_controller is not None:
            if self.hardware_controller.is_available():
                if self.initial_homing:
                    self._start_homing_sequence()
                else:
                    self.status = "IDLE"
                logger.info("Controlador PTZ personalizado conectado y habilitado.")
            else:
                self.status = "FIXED_CAMERA"
            return


        if self.enabled is False:
            logger.info("PTZ físico deshabilitado en configuración. Operando en modo cámara fija.")
            self.status = "FIXED_CAMERA"
            return

        ip = self.auth.get("camera_ip", "192.168.1.108")
        user = self.auth.get("username", "admin")
        password = self.auth.get("password", "")
        onvif_port = int(self.config.get("onvif_port", 80))
        channel = int(self.auth.get("channel", 1))

        # Intentar inicializar driver configurado
        if self.driver_type == "onvif":
            controller = OnvifPTZController(ip, port=onvif_port, username=user, password=password)
        else:
            controller = DahuaCgiPTZController(ip, port=onvif_port, username=user, password=password, channel=channel)

        if controller.is_available():
            self.hardware_controller = controller
            if self.initial_homing:
                self._start_homing_sequence()
            else:
                self.status = "IDLE"
            logger.info("Controlador PTZ físico conectado y habilitado.")
        else:
            if self.enabled is True:
                logger.warning("PTZ físico forzado pero la cámara no respondió a comandos PTZ.")
            else:
                logger.info("No se detectó hardware PTZ en la cámara. Modo fijado en: CÁMARA FIJA.")
            self.status = "FIXED_CAMERA"

    def has_hardware_ptz(self) -> bool:
        """Indica si hay una cámara PTZ motorizada conectada."""
        return self.hardware_controller is not None and self.hardware_controller.is_available()

    def update(self, primary_target: Optional[Dict[str, Any]] = None):
        """
        Actualiza el lazo de control PTZ según las detecciones del frame actual.
        """
        now = time.time()

        # Si no hay PTZ físico, sólo actualizamos estado informativo
        if not self.has_hardware_ptz():
            if primary_target is not None:
                self.status = "DIGITAL_TRACKING" if self.use_digital_ptz else "TARGET_LOCKED"
            else:
                self.status = "FIXED_CAMERA"
            return

        # -------------------------------------------------------------
        # CALIBRACIÓN INICIAL (NATIVA POR PRESET O SECUENCIAL POR TIEMPOS)
        # -------------------------------------------------------------
        if self.is_homing:
            elapsed = now - self.homing_start_time

            # 1. Homing Nativo mediante Preset de Cámara
            if self.homing_stage == "NATIVE":
                # Consultar si el motor de la cámara ya se detuvo (IDLE) o pasaron 4 segundos
                st = self.hardware_controller.get_status()
                is_moving = st.get("is_moving", False)
                if (not is_moving and elapsed > 1.5) or elapsed > 4.5:
                    self.is_homing = False
                    self.homing_stage = "DONE"
                    self.status = "IDLE (En Home Nativo)"
                    self.last_target_time = now
                    logger.info(f"--- POSICIONAMIENTO NATIVO COMPLETADO: Cámara en Preset {self.home_preset} ---")
                    return
                else:
                    self.status = f"CALIBRACIÓN NATIVA: Yendo a Preset {self.home_preset} ({elapsed:.1f}s)..."
                    return

            # 2. Homing Temporizado Secuencial (Paso 1: Tope Derecho)
            if self.homing_stage == "PAN_RIGHT":
                if elapsed < self.homing_pan_duration:
                    self.status = f"CALIBRACIÓN: 1/2 Tope DERECHO ({elapsed:.1f}s/{self.homing_pan_duration:.1f}s)"
                    # Keep-alive cada 1.0s para evitar que el firmware Dahua detenga el motor
                    if (now - self.last_cmd_time) >= 1.0:
                        self.hardware_controller.continuous_move(pan_velocity=0.45, tilt_velocity=0.0)
                        self.last_cmd_time = now
                    return
                else:
                    # Finalizar Paso 1 (Giro horizontal a tope derecho)
                    self.hardware_controller.stop()
                    time.sleep(0.15)
                    self.homing_stage = "TILT_UP"
                    self.homing_start_time = now
                    logger.info("Calibración Inicial: [Paso 2/2] Llevando cámara a tope SUPERIOR (Arriba)...")
                    # Iniciar Paso 2 (Inclinación vertical a tope superior)
                    self.hardware_controller.continuous_move(pan_velocity=0.0, tilt_velocity=0.35)
                    self.last_sent_pan = 0.0
                    self.last_sent_tilt = 0.35
                    self.last_cmd_time = now
                    self.status = f"CALIBRACIÓN: 2/2 Tope ARRIBA (0.0s/{self.homing_tilt_duration:.1f}s)"
                    return

            elif self.homing_stage == "TILT_UP":
                if elapsed < self.homing_tilt_duration:
                    self.status = f"CALIBRACIÓN: 2/2 Tope ARRIBA ({elapsed:.1f}s/{self.homing_tilt_duration:.1f}s)"
                    # Keep-alive cada 1.0s
                    if (now - self.last_cmd_time) >= 1.0:
                        self.hardware_controller.continuous_move(pan_velocity=0.0, tilt_velocity=0.35)
                        self.last_cmd_time = now
                    return
                else:
                    # Finalizar Paso 2: Cámara en posición Cero (Tope Derecho + Arriba)
                    self.hardware_controller.stop()
                    self.is_homing = False
                    self.homing_stage = "DONE"
                    self.last_sent_pan = 0.0
                    self.last_sent_tilt = 0.0
                    self.patrol_direction = 1       # Para que al entrar en patrullaje se invierta a -1 (Izquierda a lo largo de los 355°)
                    self.patrol_tilt_direction = 1  # Para que al cambiar de pasada se incline a -1 (Calle)
                    self.patrol_substate = "IDLE"
                    self.last_target_time = 0.0     # Forzar inicio de patrulla panorámica de inmediato si no hay nadie
                    logger.info("--- CALIBRACIÓN INICIAL COMPLETADA: Punto Cero establecido (Tope Derecho + Tope Arriba) ---")
                    self.status = "IDLE (Calibrado)"
                    return


        # -------------------------------------------------------------
        # PAUSA TEMPORAL POR CONTROL MANUAL DESDE LA WEB
        # -------------------------------------------------------------
        if now < self.manual_override_until:
            rem = self.manual_override_until - now
            self.status = f"MANUAL_WEB (Pausa {rem:.1f}s)"
            return

        # -------------------------------------------------------------
        # EVALUACIÓN DE INMOVILIDAD (OBJETOS ESTÁTICOS VS EN MOVIMIENTO)
        # -------------------------------------------------------------
        active_target = primary_target
        if primary_target is not None:
            tid = primary_target.get("track_id", -1)
            curr_pos = primary_target["norm_error"]

            if tid != -1:
                if tid not in self.target_states:
                    self.target_states[tid] = {
                        "first_seen": now,
                        "last_moved": now,
                        "last_pos": curr_pos,
                        "is_static": False
                    }
                else:
                    t_info = self.target_states[tid]
                    prev_pos = t_info["last_pos"]
                    dist = math.hypot(curr_pos[0] - prev_pos[0], curr_pos[1] - prev_pos[1])

                    # Si el objetivo se desplazó por encima del umbral de movimiento
                    if dist > self.movement_threshold:
                        t_info["last_moved"] = now
                        t_info["last_pos"] = curr_pos
                        if t_info["is_static"]:
                            logger.info(f"¡Objetivo #{tid} ({primary_target['class_name']}) volvió a moverse! Reanudando seguimiento.")
                        t_info["is_static"] = False
                    else:
                        time_immobile = now - t_info["last_moved"]
                        # Si permanece quieto por más tiempo que el límite configurado (ej. 3 seg)
                        if time_immobile >= self.static_target_timeout:
                            if not t_info["is_static"]:
                                t_info["is_static"] = True
                                logger.info(
                                    f"Objetivo #{tid} ({primary_target['class_name']}) inmóvil por {int(time_immobile)}s. "
                                    f"Marcando como ESTÁTICO y continuando de inmediato el patrullaje que estaba haciendo..."
                                )
                                # Reanudar patrullaje de inmediato (sin esperar los 12s de inactividad)
                                self.last_target_time = 0.0

                # Si está marcado como estático, se ignora para el seguimiento motorizado
                if self.target_states.get(tid, {}).get("is_static", False):
                    primary_target["is_static"] = True
                    active_target = None
                else:
                    primary_target["is_static"] = False

        # Limpieza periódica de estados viejos
        if len(self.target_states) > 30:
            self.target_states = {k: v for k, v in self.target_states.items() if now - v["last_moved"] < 15.0}

        # -------------------------------------------------------------
        # CASO 1: HAY UN OBJETIVO ACTIVO EN MOVIMIENTO
        # -------------------------------------------------------------
        if active_target is not None:
            self.last_target_time = now

            # Si el seguimiento IA motorizado está desactivado para esta cámara
            if not self.tracking_enabled:
                if self.last_sent_pan != 0.0 or self.last_sent_tilt != 0.0:
                    self.hardware_controller.stop()
                    self.last_sent_pan = 0.0
                    self.last_sent_tilt = 0.0
                self.is_patrolling = False
                self.patrol_substate = "IDLE"
                self.status = f"DETECTADO #{active_target.get('track_id', '?')} ({active_target['class_name']}) [Seguimiento OFF]"
                return

            if self.is_patrolling or self.patrol_substate != "IDLE":
                logger.info(f"¡{active_target['class_name'].upper()} EN MOVIMIENTO! Interrumpiendo patrulla 3D y siguiendo...")
                # Guardar el estado exacto del patrullaje en curso para continuarlo si se detiene
                self.saved_patrol_direction = self.patrol_direction
                self.saved_patrol_tilt_direction = self.patrol_tilt_direction
                self.saved_patrol_substate = self.patrol_substate if self.patrol_substate != "IDLE" else "PANNING"
                self.was_patrol_interrupted = True
                self.is_patrolling = False
                self.patrol_substate = "IDLE"

            self.status = f"TRACKING #{active_target.get('track_id', '?')} ({active_target['class_name']})"

            # Error respecto al centro del encuadre (-1.0 a 1.0)
            norm_err_x, norm_err_y = active_target["norm_error"]

            cmd_pan = 0.0
            cmd_tilt = 0.0

            # Aplicar zona muerta horizontal
            if abs(norm_err_x) > self.deadzone_x:
                cmd_pan = norm_err_x * self.tracking_speed

            # Aplicar zona muerta vertical (invertido: en video Y crece hacia abajo)
            if abs(norm_err_y) > self.deadzone_y:
                cmd_tilt = -norm_err_y * self.tracking_speed

            # Caso A: Objetivo centrado dentro de la zona muerta
            if abs(cmd_pan) < 0.05 and abs(cmd_tilt) < 0.05:
                if self.last_sent_pan != 0.0 or self.last_sent_tilt != 0.0:
                    self.hardware_controller.stop()
                    self.last_sent_pan = 0.0
                    self.last_sent_tilt = 0.0
                return

            # Caso B: Objetivo fuera del centro -> enviar movimiento si pasó el intervalo
            if (now - self.last_cmd_time) >= self.cmd_interval or (self.last_sent_pan == 0.0 and self.last_sent_tilt == 0.0):
                self.hardware_controller.continuous_move(cmd_pan, cmd_tilt)
                self.last_cmd_time = now
                self.last_sent_pan = cmd_pan
                self.last_sent_tilt = cmd_tilt
            return

        # -------------------------------------------------------------
        # CASO 2: NO HAY OBJETIVOS ACTIVOS (Objetivos estáticos o sin nadie)
        # -------------------------------------------------------------
        time_since_target = now - self.last_target_time

        # Si el patrullaje de búsqueda está desactivado para esta cámara
        if not self.patrol_enabled:
            if self.last_sent_pan != 0.0 or self.last_sent_tilt != 0.0:
                self.hardware_controller.stop()
                self.last_sent_pan = 0.0
                self.last_sent_tilt = 0.0
            self.patrol_substate = "IDLE"
            self.is_patrolling = False
            self.status = "IDLE (Patrullaje Desactivado)"
            return

        # Si aún no vence el intervalo de inactividad
        if time_since_target < self.patrol_interval:
            if self.last_sent_pan != 0.0 or self.last_sent_tilt != 0.0:
                self.hardware_controller.stop()
                self.last_sent_pan = 0.0
                self.last_sent_tilt = 0.0
            self.patrol_substate = "IDLE"
            self.is_patrolling = False
            self.status = f"IDLE (Patrulla en {int(self.patrol_interval - time_since_target)}s)"
            return

        # -----------------------------------------------------------------
        # MODOS DE PATRULLAJE: RONDA DE PRESETS vs BARRIDO PANORÁMICO 3D
        # -----------------------------------------------------------------
        self.is_patrolling = True

        # =================================================================
        # MODALIDAD A: RONDA DE PRESETS (GUARD TOUR CON TIEMPOS INDIVIDUALES)
        # =================================================================
        if self.patrol_type == "preset_tour":
            if not self.preset_tour:
                self.status = "PATRULLA PRESETS: Lista vacía"
                return

            if self.tour_current_idx >= len(self.preset_tour):
                self.tour_current_idx = 0

            step = self.preset_tour[self.tour_current_idx]
            target_preset = str(step.get("preset", "1"))
            dwell_time = float(step.get("dwell_time_sec", 8.0))

            # Subestado 1: Enviando comando a la cámara y esperando arribo al preset
            if self.tour_substate == "MOVING":
                if self.tour_cmd_sent_time == 0.0:
                    logger.info(f"Ronda de Presets: Moviendo a Preset {target_preset} (Vigilancia programada: {dwell_time}s)...")
                    ok = self.hardware_controller.goto_preset(target_preset)
                    if not ok:
                        logger.warning(f"La cámara no cuenta con el Preset {target_preset} guardado. Saltando al siguiente paso de la ronda...")
                        self.tour_current_idx = (self.tour_current_idx + 1) % len(self.preset_tour)
                        self.status = f"RONDA: Preset {target_preset} no existe en cámara"
                        return

                    self.tour_cmd_sent_time = now
                    self.status = f"RONDA: Moviendo a Preset {target_preset}..."
                    return

                elapsed_move = now - self.tour_cmd_sent_time
                st = self.hardware_controller.get_status()
                is_moving = st.get("is_moving", False)

                # Consideramos arribo si la cámara reporta motor detenido o transcurrieron al menos 2.5s
                if (not is_moving and elapsed_move > 1.2) or elapsed_move > 3.5:
                    self.tour_substate = "DWELLING"
                    self.tour_dwell_start_time = now
                    self.tour_cmd_sent_time = 0.0
                    logger.info(f"Ronda de Presets: En Preset {target_preset}. Iniciando vigilancia por {dwell_time}s...")
                else:
                    self.status = f"RONDA: Moviendo a Preset {target_preset} ({elapsed_move:.1f}s)..."
                return


            # Subestado 2: Permaneciendo quieto en el preset vigilando con la IA
            elif self.tour_substate == "DWELLING":
                dwell_elapsed = now - self.tour_dwell_start_time
                rem = max(0.0, dwell_time - dwell_elapsed)
                self.status = f"RONDA: Preset {target_preset} (Vigilando {int(dwell_elapsed)}s/{int(dwell_time)}s)"

                if dwell_elapsed >= dwell_time:
                    # Pasar al siguiente preset de la ronda
                    self.tour_current_idx = (self.tour_current_idx + 1) % len(self.preset_tour)
                    self.tour_substate = "MOVING"
                    self.tour_cmd_sent_time = 0.0
                    next_step = self.preset_tour[self.tour_current_idx]
                    logger.info(f"Ronda de Presets: Tiempo cumplido. Siguiente parada: Preset {next_step.get('preset')}...")
                return

        # =================================================================
        # MODALIDAD B: BARRIDO PANORÁMICO CONTINUO 3D (355°)
        # =================================================================
        # Subestado 1: Inicializar o reanudar barrido horizontal
        if self.patrol_substate == "IDLE":
            self.substate_start_time = now
            if self.was_patrol_interrupted:
                # Continuar exactamente con el patrullaje y dirección que estaba haciendo
                self.patrol_direction = self.saved_patrol_direction
                self.patrol_tilt_direction = self.saved_patrol_tilt_direction
                self.patrol_substate = self.saved_patrol_substate
                self.was_patrol_interrupted = False
                logger.info(f"Reanudando patrullaje en curso: Continuando Paneo hacia {'Derecha' if self.patrol_direction > 0 else 'Izquierda'}...")
            else:
                self.patrol_substate = "PANNING"
                self.patrol_direction *= -1  # Alternar Izquierda / Derecha

            sweep_pan = self.patrol_speed * self.patrol_direction
            self.hardware_controller.continuous_move(pan_velocity=sweep_pan, tilt_velocity=0.0)
            self.last_sent_pan = sweep_pan
            self.last_sent_tilt = 0.0
            self.last_cmd_time = now

        # Subestado 2: Ejecutando Barrido Panorámico Horizontal (355°)
        if self.patrol_substate == "PANNING":
            elapsed = now - self.substate_start_time
            tilt_desc = "Calle/Cerca" if self.patrol_tilt_direction < 0 else "Horizonte/Lejos"
            self.status = f"PATRULLA 3D: Paneo {'Der' if self.patrol_direction > 0 else 'Izq'} [{tilt_desc}] ({int(elapsed)}s/{int(self.sweep_pan_time)}s)"

            # Keep-alive cada 1.0s para asegurar que el firmware Dahua mantenga el giro continuo
            if (now - self.last_cmd_time) >= 1.0:
                sweep_pan = self.patrol_speed * self.patrol_direction
                self.hardware_controller.continuous_move(pan_velocity=sweep_pan, tilt_velocity=0.0)
                self.last_cmd_time = now

            if elapsed >= self.sweep_pan_time:
                # Llegó al extremo horizontal: cambiar al ajuste de altura Tilt
                self.hardware_controller.stop()
                time.sleep(0.15)
                self.patrol_substate = "TILT_SHIFT"
                self.substate_start_time = now
                self.patrol_tilt_direction *= -1  # Alternar altura (Bajo <-> Alto)
                tilt_vel = self.tilt_speed * self.patrol_tilt_direction
                self.hardware_controller.continuous_move(pan_velocity=0.0, tilt_velocity=tilt_vel)
                self.last_sent_pan = 0.0
                self.last_sent_tilt = tilt_vel
                self.last_cmd_time = now

        # Subestado 3: Ajustando Inclinación Vertical (Tilt) entre barridos
        elif self.patrol_substate == "TILT_SHIFT":
            elapsed = now - self.substate_start_time
            self.status = f"PATRULLA 3D: Ajustando Altura ({'Subiendo' if self.patrol_tilt_direction > 0 else 'Bajando'})..."

            # Keep-alive cada 1.0s
            if (now - self.last_cmd_time) >= 1.0:
                tilt_vel = self.tilt_speed * self.patrol_tilt_direction
                self.hardware_controller.continuous_move(pan_velocity=0.0, tilt_velocity=tilt_vel)
                self.last_cmd_time = now

            if elapsed >= self.tilt_transition_time:
                # Completó el ajuste vertical: arrancar paneo en dirección contraria
                self.hardware_controller.stop()

                time.sleep(0.15)
                self.patrol_substate = "PANNING"
                self.substate_start_time = now
                self.patrol_direction *= -1  # Invertir dirección horizontal
                sweep_pan = self.patrol_speed * self.patrol_direction
                self.hardware_controller.continuous_move(pan_velocity=sweep_pan, tilt_velocity=0.0)
                self.last_sent_pan = sweep_pan
                self.last_sent_tilt = 0.0
                self.last_cmd_time = now

    def toggle_patrol(self):
        """Permite forzar el modo patrulla manualmente desde el teclado."""
        if not self.has_hardware_ptz():
            logger.info("Comando ignorado: la cámara actual no tiene PTZ físico.")
            return

        self.is_patrolling = not self.is_patrolling
        if self.is_patrolling:
            self.patrol_sweep_start_time = time.time()
            self.last_target_time = 0  # Forzar inicio
            logger.info("Patrullaje activado manualmente.")
        else:
            self.hardware_controller.stop()
            self.last_target_time = time.time()
            logger.info("Patrullaje pausado.")

    def stop(self):
        """Detiene el hardware PTZ con seguridad."""
        if self.hardware_controller:
            try:
                self.hardware_controller.stop()
                self.hardware_controller.close()
            except Exception:
                pass
        self.status = "STOPPED"

    def manual_move(self, pan: float, tilt: float, zoom: float = 0.0) -> bool:
        """Mueve la cámara manualmente desde la interfaz web pausando el patrullaje."""
        if not self.has_hardware_ptz():
            return False
        now = time.time()
        self.manual_override_until = now + self.manual_override_pause_sec

        if self.is_patrolling or self.patrol_substate != "IDLE":
            self.saved_patrol_direction = self.patrol_direction
            self.saved_patrol_tilt_direction = self.patrol_tilt_direction
            self.saved_patrol_substate = self.patrol_substate if self.patrol_substate != "IDLE" else "PANNING"
            self.was_patrol_interrupted = True
            self.is_patrolling = False
            self.patrol_substate = "IDLE"

        self.hardware_controller.continuous_move(pan, tilt, zoom)
        self.last_sent_pan = pan
        self.last_sent_tilt = tilt
        self.last_cmd_time = now
        self.status = f"MANUAL (P:{pan:+.2f}, T:{tilt:+.2f})"
        return True

    def manual_stop(self) -> bool:
        """Detiene el movimiento manual e inicia la pausa de gracia antes de reanudar la patrulla."""
        if not self.has_hardware_ptz():
            return False
        self.hardware_controller.stop()
        self.last_sent_pan = 0.0
        self.last_sent_tilt = 0.0
        now = time.time()
        self.manual_override_until = now + self.manual_override_pause_sec
        self.status = f"MANUAL_STOP (Pausa {self.manual_override_pause_sec:.1f}s)"
        return True

    def trigger_homing(self) -> bool:
        """Dispara la calibración inicial o retorno a Home bajo demanda."""
        if not self.has_hardware_ptz():
            return False
        self._start_homing_sequence()
        return True

    def toggle_tracking(self, enabled: Optional[bool] = None) -> bool:
        """Habilita o deshabilita el seguimiento IA motorizado."""
        if enabled is not None:
            self.tracking_enabled = bool(enabled)
        else:
            self.tracking_enabled = not self.tracking_enabled

        if not self.tracking_enabled and self.has_hardware_ptz():
            self.hardware_controller.stop()
            self.last_sent_pan = 0.0
            self.last_sent_tilt = 0.0

        logger.info(f"Modo Seguimiento IA cambiado a: {'ACTIVO' if self.tracking_enabled else 'DESACTIVADO'}")
        return self.tracking_enabled

    def toggle_patrol_enabled(self, enabled: Optional[bool] = None) -> bool:
        """Habilita o deshabilita el modo patrulla de búsqueda panorámica."""
        if enabled is not None:
            self.patrol_enabled = bool(enabled)
        else:
            self.patrol_enabled = not self.patrol_enabled

        if not self.patrol_enabled:
            self.is_patrolling = False
            self.patrol_substate = "IDLE"
            if self.has_hardware_ptz():
                self.hardware_controller.stop()
                self.last_sent_pan = 0.0
                self.last_sent_tilt = 0.0
        else:
            self.last_target_time = 0.0  # Forzar inicio pronto si está libre

        logger.info(f"Modo Patrullaje cambiado a: {'ACTIVO' if self.patrol_enabled else 'DESACTIVADO'}")
        return self.patrol_enabled

    def save_current_as_home(self) -> bool:
        """Guarda la posición actual de la cámara como Preset 1 (Home) en la cámara física."""
        if not self.has_hardware_ptz():
            return False
        success = self.hardware_controller.set_preset(preset_name="Home", preset_token=self.home_preset)
        if success:
            logger.info(f"Posición actual fijada como Preset {self.home_preset} (Home).")
        return bool(success)

    def go_to_home(self) -> bool:
        """Envía a la cámara a la posición Home guardada (Preset nativo o recalibración)."""
        if not self.has_hardware_ptz():
            return False
        if self.positioning_mode == "native_preset":
            self.is_homing = True
            self.homing_stage = "NATIVE"
            self.homing_start_time = time.time()
            return self.hardware_controller.goto_preset(self.home_preset)
        else:
            return self.trigger_homing()

    def get_camera_presets(self) -> list:
        """Consulta directamente a la cámara los presets físicos reales guardados."""
        if not self.has_hardware_ptz():
            return []
        try:
            return self.hardware_controller.get_presets()
        except Exception as e:
            logger.debug(f"Error consultando presets de hardware: {e}")
            return []

    def get_serial_number(self) -> Optional[str]:
        """Consulta el Número de Serie nativo único del hardware."""
        if not self.has_hardware_ptz():
            return None
        try:
            return self.hardware_controller.get_serial_number()
        except Exception as e:
            logger.debug(f"Error consultando Serial Number: {e}")
            return None


    def save_preset(self, preset_num: str, preset_name: str = "") -> bool:
        """Guarda la posición actual en cualquier preset numérico de la cámara."""
        if not self.has_hardware_ptz():
            return False
        name = preset_name or f"Preset {preset_num}"
        success = self.hardware_controller.set_preset(preset_name=name, preset_token=str(preset_num))
        if success:
            logger.info(f"Preset {preset_num} guardado exitosamente.")
        return bool(success)

    def goto_preset_num(self, preset_num: str) -> bool:
        """Mueve inmediatamente la cámara a un preset específico."""
        if not self.has_hardware_ptz():
            return False
        now = time.time()
        self.manual_override_until = now + self.manual_override_pause_sec
        success = self.hardware_controller.goto_preset(str(preset_num))
        if success:
            self.status = f"MANUAL: Preset {preset_num}"
        return bool(success)


    def get_state_dict(self) -> Dict[str, Any]:
        """Retorna el estado estructurado del PTZ para la API web."""
        now = time.time()
        native_status = {"supported": False, "pan": None, "tilt": None, "is_moving": False}
        if self.has_hardware_ptz():
            try:
                native_status = self.hardware_controller.get_status()
            except Exception:
                pass

        white_light_status = {"supported": False, "active": False}
        if self.has_hardware_ptz():
            try:
                white_light_status = self.hardware_controller.get_white_light_status()
            except Exception:
                pass

        return {
            "status": self.status,
            "has_hardware_ptz": self.has_hardware_ptz(),
            "tracking_enabled": self.tracking_enabled,
            "patrol_enabled": self.patrol_enabled,
            "positioning_mode": self.positioning_mode,
            "patrol_type": self.patrol_type,
            "preset_tour": self.preset_tour,
            "tour_current_idx": self.tour_current_idx,
            "home_preset": self.home_preset,
            "native_status": native_status,
            "white_light": white_light_status,
            "is_patrolling": self.is_patrolling,
            "is_homing": self.is_homing,
            "homing_stage": self.homing_stage if self.is_homing else "DONE",
            "use_digital_ptz": self.use_digital_ptz,
            "patrol_direction": "Derecha" if self.patrol_direction > 0 else "Izquierda",
            "patrol_tilt": "Horizonte" if self.patrol_tilt_direction > 0 else "Calle",
            "manual_override_active": now < self.manual_override_until,
            "manual_override_remaining": round(max(0.0, self.manual_override_until - now), 1)
        }

    def set_white_light(self, state: bool) -> bool:
        """Enciende o apaga el foco óptico / flash de la cámara conectada."""
        if not self.has_hardware_ptz():
            return False
        try:
            return self.hardware_controller.set_white_light(state)
        except Exception as e:
            logger.debug(f"Error conmutando luz blanca en PTZManager: {e}")
            return False

    def is_white_light_supported(self) -> bool:
        """Indica si el hardware soporta control óptico de luz blanca."""
        if not self.has_hardware_ptz():
            return False
        try:
            return bool(self.hardware_controller.get_white_light_status().get("supported", False))
        except Exception:
            return False

    def get_white_light_status(self) -> Dict[str, Any]:
        """Consulta el estado del foco de luz blanca."""
        if not self.has_hardware_ptz():
            return {"supported": False, "active": False}
        try:
            return self.hardware_controller.get_white_light_status()
        except Exception:
            return {"supported": False, "active": False}



