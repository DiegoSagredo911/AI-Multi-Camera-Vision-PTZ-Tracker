"""
Controlador PTZ Universal basado en el estándar ONVIF (Profile S/T).
Permite controlar cámaras PTZ de Dahua, Hikvision, TP-Link, Axis, etc.
"""

import logging
from typing import Optional
from .base import BasePTZController

logger = logging.getLogger("OnvifPTZ")


class OnvifPTZController(BasePTZController):
    """
    Controlador PTZ estándar ONVIF.
    Detecta de forma segura si la cámara soporta PTZ y ejecuta movimientos continuos.
    """

    def __init__(
        self,
        ip: str,
        port: int = 80,
        username: str = "admin",
        password: str = "password123"
    ):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password

        self._available = False
        self._camera = None
        self._ptz_service = None
        self._media_service = None
        self._profile_token: Optional[str] = None
        self._request_type = None

        self._initialize()

    def _initialize(self):
        """Intenta inicializar la conexión ONVIF y verificar capacidades PTZ."""
        try:
            from onvif import ONVIFCamera
            logger.info(f"Intentando conectar a ONVIF en {self.ip}:{self.port}...")
            
            # Conexión ONVIF
            self._camera = ONVIFCamera(
                self.ip,
                self.port,
                self.username,
                self.password,
                no_cache=True
            )

            # Servicios Media y PTZ
            self._media_service = self._camera.create_media_service()
            profiles = self._media_service.GetProfiles()
            if not profiles:
                logger.warning("No se encontraron perfiles de medios en la cámara ONVIF.")
                self._available = False
                return

            self._profile_token = profiles[0].token

            # Servicio PTZ
            self._ptz_service = self._camera.create_ptz_service()
            
            # Verificar si el perfil soporta PTZ
            configs = self._ptz_service.GetConfigurations()
            if not configs:
                logger.info("La cámara responde a ONVIF pero NO cuenta con configuración PTZ (cámara fija).")
                self._available = False
                return

            self._available = True
            logger.info("Controlador ONVIF PTZ conectado y listo.")

        except Exception as e:
            logger.info(f"Cámara sin soporte ONVIF PTZ o no accesible: {e}")
            self._available = False

    def is_available(self) -> bool:
        return self._available

    def continuous_move(
        self,
        pan_velocity: float,
        tilt_velocity: float,
        zoom_velocity: float = 0.0
    ) -> bool:
        """
        Envía comando de ContinuousMove a través de ONVIF.
        pan: [-1.0, 1.0], tilt: [-1.0, 1.0], zoom: [-1.0, 1.0]
        """
        if not self._available or not self._ptz_service:
            return False

        try:
            # Clamping de velocidades
            pan_val = max(min(pan_velocity, 1.0), -1.0)
            tilt_val = max(min(tilt_velocity, 1.0), -1.0)
            zoom_val = max(min(zoom_velocity, 1.0), -1.0)

            request = self._ptz_service.create_type("ContinuousMove")
            request.ProfileToken = self._profile_token

            vel_dict = {
                "PanTilt": {"x": pan_val, "y": tilt_val}
            }
            if abs(zoom_val) > 0.01:
                vel_dict["Zoom"] = {"x": zoom_val}

            request.Velocity = vel_dict
            self._ptz_service.ContinuousMove(request)
            return True

        except Exception as e:
            logger.error(f"Error al enviar comando ONVIF ContinuousMove: {e}")
            return False

    def stop(self) -> bool:
        """Detiene el movimiento PTZ."""
        if not self._available or not self._ptz_service:
            return False

        try:
            request = self._ptz_service.create_type("Stop")
            request.ProfileToken = self._profile_token
            request.PanTilt = True
            request.Zoom = True
            self._ptz_service.Stop(request)
            return True
        except Exception as e:
            logger.error(f"Error al enviar comando ONVIF Stop: {e}")
            return False

    def get_status(self) -> dict:
        """
        Consulta las coordenadas exactas de la cámara y el estado de movimiento (ONVIF GetStatus).
        """
        if not self._available or not self._ptz_service:
            return {"supported": False, "pan": None, "tilt": None, "is_moving": False}

        try:
            status = self._ptz_service.GetStatus({"ProfileToken": self._profile_token})
            pan = None
            tilt = None
            is_moving = False

            if hasattr(status, "Position") and status.Position is not None:
                pt = getattr(status.Position, "PanTilt", None)
                if pt is not None:
                    pan = getattr(pt, "x", None)
                    tilt = getattr(pt, "y", None)

            if hasattr(status, "MoveStatus") and status.MoveStatus is not None:
                pt_move = getattr(status.MoveStatus, "PanTilt", None)
                if pt_move is not None:
                    is_moving = (str(pt_move).upper() == "MOVING")

            return {
                "supported": (pan is not None or tilt is not None),
                "pan": pan,
                "tilt": tilt,
                "is_moving": is_moving
            }
        except Exception as e:
            logger.debug(f"La cámara no soporta ONVIF GetStatus: {e}")
            return {"supported": False, "pan": None, "tilt": None, "is_moving": False}

    def goto_preset(self, preset_token: str = "1") -> bool:
        """
        Envía a la cámara al preset nativo indicado (GotoPreset).
        """
        if not self._available or not self._ptz_service:
            return False

        try:
            request = self._ptz_service.create_type("GotoPreset")
            request.ProfileToken = self._profile_token
            request.PresetToken = str(preset_token)
            self._ptz_service.GotoPreset(request)
            logger.info(f"Comando ONVIF GotoPreset({preset_token}) enviado exitosamente.")
            return True
        except Exception as e:
            logger.warning(f"Error al ejecutar ONVIF GotoPreset({preset_token}): {e}")
            return False

    def set_preset(self, preset_name: str = "Home", preset_token: str = "1") -> bool:
        """
        Guarda la posición actual de la cámara como un Preset nativo (SetPreset).
        """
        if not self._available or not self._ptz_service:
            return False

        try:
            request = self._ptz_service.create_type("SetPreset")
            request.ProfileToken = self._profile_token
            request.PresetName = preset_name
            request.PresetToken = str(preset_token)
            self._ptz_service.SetPreset(request)
            logger.info(f"Comando ONVIF SetPreset({preset_name}, {preset_token}) guardado exitosamente.")
            return True
        except Exception as e:
            logger.warning(f"Error al ejecutar ONVIF SetPreset: {e}")
            return False

    def get_presets(self) -> list:
        """
        Lista los presets nativos configurados en la cámara.
        """
        if not self._available or not self._ptz_service:
            return []

        try:
            presets = self._ptz_service.GetPresets({"ProfileToken": self._profile_token})
            result = []
            if presets:
                for p in presets:
                    token = getattr(p, "token", "")
                    name = getattr(p, "Name", f"Preset {token}")
                    result.append({"token": str(token), "name": str(name)})
            return result
        except Exception as e:
            logger.debug(f"Error al obtener ONVIF GetPresets: {e}")
            return []

    def get_serial_number(self) -> Optional[str]:
        """
        Consulta el Número de Serie nativo del dispositivo vía ONVIF GetDeviceInformation.
        """
        if not self._available or not self._camera:
            return None

        try:
            device_service = self._camera.create_devicemgmt_service()
            info = device_service.GetDeviceInformation()
            sn = getattr(info, "SerialNumber", None)
            if sn:
                clean_sn = str(sn).strip().replace(" ", "_")
                logger.info(f"Número de Serie ONVIF detectado: {clean_sn}")
                return clean_sn
        except Exception as e:
            logger.debug(f"Error al obtener GetDeviceInformation ONVIF: {e}")

        return None

    def close(self):
        self.stop()
        self._available = False


