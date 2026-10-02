"""
Pruebas Unitarias para las 5 Nuevas Funcionalidades Modulares:
1. Auto-Zoom Dinámico (Encuadre Automático)
2. Suavizado de Movimiento PTZ (Filtro EMA)
3. Zonas de Interés y Geocercas (Polígonos Inclusión/Exclusión)
4. Grabación Automática de Clip Corto de Evidencia (MP4 en RAM)
5. Seguridad del Panel Web (Autenticación y Control de Acceso)
"""

import time
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np

from core.geofence import GeofenceManager
from core.event_recorder import EventRecorder
from core.ptz_manager import PTZManager
from web.app import app
from fastapi.testclient import TestClient


class TestModularFeatures(unittest.TestCase):

    def test_geofence_inclusion_and_exclusion(self):
        """Verifica la filtración por polígono tanto en modo inclusión como exclusión."""
        # Polígono cuadrado en el centro: [0.25, 0.25] a [0.75, 0.75]
        points = [
            {"x": 0.25, "y": 0.25},
            {"x": 0.75, "y": 0.25},
            {"x": 0.75, "y": 0.75},
            {"x": 0.25, "y": 0.75}
        ]
        geo = GeofenceManager({"enabled": True, "mode": "inclusion", "points": points})

        w, h = 1000, 1000
        # Objetivo 1: en el centro (x=500, y=500) -> Dentro de la zona
        target_inside = {
            "track_id": 1,
            "class_name": "person",
            "bbox": (450, 400, 550, 600), # base=(500, 600) -> dentro
            "center": (500, 500)
        }
        # Objetivo 2: en la esquina superior izquierda (x=100, y=100) -> Fuera
        target_outside = {
            "track_id": 2,
            "class_name": "person",
            "bbox": (50, 50, 150, 150), # base=(100, 150) -> fuera
            "center": (100, 100)
        }

        # Modo INCLUSIÓN:
        self.assertTrue(geo.is_target_allowed(target_inside, w, h))
        self.assertFalse(geo.is_target_allowed(target_outside, w, h))

        filtered = geo.filter_detections([target_inside, target_outside], w, h)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["track_id"], 1)

        # Modo EXCLUSIÓN:
        geo.update_config({"mode": "exclusion"})
        self.assertFalse(geo.is_target_allowed(target_inside, w, h))
        self.assertTrue(geo.is_target_allowed(target_outside, w, h))

        filtered_ex = geo.filter_detections([target_inside, target_outside], w, h)
        self.assertEqual(len(filtered_ex), 1)
        self.assertEqual(filtered_ex[0]["track_id"], 2)

        # Geocerca deshabilitada: permite todo
        geo.update_config({"enabled": False})
        self.assertTrue(geo.is_target_allowed(target_inside, w, h))
        self.assertTrue(geo.is_target_allowed(target_outside, w, h))

    def test_auto_zoom_framing(self):
        """Verifica que el Auto-Zoom acelere hacia adentro o afuera según el ratio de altura."""
        mock_hw = MagicMock()
        mock_hw.is_available.return_value = True

        ptz_cfg = {
            "enabled": True,
            "tracking_enabled": True,
            "deadzone_x": 0.05,
            "deadzone_y": 0.05,
            "auto_zoom": {
                "enabled": True,
                "target_height_ratio_min": 0.20,
                "target_height_ratio_max": 0.50,
                "zoom_speed": 0.40
            },
            "smoothing": {"enabled": False} # Sin EMA para testear valores directos
        }
        manager = PTZManager(ptz_cfg, {}, custom_controller=mock_hw)
        manager.is_homing = False

        frame_shape = (1000, 1920) # h=1000, w=1920

        # Caso A: Objetivo pequeño en el encuadre (altura = 100px -> ratio 0.10 < 0.20)
        target_small = {
            "track_id": 1,
            "class_name": "person",
            "norm_error": (0.0, 0.0),
            "height": 100
        }
        manager.update(target_small, frame_shape=frame_shape)
        # Debe emitir zoom in (+0.40)
        self.assertEqual(manager.current_zoom_state, "IN")
        mock_hw.continuous_move.assert_called_with(0.0, 0.0, 0.40)

        # Caso B: Objetivo grande en el encuadre (altura = 600px -> ratio 0.60 > 0.50)
        manager.last_cmd_time = 0.0
        target_large = {
            "track_id": 1,
            "class_name": "person",
            "norm_error": (0.0, 0.0),
            "height": 600
        }
        manager.update(target_large, frame_shape=frame_shape)
        # Debe emitir zoom out (-0.40)
        self.assertEqual(manager.current_zoom_state, "OUT")
        mock_hw.continuous_move.assert_called_with(0.0, 0.0, -0.40)

        # Caso C: Objetivo en rango óptimo (altura = 350px -> ratio 0.35)
        manager.last_cmd_time = 0.0
        target_optimum = {
            "track_id": 1,
            "class_name": "person",
            "norm_error": (0.0, 0.0),
            "height": 350
        }
        manager.update(target_optimum, frame_shape=frame_shape)
        self.assertEqual(manager.current_zoom_state, "IDLE")

    def test_ptz_smoothing_ema(self):
        """Verifica que el filtro EMA amortigüe las aceleraciones bruscas."""
        mock_hw = MagicMock()
        mock_hw.is_available.return_value = True

        ptz_cfg = {
            "enabled": True,
            "tracking_enabled": True,
            "tracking_speed": 1.0,
            "deadzone_x": 0.01,
            "deadzone_y": 0.01,
            "auto_zoom": {"enabled": False},
            "smoothing": {
                "enabled": True,
                "alpha": 0.25 # Factor amortiguador suave
            }
        }
        manager = PTZManager(ptz_cfg, {}, custom_controller=mock_hw)
        manager.is_homing = False

        # Enviar salto brusco de posición de norm_error_x = 0.80
        target = {
            "track_id": 1,
            "class_name": "person",
            "norm_error": (0.80, 0.0),
            "height": 200
        }
        manager.update(target, frame_shape=(1000, 1000))

        # En el primer paso: smoothed = (1 - 0.25)*0 + 0.25*0.80 = 0.20
        self.assertAlmostEqual(manager.smoothed_pan, 0.20, places=2)
        mock_hw.continuous_move.assert_called_with(0.20, 0.0, 0.0)

    def test_event_recorder_ram_buffer_and_trigger(self):
        """Verifica que EventRecorder mantenga frames en RAM y dispare grabación sin errores."""
        test_dir = Path("scratch/test_events")
        if test_dir.exists():
            shutil.rmtree(test_dir)

        rec = EventRecorder(
            cam_id="test_cam",
            cam_name="Cámara Test",
            recording_config={"enabled": True, "pre_alarm_sec": 1.0, "post_alarm_sec": 1.0},
            output_dir=str(test_dir)
        )

        dummy_frame = np.zeros((120, 160, 3), dtype=np.uint8)

        # Llenar búfer con varios cuadros
        for _ in range(15):
            rec.push_frame(dummy_frame)

        # Verificar que el búfer RAM contiene frames
        self.assertGreater(len(rec._ram_buffer), 10)

        # Disparar evento
        started = rec.trigger_event("TEST_ALERT", {"track_id": 99, "class_name": "person", "confidence": 0.95})
        self.assertTrue(started)
        self.assertTrue(rec._is_recording)

        # Simular llegada de cuadros post-alarma
        rec._record_end_time = time.time() - 0.01 # Forzar fin de post-alarma
        rec.push_frame(dummy_frame)

        # Esperar brevemente a que el hilo escritor termine de generar archivos
        time.sleep(0.3)
        mp4_files = list(test_dir.glob("*.mp4"))
        json_files = list(test_dir.glob("*.json"))
        self.assertGreaterEqual(len(mp4_files), 1)
        self.assertGreaterEqual(len(json_files), 1)

        # Limpiar directorio de test
        shutil.rmtree(test_dir)

    def test_web_auth_flow(self):
        """Verifica el flujo de seguridad web (login con credenciales y middleware)."""
        from web.app import web_manager
        client = TestClient(app)

        # Configurar seguridad activa
        web_manager.config_data["security"] = {
            "auth_enabled": True,
            "admin_username": "superadmin",
            "admin_password": "secretpassword"
        }

        # 1. Petición sin credenciales a endpoint protegido -> 401
        res_unauth = client.get("/api/config")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Login con credenciales incorrectas -> 401
        res_bad_login = client.post("/api/login", json={"username": "superadmin", "password": "wrong"})
        self.assertEqual(res_bad_login.status_code, 401)

        # 3. Login exitoso -> 200 y cookie establecida
        res_login = client.post("/api/login", json={"username": "superadmin", "password": "secretpassword"})
        self.assertEqual(res_login.status_code, 200)
        self.assertIn("session_auth", res_login.cookies)

        # 4. Petición protegida con cookie de sesión -> 200
        client.cookies.set("session_auth", res_login.cookies["session_auth"])
        res_auth = client.get("/api/config")
        self.assertEqual(res_auth.status_code, 200)

        # Desactivar auth_enabled para no interferir con otras pruebas
        web_manager.config_data["security"]["auth_enabled"] = False

    def test_update_config_api(self):
        """Verifica que POST /api/config procesa correctamente new_cfg y sincroniza las clases."""
        import tempfile
        from web.app import web_manager
        client = TestClient(app)
        if "security" not in web_manager.config_data:
            web_manager.config_data["security"] = {}
        web_manager.config_data["security"]["auth_enabled"] = False

        orig_path = web_manager.config_path
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
            web_manager.config_path = tmp.name

        try:
            test_payload = {
                "config": {
                    "detection": {
                        "model_name": "yolov8n.pt",
                        "confidence": 0.35,
                        "target_classes": ["person"]
                    },
                    "cameras": [
                        {
                            "id": "cam_test_sync",
                            "name": "Cam Test Sync",
                            "tracking_priority": ["car", "dog"]
                        }
                    ]
                }
            }
            res = client.post("/api/config", json=test_payload)
            self.assertEqual(res.status_code, 200)
            resp_json = res.json()
            self.assertTrue(resp_json.get("success"))
            saved_classes = web_manager.config_data["detection"]["target_classes"]
            self.assertIn("car", saved_classes)
            self.assertIn("dog", saved_classes)
            self.assertIn("person", saved_classes)
        finally:
            web_manager.config_path = orig_path


if __name__ == "__main__":
    unittest.main()
