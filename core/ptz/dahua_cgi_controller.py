"""
Controlador PTZ nativo de Dahua mediante API HTTP CGI.
Alternativa ligera al protocolo SOAP de ONVIF.
"""

import logging
from typing import Optional, Dict, Any
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from .base import BasePTZController

logger = logging.getLogger("DahuaCgiPTZ")


class DahuaCgiPTZController(BasePTZController):
    """
    Controlador PTZ mediante la API HTTP CGI de Dahua:
    /cgi-bin/ptz.cgi?action=start&channel=1&code=...
    """

    def __init__(
        self,
        ip: str,
        port: int = 80,
        username: str = "admin",
        password: str = "password123",
        channel: int = 1
    ):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.channel = channel

        self.base_url = f"http://{self.ip}:{self.port}/cgi-bin/ptz.cgi"
        self._auth = HTTPDigestAuth(self.username, self.password)
        self._available = False
        self._current_command = None

        # Control óptico de luz blanca / foco / flash (Cero audio)
        self._white_light_supported: Optional[bool] = None
        self._white_light_method: Optional[str] = None  # "lighting" o "ptz_aux"
        self._white_light_state: bool = False

        self._check_availability()

    def _check_availability(self):
        """Verifica si la cámara responde a comandos PTZ CGI."""
        try:
            # Comando de prueba de detención
            url = f"{self.base_url}?action=stop&channel={self.channel}&code=Up&arg1=0&arg2=0&arg3=0"
            res = requests.get(url, auth=self._auth, timeout=3.0)
            
            # Si responde 401, reintentar con BasicAuth
            if res.status_code == 401:
                self._auth = HTTPBasicAuth(self.username, self.password)
                res = requests.get(url, auth=self._auth, timeout=3.0)

            if res.status_code == 200 and "OK" in res.text:
                self._available = True
                logger.info("Controlador Dahua CGI PTZ verificado exitosamente.")
            else:
                logger.info(f"Cámara Dahua sin soporte PTZ mecánico por CGI (Status {res.status_code}).")
                self._available = False
        except Exception as e:
            logger.info(f"Cámara Dahua no disponible para PTZ CGI: {e}")
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
        Calcula la dirección y velocidad Dahua (1..8) y envía el comando.
        """
        if not self._available:
            return False

        # Determinar código de movimiento según signos
        speed_pan = int(abs(pan_velocity) * 8)
        speed_tilt = int(abs(tilt_velocity) * 8)
        speed = max(speed_pan, speed_tilt, 1)

        code = None
        if pan_velocity > 0.15 and tilt_velocity > 0.15:
            code = "RightUp"
        elif pan_velocity > 0.15 and tilt_velocity < -0.15:
            code = "RightDown"
        elif pan_velocity < -0.15 and tilt_velocity > 0.15:
            code = "LeftUp"
        elif pan_velocity < -0.15 and tilt_velocity < -0.15:
            code = "LeftDown"
        elif pan_velocity > 0.15:
            code = "Right"
        elif pan_velocity < -0.15:
            code = "Left"
        elif tilt_velocity > 0.15:
            code = "Up"
        elif tilt_velocity < -0.15:
            code = "Down"
        elif zoom_velocity > 0.15:
            code = "ZoomTele"
            speed = max(1, min(8, int(abs(zoom_velocity) * 8)))
        elif zoom_velocity < -0.15:
            code = "ZoomWide"
            speed = max(1, min(8, int(abs(zoom_velocity) * 8)))

        if code is None:
            return self.stop()

        try:
            url = f"{self.base_url}?action=start&channel={self.channel}&code={code}&arg1={speed}&arg2={speed}&arg3=0"
            res = requests.get(url, auth=self._auth, timeout=1.5)
            self._current_command = code
            return res.status_code == 200
        except Exception as e:
            logger.error(f"Error en Dahua CGI ContinuousMove: {e}")
            return False

    def stop(self) -> bool:
        if not self._available:
            return False

        try:
            code = self._current_command or "Up"
            url = f"{self.base_url}?action=stop&channel={self.channel}&code={code}&arg1=0&arg2=0&arg3=0"
            res = requests.get(url, auth=self._auth, timeout=1.5)
            self._current_command = None
            return res.status_code == 200
        except Exception as e:
            logger.error(f"Error en Dahua CGI Stop: {e}")
            return False

    def get_status(self) -> dict:
        """
        Consulta coordenadas actuales a través del endpoint Dahua CGI getStatus.
        """
        if not self._available:
            return {"supported": False, "pan": None, "tilt": None, "is_moving": False}

        try:
            url = f"{self.base_url}?action=getStatus&channel={self.channel}"
            res = requests.get(url, auth=self._auth, timeout=1.5)
            if res.status_code == 200 and "status.Position" in res.text:
                pan = None
                tilt = None
                for line in res.text.splitlines():
                    line = line.strip()
                    if line.startswith("status.Position[0]="):
                        pan = float(line.split("=")[1])
                    elif line.startswith("status.Position[1]="):
                        tilt = float(line.split("=")[1])
                return {
                    "supported": True,
                    "pan": pan,
                    "tilt": tilt,
                    "is_moving": (self._current_command is not None)
                }
        except Exception as e:
            logger.debug(f"Error al consultar getStatus Dahua CGI: {e}")

        return {"supported": False, "pan": None, "tilt": None, "is_moving": False}

    def goto_preset(self, preset_token: str = "1") -> bool:
        """
        Envía comando Dahua CGI GotoPreset (arg2 = preset_id).
        """
        if not self._available:
            return False

        try:
            preset_num = int(preset_token)
        except ValueError:
            preset_num = 1

        try:
            url = f"{self.base_url}?action=start&channel={self.channel}&code=GotoPreset&arg1=0&arg2={preset_num}&arg3=0"
            res = requests.get(url, auth=self._auth, timeout=2.0)
            if res.status_code == 200 and "OK" in res.text:
                logger.info(f"Comando Dahua CGI GotoPreset({preset_num}) ejecutado con éxito.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error en Dahua CGI GotoPreset: {e}")
            return False

    def set_preset(self, preset_name: str = "Home", preset_token: str = "1") -> bool:
        """
        Guarda la posición actual como Preset mediante Dahua CGI SetPreset.
        """
        if not self._available:
            return False

        try:
            preset_num = int(preset_token)
        except ValueError:
            preset_num = 1

        try:
            url = f"{self.base_url}?action=start&channel={self.channel}&code=SetPreset&arg1=0&arg2={preset_num}&arg3=0"
            res = requests.get(url, auth=self._auth, timeout=2.0)
            if res.status_code == 200 and "OK" in res.text:
                logger.info(f"Comando Dahua CGI SetPreset({preset_num}) guardado exitosamente.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error en Dahua CGI SetPreset: {e}")
            return False

    def get_presets(self) -> list:
        """
        Obtiene los presets guardados en la cámara mediante Dahua CGI getPresets.
        """
        if not self._available:
            return []

        try:
            url = f"{self.base_url}?action=getPresets&channel={self.channel}"
            res = requests.get(url, auth=self._auth, timeout=2.0)
            if res.status_code != 200:
                return []

            # Formato Dahua CGI:
            # presets[0].Index=1
            # presets[0].Name=Entrada
            # presets[1].Index=2
            # presets[1].Name=Patio
            import re
            presets_by_idx = {}
            for line in res.text.splitlines():
                line = line.strip()
                m = re.match(r"presets\[(\d+)\]\.(Index|Name)=(.*)", line)
                if m:
                    arr_idx, field, val = m.groups()
                    if arr_idx not in presets_by_idx:
                        presets_by_idx[arr_idx] = {}
                    presets_by_idx[arr_idx][field] = val.strip()

            result = []
            for arr_idx in sorted(presets_by_idx.keys(), key=lambda x: int(x)):
                item = presets_by_idx[arr_idx]
                token = item.get("Index")
                if token is not None:
                    name = item.get("Name") or f"Preset {token}"
                    result.append({"token": str(token), "name": str(name)})

            return result
        except Exception as e:
            logger.debug(f"Error al consultar Dahua CGI getPresets: {e}")
            return []

    def get_serial_number(self) -> Optional[str]:
        """
        Consulta el Número de Serie nativo Dahua vía magicBox.cgi getSerialNo o getDeviceType.
        """
        if not self._available:
            return None

        try:
            url = f"http://{self.ip}:{self.port}/cgi-bin/magicBox.cgi?action=getSerialNo"
            res = requests.get(url, auth=self._auth, timeout=2.0)
            if res.status_code == 200 and "sn=" in res.text:
                sn = res.text.split("sn=")[-1].strip().splitlines()[0]
                if sn:
                    clean_sn = sn.strip().replace(" ", "_")
                    logger.info(f"Número de Serie Dahua CGI detectado: {clean_sn}")
                    return clean_sn
        except Exception as e:
            logger.debug(f"Error al consultar Dahua CGI getSerialNo: {e}")

        return None

    def _detect_white_light_capabilities(self):
        """
        Sondea silenciosamente a la cámara Dahua para verificar si cuenta con
        soporte para iluminador óptico / luz blanca (Cero audio/sirenas).
        """
        if not self._available:
            self._white_light_supported = False
            return

        try:
            # 1. Probar gestor de iluminación Dahua Lighting
            lighting_url = f"http://{self.ip}:{self.port}/cgi-bin/configManager.cgi?action=getConfig&name=Lighting"
            res = requests.get(lighting_url, auth=self._auth, timeout=2.0)
            if res.status_code == 200 and ("Lighting" in res.text or "table.Lighting" in res.text):
                self._white_light_supported = True
                self._white_light_method = "lighting"
                logger.info("Soporte de Luz Blanca Óptica Dahua detectado (Método: Lighting CGI).")
                return

            # 2. Probar si soporta comando auxiliar óptico PTZ Aux1
            aux_test_url = f"{self.base_url}?action=stop&channel={self.channel}&code=Aux1&arg1=1&arg2=0&arg3=0"
            res_aux = requests.get(aux_test_url, auth=self._auth, timeout=2.0)
            if res_aux.status_code == 200 and "OK" in res_aux.text:
                self._white_light_supported = True
                self._white_light_method = "ptz_aux"
                logger.info("Soporte de Luz Blanca Óptica Dahua detectado (Método: PTZ Aux1).")
                return

            # Si ninguno respondió afirmativamente
            self._white_light_supported = False
            self._white_light_method = None
            logger.debug("La cámara Dahua no expuso control de iluminador óptico por CGI.")
        except Exception as e:
            logger.debug(f"Error detectando capacidades de luz blanca Dahua: {e}")
            self._white_light_supported = False

    def set_white_light(self, state: bool) -> bool:
        """
        Enciende (state=True) o apaga (state=False) el foco de luz blanca de forma 100% silenciosa y óptica.
        Garantizado: Cero comandos de sonido, sirenas o altavoces.
        """
        if not self._available:
            return False

        if self._white_light_supported is None:
            self._detect_white_light_capabilities()

        # Si el método no fue detectado aún, intentar con lighting por defecto
        method = self._white_light_method or "lighting"

        success = False
        try:
            if method == "lighting":
                # Intentar método 1: Modo Manual con NearLight al 100% / Auto al apagar
                if state:
                    url = f"http://{self.ip}:{self.port}/cgi-bin/configManager.cgi?action=setConfig&Lighting[0][0].Mode=Manual&Lighting[0][0].NearLight[0].Light=100"
                else:
                    url = f"http://{self.ip}:{self.port}/cgi-bin/configManager.cgi?action=setConfig&Lighting[0][0].Mode=Auto"

                res = requests.get(url, auth=self._auth, timeout=2.0)
                if res.status_code == 200 and "OK" in res.text:
                    success = True
                else:
                    # Método alternativo: ForceOn / ForceOff
                    mode_val = "ForceOn" if state else "ForceOff"
                    alt_url = f"http://{self.ip}:{self.port}/cgi-bin/configManager.cgi?action=setConfig&Lighting[0][0].Mode={mode_val}"
                    alt_res = requests.get(alt_url, auth=self._auth, timeout=2.0)
                    if alt_res.status_code == 200 and "OK" in alt_res.text:
                        success = True

            elif method == "ptz_aux":
                action = "start" if state else "stop"
                url = f"{self.base_url}?action={action}&channel={self.channel}&code=Aux1&arg1=1&arg2=0&arg3=0"
                res = requests.get(url, auth=self._auth, timeout=2.0)
                if res.status_code == 200 and "OK" in res.text:
                    success = True

            if success:
                self._white_light_state = state
                self._white_light_supported = True
                logger.info(f"Luz Blanca Dahua {'ENCENDIDA (Flash)' if state else 'APAGADA (Reposo)'} con éxito.")
                return True
            else:
                logger.debug(f"Fallo al enviar comando de luz blanca Dahua (state={state}).")
                return False

        except Exception as e:
            logger.error(f"Error al conmutar luz blanca Dahua: {e}")
            return False

    def get_white_light_status(self) -> Dict[str, Any]:
        """
        Retorna el estado actual del iluminador óptico.
        """
        return {
            "supported": bool(self._white_light_supported) if self._white_light_supported is not None else False,
            "active": self._white_light_state,
            "method": self._white_light_method
        }




