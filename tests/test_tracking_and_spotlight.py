import unittest
import time
from unittest.mock import MagicMock

from core.detector import ObjectDetector
from core.spotlight_manager import SpotlightManager


class DummyDetector:
    """Mock o detector básico para testear select_primary_target sin cargar YOLO completo."""
    def __init__(self):
        pass


class TestTrackingPriorityAndSpotlight(unittest.TestCase):

    def test_priority_target_selection(self):
        # Crear detector con método select_primary_target
        detector = ObjectDetector.__new__(ObjectDetector)
        detector.previous_positions = {}

        # Escenario 1: Coexisten un auto y una persona
        detections = [
            {
                "track_id": 1,
                "class_name": "car",
                "norm_error": (0.05, 0.05),
                "speed_px": 10.0,
                "is_static": False
            },
            {
                "track_id": 2,
                "class_name": "person",
                "norm_error": (0.20, 0.20),
                "speed_px": 6.0,
                "is_static": False
            }
        ]

        # Prioridad: 1º person, 2º car
        priority = ["person", "car"]
        selected = detector.select_primary_target(detections, current_target_id=None, priority_classes=priority)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["track_id"], 2)
        self.assertEqual(selected["class_name"], "person")

        # Escenario 2: Cambiar la prioridad: 1º car, 2º person
        priority_car_first = ["car", "person"]
        selected_car = detector.select_primary_target(detections, current_target_id=None, priority_classes=priority_car_first)
        self.assertIsNotNone(selected_car)
        self.assertEqual(selected_car["track_id"], 1)
        self.assertEqual(selected_car["class_name"], "car")

        # Escenario 3: Estaba siguiendo al auto (ID=1), pero aparece una persona (ID=2) con mayor prioridad
        selected_switch = detector.select_primary_target(detections, current_target_id=1, priority_classes=["person", "car"])
        self.assertEqual(selected_switch["track_id"], 2, "Debe conmutar inmediatamente a la persona por tener mayor prioridad")

        # Escenario 4: Solo hay autos y motos, y solo permitimos 'person'
        only_vehicles = [
            {"track_id": 10, "class_name": "car", "norm_error": (0, 0), "speed_px": 5.0, "is_static": False}
        ]
        selected_none = detector.select_primary_target(only_vehicles, current_target_id=None, priority_classes=["person"])
        self.assertIsNone(selected_none, "No debe seleccionar ninguna clase no permitida")

    def test_update_target_classes(self):
        detector = ObjectDetector.__new__(ObjectDetector)
        detector.class_names = {0: "person", 2: "car", 16: "dog", 15: "cat"}
        detector.target_classes = ["person"]
        detector.target_class_ids = [0]

        detector.update_target_classes(["person", "car", "dog"])
        self.assertIn("person", detector.target_classes)
        self.assertIn("car", detector.target_classes)
        self.assertIn("dog", detector.target_classes)
        self.assertEqual(sorted(detector.target_class_ids), [0, 2, 16])

    def test_spotlight_manager_delay_and_one_shot(self):
        config = {
            "enabled": True,
            "night_only": False,
            "trigger_classes": ["person"],
            "trigger_delay_sec": 3.0,
            "flash_duration_sec": 3.0,
            "one_shot_per_target": True,
            "cooldown_sec": 8.0
        }
        spot = SpotlightManager(cam_name="Cam_Test", config=config)

        mock_ptz = MagicMock()
        mock_ptz.has_hardware_ptz.return_value = True
        mock_ptz.set_white_light.return_value = True

        person_target = {
            "track_id": 101,
            "class_name": "person",
            "norm_error": (0.0, 0.0)
        }

        # Tiempo inicial t=100.0
        start_time = 100.0
        
        # 1. Simular detección durante los primeros 2 segundos (t=100.0 a 102.0)
        # Mock time.time()
        with unittest.mock.patch("time.time", return_value=100.0):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(spot.is_flashing)
            self.assertTrue(spot.is_dwelling)
            self.assertAlmostEqual(spot.dwell_remaining, 3.0, places=1)
            mock_ptz.set_white_light.assert_not_called()

        with unittest.mock.patch("time.time", return_value=102.0):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(spot.is_flashing)
            self.assertTrue(spot.is_dwelling)
            self.assertAlmostEqual(spot.dwell_remaining, 1.0, places=1)
            mock_ptz.set_white_light.assert_not_called()

        # 2. Cumplidos 3 segundos de seguimiento (t=103.1): DEBE DISPARAR EL FLASH
        with unittest.mock.patch("time.time", return_value=103.1):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertTrue(spot.is_flashing, "El flash debe encenderse tras 3 segundos de seguimiento continuo")
            self.assertFalse(spot.is_dwelling)
            mock_ptz.set_white_light.assert_called_with(True)

        # 3. Durante el destello (t=104.5): El flash sigue activo
        with unittest.mock.patch("time.time", return_value=104.5):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertTrue(spot.is_flashing)

        # 4. Transcurrida la duración del flash (t=106.2, duró > 3.0s): DEBE APAGARSE
        with unittest.mock.patch("time.time", return_value=106.2):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(spot.is_flashing, "El flash debe apagarse tras su tiempo de duración")
            mock_ptz.set_white_light.assert_called_with(False)

        # 5. REGLA CLAVE: Continúa siguiendo a la misma persona (ID=101) por 20 segundos más (t=120.0).
        # ¡NO DEBE VOLVER A ENCENDER EL FLASH! Un solo flash por persona.
        mock_ptz.set_white_light.reset_mock()
        with unittest.mock.patch("time.time", return_value=120.0):
            spot.evaluate([person_target], person_target, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(spot.is_flashing, "No debe re-encender el flash para la misma persona mientras se la sigue")
            self.assertFalse(spot.is_dwelling)
            mock_ptz.set_white_light.assert_not_called()

        # 6. Aparece una NUEVA persona (ID=202)
        new_person = {
            "track_id": 202,
            "class_name": "person",
            "norm_error": (0.0, 0.0)
        }
        with unittest.mock.patch("time.time", return_value=130.0):
            spot.evaluate([new_person], new_person, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(spot.is_flashing)
            self.assertTrue(spot.is_dwelling)

        with unittest.mock.patch("time.time", return_value=133.2):
            spot.evaluate([new_person], new_person, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertTrue(spot.is_flashing, "La nueva persona debe recibir su propio flash único tras 3s")
            mock_ptz.set_white_light.assert_called_with(True)

    def test_spotlight_night_only_and_class_filter(self):
        config = {
            "enabled": True,
            "night_only": True,
            "trigger_classes": ["person"],
            "trigger_delay_sec": 0.0,  # Inmediato
            "flash_duration_sec": 2.0
        }
        spot = SpotlightManager(cam_name="Cam_Night", config=config)
        mock_ptz = MagicMock()
        mock_ptz.has_hardware_ptz.return_value = True

        person = {"track_id": 1, "class_name": "person"}
        car = {"track_id": 2, "class_name": "car"}

        # Caso de día (is_night_vision=False) con night_only=True -> No debe disparar
        with unittest.mock.patch("time.time", return_value=10.0):
            flashing = spot.evaluate([person], person, is_night_vision=False, ptz_manager=mock_ptz)
            self.assertFalse(flashing)

        # Caso de noche pero clase auto (no coincide con person) -> No debe disparar
        with unittest.mock.patch("time.time", return_value=20.0):
            flashing = spot.evaluate([car], car, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertFalse(flashing)

        # Caso de noche y persona -> Dispara
        with unittest.mock.patch("time.time", return_value=30.0):
            flashing = spot.evaluate([person], person, is_night_vision=True, ptz_manager=mock_ptz)
            self.assertTrue(flashing)

    def test_modular_targets_several_some_none(self):
        mock_ptz = MagicMock()
        mock_ptz.has_hardware_ptz.return_value = True

        person = {"track_id": 1, "class_name": "person"}
        moto = {"track_id": 2, "class_name": "motorcycle"}
        car = {"track_id": 3, "class_name": "car"}

        # 1. Caso: Seleccionar varios (person y motorcycle)
        spot = SpotlightManager(cam_name="Cam1", config={"enabled": True, "night_only": False, "trigger_classes": ["person", "motorcycle"], "trigger_delay_sec": 0.0})
        self.assertTrue(spot.evaluate([person], person, is_night_vision=True, ptz_manager=mock_ptz))
        spot.is_flashing = False
        self.assertTrue(spot.evaluate([moto], moto, is_night_vision=True, ptz_manager=mock_ptz))
        spot.is_flashing = False
        self.assertFalse(spot.evaluate([car], car, is_night_vision=True, ptz_manager=mock_ptz))

        # 2. Caso: Seleccionar solo algunos (solo car)
        spot_car = SpotlightManager(cam_name="Cam2", config={"enabled": True, "night_only": False, "trigger_classes": ["car"], "trigger_delay_sec": 0.0})
        self.assertFalse(spot_car.evaluate([person], person, is_night_vision=True, ptz_manager=mock_ptz))
        self.assertTrue(spot_car.evaluate([car], car, is_night_vision=True, ptz_manager=mock_ptz))

        # 3. Caso: Ninguno (trigger_classes = [])
        spot_none = SpotlightManager(cam_name="Cam3", config={"enabled": True, "night_only": False, "trigger_classes": [], "trigger_delay_sec": 0.0})
        self.assertFalse(spot_none.evaluate([person], person, is_night_vision=True, ptz_manager=mock_ptz))
        self.assertFalse(spot_none.evaluate([car], car, is_night_vision=True, ptz_manager=mock_ptz))
        self.assertFalse(spot_none.evaluate([moto], moto, is_night_vision=True, ptz_manager=mock_ptz))


if __name__ == "__main__":
    unittest.main()
