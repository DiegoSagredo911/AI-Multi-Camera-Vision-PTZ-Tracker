/**
 * AI Vision & PTZ Hub - Lógica de Cliente Web
 * Conexión WebSocket para telemetría en tiempo real y llamadas REST de control y configuración.
 */

let activeCameraId = "cam_ptz"; // ID de cámara PTZ por defecto
let manualSpeed = 0.45;
let currentConfig = null;
let telemetrySocket = null;
let isMoving = false;

// ---------------------------------------------------------------------------
// 1. INICIALIZACIÓN
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initWebSocket();
    loadConfig();
    setupKeyboardControls();
});

// ---------------------------------------------------------------------------
// 2. NAVEGACIÓN ENTRE PESTAÑAS (MONITOREO VS CONFIGURACIÓN)
// ---------------------------------------------------------------------------
function switchTab(tab) {
    const tabMonitor = document.getElementById("tabContentMonitor");
    const tabEvents = document.getElementById("tabContentEvents");
    const tabConfig = document.getElementById("tabContentConfig");
    const btnMonitor = document.getElementById("tabBtnMonitor");
    const btnEvents = document.getElementById("tabBtnEvents");
    const btnConfig = document.getElementById("tabBtnConfig");

    tabMonitor.classList.add("hidden");
    if (tabEvents) tabEvents.classList.add("hidden");
    tabConfig.classList.add("hidden");

    btnMonitor.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 text-gray-300 hover:text-white hover:bg-dark-600/50";
    if (btnEvents) btnEvents.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 text-gray-300 hover:text-white hover:bg-dark-600/50";
    btnConfig.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 text-gray-300 hover:text-white hover:bg-dark-600/50";

    if (tab === "monitor") {
        tabMonitor.classList.remove("hidden");
        btnMonitor.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 bg-blue-600 text-white shadow";
    } else if (tab === "events") {
        if (tabEvents) tabEvents.classList.remove("hidden");
        if (btnEvents) btnEvents.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 bg-blue-600 text-white shadow";
        loadEventsGallery();
    } else {
        tabConfig.classList.remove("hidden");
        btnConfig.className = "px-3.5 py-1.5 rounded-md font-medium transition flex items-center gap-1.5 bg-blue-600 text-white shadow";
        loadConfig(); // Recargar datos frescos
    }
}

// ---------------------------------------------------------------------------
// 3. CONTROL DE VIDEO STREAMING
// ---------------------------------------------------------------------------
function changeStream(streamId) {
    const img = document.getElementById("liveStream");
    if (streamId === "grid") {
        img.src = "/api/stream/grid";
    } else {
        img.src = `/api/stream/${streamId}`;
        activeCameraId = streamId;
    }
}

function toggleFullscreen() {
    const img = document.getElementById("liveStream");
    if (!document.fullscreenElement) {
        img.requestFullscreen().catch(err => {
            console.error(`Error al entrar a pantalla completa: ${err.message}`);
        });
    } else {
        document.exitFullscreen();
    }
}

// ---------------------------------------------------------------------------
// 4. CONTROL MOTORIZADO PTZ (D-PAD Y TECLADO)
// ---------------------------------------------------------------------------
function updateManualSpeed(val) {
    manualSpeed = parseFloat(val);
    document.getElementById("manualSpeedLabel").innerText = manualSpeed.toFixed(2);
}

async function sendPTZ(panFactor, tiltFactor) {
    isMoving = true;
    const pan = panFactor * (manualSpeed / 0.45);
    const tilt = tiltFactor * (manualSpeed / 0.45);

    try {
        await fetch(`/api/ptz/${activeCameraId}/move`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pan: pan, tilt: tilt, zoom: 0.0 })
        });
    } catch (err) {
        console.error("Error al enviar comando PTZ:", err);
    }
}

async function stopPTZ() {
    if (!isMoving) return;
    isMoving = false;
    try {
        await fetch(`/api/ptz/${activeCameraId}/stop`, { method: "POST" });
    } catch (err) {
        console.error("Error al detener PTZ:", err);
    }
}

async function togglePatrol() {
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/patrol`, { method: "POST" });
        const data = await res.json();
        showToast(data.is_patrolling ? "Patrullaje activado" : "Patrullaje pausado", "info");
    } catch (err) {
        showToast("Error al alternar patrulla", "error");
    }
}

async function toggleTrackingModular() {
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/tracking`, { method: "POST" });
        const data = await res.json();
        const active = data.tracking_enabled;
        showToast(active ? "Seguimiento IA ACTIVADO" : "Seguimiento IA DESACTIVADO", active ? "success" : "info");
    } catch (err) {
        showToast("Error al conmutar seguimiento", "error");
    }
}

async function togglePatrolModular() {
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/patrol_mode`, { method: "POST" });
        const data = await res.json();
        const active = data.patrol_enabled;
        showToast(active ? "Modo Patrullaje ACTIVADO" : "Modo Patrullaje DESACTIVADO", active ? "success" : "info");
    } catch (err) {
        showToast("Error al conmutar patrullaje", "error");
    }
}

async function saveHomePosition() {
    if (!confirm("¿Deseas fijar la posición actual de la cámara como Home (Preset 1) en su memoria física?")) {
        return;
    }
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/set_home`, { method: "POST" });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
    } catch (err) {
        showToast("Error al guardar posición Home en la cámara", "error");
    }
}

async function quickGotoPreset(presetId) {
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/goto_preset?preset_id=${presetId}`, { method: "POST" });
        const data = await res.json();
        showToast(`Moviendo a Preset ${presetId}...`, "info");
    } catch (err) {
        showToast(`Error al mover a Preset ${presetId}`, "error");
    }
}

async function quickSavePreset(presetId) {
    if (!confirm(`¿Fijar la posición actual como Preset ${presetId} en la memoria física de la cámara?`)) {
        return;
    }
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/set_preset?preset_id=${presetId}&name=Preset+${presetId}`, { method: "POST" });
        const data = await res.json();
        showToast(data.message, data.success ? "success" : "error");
    } catch (err) {
        showToast(`Error al guardar Preset ${presetId}`, "error");
    }
}


async function triggerCalibration() {
    if (!confirm("¿Deseas iniciar la calibración / retorno a Home?")) {
        return;
    }
    try {
        const res = await fetch(`/api/ptz/${activeCameraId}/calibrate`, { method: "POST" });
        const data = await res.json();
        showToast(data.message, "success");
    } catch (err) {
        showToast("Error al iniciar calibración", "error");
    }
}


function setupKeyboardControls() {
    const activeKeys = new Set();

    window.addEventListener("keydown", (e) => {
        // Ignorar si el usuario está escribiendo en un input
        if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;

        if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
            e.preventDefault();
            if (activeKeys.has(e.key)) return;
            activeKeys.add(e.key);

            let pan = 0, tilt = 0;
            if (activeKeys.has("ArrowUp")) tilt += 0.45;
            if (activeKeys.has("ArrowDown")) tilt -= 0.45;
            if (activeKeys.has("ArrowLeft")) pan -= 0.45;
            if (activeKeys.has("ArrowRight")) pan += 0.45;

            sendPTZ(pan, tilt);
        }
    });

    window.addEventListener("keyup", (e) => {
        if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
            activeKeys.delete(e.key);
            if (activeKeys.size === 0) {
                stopPTZ();
            }
        }
    });
}

// ---------------------------------------------------------------------------
// 5. WEBSOCKET DE TELEMETRÍA EN VIVO
// ---------------------------------------------------------------------------
function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;

    telemetrySocket = new WebSocket(wsUrl);

    telemetrySocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        } catch (err) {
            console.error("Error parseando telemetría:", err);
        }
    };

    telemetrySocket.onclose = () => {
        // Reconectar en 2 segundos si se cierra la conexión
        setTimeout(initWebSocket, 2000);
    };

    telemetrySocket.onerror = (err) => {
        console.warn("WebSocket de telemetría desconectado. Reintentando...");
    };
}

function updateDashboard(data) {
    if (!data.cameras || data.cameras.length === 0) return;

    // Calcular FPS promedio y total de detecciones
    let totalDets = 0;
    let avgFps = 0;
    let activeDetectionsList = [];

    data.cameras.forEach(cam => {
        avgFps += cam.fps;
        totalDets += cam.detections_count;

        if (cam.detections && cam.detections.length > 0) {
            cam.detections.forEach(d => {
                activeDetectionsList.push({
                    ...d,
                    cameraName: cam.name
                });
            });
        }

        // Si es la cámara activa seleccionada, actualizar el badge de estado PTZ y controles modulares
        if (cam.cam_id === activeCameraId || (activeCameraId === "grid" && cam.ptz.has_hardware_ptz)) {
            const ptzBadge = document.getElementById("ptzStatusBadge");
            if (ptzBadge) {
                ptzBadge.innerText = cam.ptz.status;
                if (cam.ptz.is_patrolling) {
                    ptzBadge.className = "text-[10px] px-2 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/40 animate-pulse";
                } else if (cam.ptz.is_homing) {
                    ptzBadge.className = "text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse";
                } else if (cam.ptz.status.includes("TRACKING")) {
                    ptzBadge.className = "text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold";
                } else {
                    ptzBadge.className = "text-[10px] px-2 py-0.5 rounded bg-gray-700 text-gray-300 border border-gray-600";
                }
            }

            // Actualizar botones de los módulos Tracking y Patrullaje
            const btnTracking = document.getElementById("btnTrackingToggle");
            if (btnTracking && cam.ptz.tracking_enabled !== undefined) {
                if (cam.ptz.tracking_enabled) {
                    btnTracking.className = "px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1.5 transition bg-emerald-600/20 text-emerald-300 border-emerald-500/30";
                    btnTracking.innerHTML = `<i data-lucide="crosshair" class="w-3.5 h-3.5"></i> Seguimiento ON`;
                } else {
                    btnTracking.className = "px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1.5 transition bg-dark-700 text-gray-400 border-dark-600";
                    btnTracking.innerHTML = `<i data-lucide="crosshair" class="w-3.5 h-3.5"></i> Seguimiento OFF`;
                }
            }

            const btnPatrol = document.getElementById("btnPatrolToggle");
            if (btnPatrol && cam.ptz.patrol_enabled !== undefined) {
                if (cam.ptz.patrol_enabled) {
                    btnPatrol.className = "px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1.5 transition bg-blue-600/20 text-blue-300 border-blue-500/30";
                    btnPatrol.innerHTML = `<i data-lucide="scan" class="w-3.5 h-3.5"></i> Patrullaje ON`;
                } else {
                    btnPatrol.className = "px-2.5 py-1.5 rounded-lg border text-xs font-semibold flex items-center justify-center gap-1.5 transition bg-dark-700 text-gray-400 border-dark-600";
                    btnPatrol.innerHTML = `<i data-lucide="scan" class="w-3.5 h-3.5"></i> Patrullaje OFF`;
                }
            }

            // Coordenadas nativas de hardware
            const coordsBox = document.getElementById("nativeCoordsBox");
            const coordsText = document.getElementById("nativeCoordsText");
            if (coordsBox && coordsText) {
                if (cam.ptz.native_status && cam.ptz.native_status.supported) {
                    coordsBox.classList.remove("hidden");
                    const pVal = cam.ptz.native_status.pan !== null ? Number(cam.ptz.native_status.pan).toFixed(1) : "--";
                    const tVal = cam.ptz.native_status.tilt !== null ? Number(cam.ptz.native_status.tilt).toFixed(1) : "--";
                    coordsText.innerText = `P: ${pVal}° | T: ${tVal}°`;
                } else {
                    coordsBox.classList.add("hidden");
                }
            }

            // Badges de Visión Nocturna y Flash / Luz Blanca
            const spotBadge = document.getElementById("spotlightStatusBadge");
            const nightBadge = document.getElementById("nightVisionBadge");
            if (cam.spotlight) {
                if (spotBadge) {
                    if (cam.spotlight.active) {
                        spotBadge.innerHTML = `<i data-lucide="zap" class="w-2.5 h-2.5"></i> FLASH ON`;
                        spotBadge.className = "text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center gap-1 animate-pulse";
                        spotBadge.classList.remove("hidden");
                    } else if (cam.spotlight.dwelling && cam.spotlight.dwell_remaining > 0) {
                        spotBadge.innerHTML = `<i data-lucide="timer" class="w-2.5 h-2.5"></i> FLASH (${cam.spotlight.dwell_remaining}s)`;
                        spotBadge.className = "text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-200 border border-amber-500/30 flex items-center gap-1";
                        spotBadge.classList.remove("hidden");
                    } else {
                        spotBadge.classList.add("hidden");
                    }
                }
                if (nightBadge) {
                    if (cam.spotlight.is_night) {
                        nightBadge.classList.remove("hidden");
                    } else {
                        nightBadge.classList.add("hidden");
                    }
                }
            }
        }
    });


    avgFps = (avgFps / data.cameras.length).toFixed(1);
    const headerFpsEl = document.getElementById("headerFps");
    if (headerFpsEl) headerFpsEl.innerText = `FPS: ${avgFps}`;
    const headerDetectionsEl = document.getElementById("headerDetections");
    if (headerDetectionsEl) headerDetectionsEl.innerText = `${totalDets} Objetivos`;
    const activeDetectionsCountEl = document.getElementById("activeDetectionsCount");
    if (activeDetectionsCountEl) activeDetectionsCountEl.innerText = totalDets;

    // Renderizar lista de detecciones en vivo
    renderDetectionsList(activeDetectionsList);
}

function renderDetectionsList(detections) {
    const listContainer = document.getElementById("detectionsList");
    if (!listContainer) return;

    if (detections.length === 0) {
        listContainer.innerHTML = `<p class="text-gray-500 italic text-center py-6">Sin objetivos en el encuadre</p>`;
        return;
    }

    listContainer.innerHTML = detections.map(d => {
        const isStatic = d.is_static;
        const statusBadge = isStatic
            ? `<span class="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px]">ESTÁTICO</span>`
            : `<span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-bold animate-pulse">TRACKING</span>`;

        return `
            <div class="bg-dark-700/50 border border-dark-600/50 p-2.5 rounded-lg flex items-center justify-between">
                <div>
                    <div class="flex items-center gap-1.5 font-bold text-gray-200">
                        <span class="w-2 h-2 rounded-full ${isStatic ? 'bg-amber-400' : 'bg-emerald-400'}"></span>
                        ${d.class_name.toUpperCase()} #${d.track_id}
                    </div>
                    <div class="text-[10px] text-gray-400 flex items-center gap-2 mt-0.5">
                        <span>Conf: ${Math.round(d.confidence * 100)}%</span>
                        <span>Vel: ${d.speed_px} px/f</span>
                    </div>
                </div>
                <div>${statusBadge}</div>
            </div>
        `;
    }).join("");
}

// ---------------------------------------------------------------------------
// 6. GESTIÓN Y PERSISTENCIA DE CONFIGURACIÓN
// ---------------------------------------------------------------------------
async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        if (!res.ok) throw new Error("Error cargando configuración");
        currentConfig = await res.json();

        // Rellenar formulario con valores de config.yaml
        const det = currentConfig.detection || {};
        document.getElementById("cfgModelName").value = det.model_name || "yolov8n.pt";
        document.getElementById("cfgConfidence").value = det.confidence || 0.30;
        document.getElementById("cfgConfLabel").innerText = det.confidence || 0.30;

        // Cargar clases de IA marcadas en los checkboxes
        const targetClasses = (det.target_classes || ["person", "car", "motorcycle", "truck", "bus", "dog", "cat"]).map(c => String(c).toLowerCase());
        document.querySelectorAll(".cfg-class-checkbox").forEach(cb => {
            cb.checked = targetClasses.includes(cb.value.toLowerCase());
        });

        // Cargar estado de logs en consola
        if (currentConfig.logging) {
            document.getElementById("cfgQuietLogs").checked = currentConfig.logging.quiet ?? false;
        }

        // Cargar configuración de seguridad
        const sec = currentConfig.security || {};
        const authCb = document.getElementById("cfgAuthEnabled");
        if (authCb) authCb.checked = Boolean(sec.auth_enabled);
        const userInp = document.getElementById("cfgAdminUsername");
        if (userInp) userInp.value = sec.admin_username || "admin";
        const passInp = document.getElementById("cfgAdminPassword");
        if (passInp) passInp.value = sec.admin_password || "";

        renderCamerasList();

    } catch (err) {
        console.error("Error al cargar config:", err);
    }
}

// ---------------------------------------------------------------------------
// 7. GESTIÓN DE CÁMARAS (LISTAR, AGREGAR, EDITAR, ELIMINAR)
// ---------------------------------------------------------------------------
function renderCamerasList() {
    const container = document.getElementById("camerasListContainer");
    if (!container || !currentConfig) return;

    const cameras = currentConfig.cameras || [];
    if (cameras.length === 0) {
        container.innerHTML = `<p class="col-span-full text-gray-500 italic text-center py-6">No hay cámaras configuradas en el sistema.</p>`;
        return;
    }

    container.innerHTML = cameras.map((cam, idx) => {
        const hasPtz = cam.ptz && cam.ptz.enabled !== false;
        const ptzLabel = hasPtz
            ? `<span class="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30 text-[10px] font-semibold">PTZ (${cam.ptz.driver || 'ONVIF'}:${cam.ptz.onvif_port || 57858})</span>`
            : `<span class="px-2 py-0.5 rounded bg-gray-700 text-gray-400 border border-gray-600 text-[10px]">Cámara Fija</span>`;

        const hasSpotlight = cam.spotlight && cam.spotlight.enabled;
        const spotlightBadge = hasSpotlight
            ? `<span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] font-semibold flex items-center gap-1"><i data-lucide="zap" class="w-2.5 h-2.5"></i> Flash</span>`
            : '';

        return `
            <div class="bg-dark-900/70 border border-dark-700/80 rounded-xl p-4 flex flex-col justify-between shadow-lg">
                <div>
                    <div class="flex items-start justify-between gap-2 mb-2">
                        <div>
                            <h4 class="font-bold text-sm text-gray-100 flex items-center gap-1.5">
                                <i data-lucide="video" class="w-4 h-4 text-blue-400"></i> ${cam.name}
                            </h4>
                            <span class="text-[10px] font-mono text-gray-500">ID: ${cam.id}</span>
                        </div>
                        <div class="flex items-center gap-1">${ptzLabel} ${spotlightBadge}</div>
                    </div>

                    <div class="space-y-1 text-xs text-gray-400 pt-2 border-t border-dark-800">
                        <div class="flex justify-between">
                            <span>Origen:</span>
                            <span class="font-mono text-gray-300">${cam.camera_ip || 'Local'}:${cam.rtsp_port || 554}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>Usuario:</span>
                            <span class="font-mono text-gray-300">${cam.username || 'admin'}</span>
                        </div>
                        <div class="flex justify-between">
                            <span>Canal / Stream:</span>
                            <span class="font-mono text-gray-300">Ch ${cam.channel || 1} (${cam.subtype === 0 ? 'Main' : 'Sub'}) | Skip: ${cam.frame_skip || 1}x</span>
                        </div>
                        ${hasSpotlight ? `
                        <div class="flex justify-between text-[11px] text-amber-300/90 pt-1 border-t border-dark-800/80">
                            <span>Flash Óptico:</span>
                            <span class="font-mono text-[10px]">${cam.spotlight.night_only !== false ? 'Solo Noche' : 'Día y Noche'} (${cam.spotlight.cooldown_sec || 8}s)</span>
                        </div>
                        ` : ''}
                        ${hasPtz && cam.ptz ? `
                        <div class="flex justify-between text-[11px] text-amber-400/90 pt-1 border-t border-dark-800/80">
                            <span>Barrido / Inclinación:</span>
                            <span class="font-mono">${cam.ptz.sweep_pan_time_sec ?? 14}s pan | ${cam.ptz.tilt_transition_time_sec ?? 3}s tilt</span>
                        </div>
                        <div class="flex justify-between text-[11px] text-amber-400/90">
                            <span>Timeout Inmóvil:</span>
                            <span class="font-mono">${cam.ptz.static_target_timeout_sec ?? 3}s (Pausa web: ${cam.ptz.manual_override_pause_sec ?? 1}s)</span>
                        </div>
                        <div class="flex justify-between text-[11px] pt-1 border-t border-dark-800/80">
                            <span>Módulos:</span>
                            <span class="font-mono text-[10px]">
                                <span class="${cam.ptz.tracking_enabled !== false ? 'text-emerald-400' : 'text-gray-500'}">Seguimiento: ${cam.ptz.tracking_enabled !== false ? 'ON' : 'OFF'}</span> | 
                                <span class="${cam.ptz.patrol_enabled !== false ? 'text-blue-400' : 'text-gray-500'}">Patrulla: ${cam.ptz.patrol_enabled !== false ? 'ON' : 'OFF'}</span>
                            </span>
                        </div>
                        <div class="flex justify-between text-[11px]">
                            <span>Estrategia:</span>
                            <span class="font-mono text-cyan-300 text-[10px]">${cam.ptz.patrol_type === 'preset_tour' ? 'Ronda Presets (' + ((cam.ptz.preset_tour && cam.ptz.preset_tour.length) || 0) + ' paradas)' : 'Barrido Panorámico 355°'}</span>
                        </div>
                        <div class="flex justify-between text-[11px]">
                            <span>Calibración:</span>
                            <span class="font-mono text-purple-300 text-[10px]">${cam.ptz.positioning_mode === 'time_based' ? 'Temporizado (Seg)' : 'Nativo Preset (' + (cam.ptz.home_preset || '1') + ')'}</span>
                        </div>
                        ` : ''}
                    </div>
                </div>

                <div class="flex items-center justify-end gap-2 mt-4 pt-3 border-t border-dark-700/60">
                    <button onclick="openCameraModal(${idx})" class="px-2.5 py-1 bg-dark-700 hover:bg-dark-600 text-gray-200 rounded text-xs font-medium flex items-center gap-1 transition">
                        <i data-lucide="pencil" class="w-3 h-3"></i> Editar
                    </button>
                    <button onclick="deleteCamera(${idx})" class="px-2.5 py-1 bg-red-600/20 hover:bg-red-600/30 text-red-300 border border-red-500/30 rounded text-xs font-medium flex items-center gap-1 transition">
                        <i data-lucide="trash-2" class="w-3 h-3"></i> Eliminar
                    </button>
                </div>
            </div>
        `;
    }).join("");

    if (window.lucide) {
        lucide.createIcons();
    }
}

function openCameraModal(index = -1) {
    const modal = document.getElementById("cameraModal");
    const title = document.getElementById("modalCameraTitle");
    document.getElementById("modalCamIndex").value = index;

    if (index >= 0 && currentConfig && currentConfig.cameras && currentConfig.cameras[index]) {
        const cam = currentConfig.cameras[index];
        title.innerHTML = `<i data-lucide="pencil" class="w-4 h-4 text-blue-400"></i> Editar Cámara: ${cam.name}`;
        document.getElementById("modalCamId").value = cam.id || "";
        document.getElementById("modalCamName").value = cam.name || "";
        document.getElementById("modalCamIp").value = cam.camera_ip || "";
        document.getElementById("modalCamRtspPort").value = cam.rtsp_port || 554;
        document.getElementById("modalCamUser").value = cam.username || "admin";
        document.getElementById("modalCamPass").value = cam.password || "";
        document.getElementById("modalCamChannel").value = cam.channel ?? 1;
        document.getElementById("modalCamSubtype").value = cam.subtype ?? 1;
        document.getElementById("modalCamFrameSkip").value = cam.frame_skip ?? 1;

        const hasPtz = cam.ptz && cam.ptz.enabled !== false;
        document.getElementById("modalCamPtzEnabled").checked = hasPtz;
        togglePtzFields(hasPtz);
        if (hasPtz && cam.ptz) {
            document.getElementById("modalCamPtzDriver").value = cam.ptz.driver || "onvif";
            document.getElementById("modalCamOnvifPort").value = cam.ptz.onvif_port || 57858;
            document.getElementById("modalCamSweepTime").value = cam.ptz.sweep_pan_time_sec ?? 14.0;
            document.getElementById("modalCamPatrolSpeed").value = cam.ptz.patrol_speed ?? 0.35;
            document.getElementById("modalCamTiltTime").value = cam.ptz.tilt_transition_time_sec ?? 3.0;
            document.getElementById("modalCamTiltSpeed").value = cam.ptz.patrol_tilt_speed ?? 0.35;
            document.getElementById("modalCamStaticTimeout").value = cam.ptz.static_target_timeout_sec ?? 3.0;
            document.getElementById("modalCamManualPause").value = cam.ptz.manual_override_pause_sec ?? 1.0;
            document.getElementById("modalCamTrackingEnabled").checked = cam.ptz.tracking_enabled ?? true;
            document.getElementById("modalCamPatrolEnabled").checked = cam.ptz.patrol_enabled ?? true;
            document.getElementById("modalCamPosMode").value = cam.ptz.positioning_mode ?? "native_preset";
            document.getElementById("modalCamHomePreset").value = cam.ptz.home_preset ?? "1";
            document.getElementById("modalCamPatrolType").value = cam.ptz.patrol_type || "panoramic";
            togglePatrolTypeFields(cam.ptz.patrol_type || "panoramic");
            currentModalTour = (cam.ptz.preset_tour && cam.ptz.preset_tour.length > 0) 
                ? JSON.parse(JSON.stringify(cam.ptz.preset_tour)) 
                : [{"preset": "1", "dwell_time_sec": 8.0}, {"preset": "2", "dwell_time_sec": 8.0}];
            renderTourSteps(currentModalTour);
            document.getElementById("modalCamInitialHoming").checked = cam.ptz.initial_homing ?? true;
            // Consultar presets en segundo plano únicamente para enriquecer los nombres descriptivos
            populatePresetNames();
        }

        // Cargar orden de prioridad de seguimiento de objetivos
        const rawPriority = cam.tracking_priority || (cam.ptz && cam.ptz.tracking_priority) || ["person", "car", "motorcycle", "dog"];
        currentModalPriority = Array.isArray(rawPriority) ? [...rawPriority] : ["person", "car", "motorcycle", "dog"];
        renderTrackingPriorityList(currentModalPriority);

        // Cargar configuración de Disuasión Activa / Flash Inteligente
        const spot = cam.spotlight || {};
        const spotEnabled = Boolean(spot.enabled);
        document.getElementById("modalCamSpotlightEnabled").checked = spotEnabled;
        toggleSpotlightFields(spotEnabled);
        document.getElementById("modalCamSpotlightCondition").value = (spot.night_only === false) ? "always" : "night_only";
        
        // Cargar objetivos a disuadir modulares en los checkboxes
        const rawSpotTargets = Array.isArray(spot.trigger_classes) ? spot.trigger_classes : ["person"];
        const isAll = rawSpotTargets.includes("all");
        document.querySelectorAll(".spotlight-target-cb").forEach(cb => {
            if (isAll) {
                cb.checked = true;
            } else if (rawSpotTargets.includes("vehicles") && ["car", "motorcycle", "truck", "bus"].includes(cb.value)) {
                cb.checked = true;
            } else if (rawSpotTargets.includes("animals") && ["dog", "cat"].includes(cb.value)) {
                cb.checked = true;
            } else {
                cb.checked = rawSpotTargets.includes(cb.value);
            }
        });

        document.getElementById("modalCamSpotlightDelay").value = spot.trigger_delay_sec ?? 3.0;
        document.getElementById("modalCamSpotlightDuration").value = spot.flash_duration_sec ?? 3.0;
        document.getElementById("modalCamSpotlightOneShot").checked = (spot.one_shot_per_target !== false);
        document.getElementById("modalCamSpotlightCooldown").value = spot.cooldown_sec ?? 8;

        // Auto-Zoom Dinámico & Suavizado PTZ
        const az = (cam.ptz && cam.ptz.auto_zoom) ? cam.ptz.auto_zoom : {};
        document.getElementById("modalCamAutoZoomEnabled").checked = Boolean(az.enabled);
        document.getElementById("modalCamZoomMinRatio").value = az.target_height_ratio_min ?? 0.18;
        document.getElementById("modalCamZoomMaxRatio").value = az.target_height_ratio_max ?? 0.50;
        document.getElementById("modalCamZoomSpeed").value = az.zoom_speed ?? 0.35;

        const sm = (cam.ptz && cam.ptz.smoothing) ? cam.ptz.smoothing : {};
        document.getElementById("modalCamSmoothingEnabled").checked = (sm.enabled !== false);
        document.getElementById("modalCamSmoothingAlpha").value = sm.alpha ?? 0.35;
        document.getElementById("modalCamSmoothingAlphaVal").innerText = (sm.alpha ?? 0.35).toFixed(2);

        // Geocercas y Zonas de Interés
        const geo = cam.geofence || {};
        document.getElementById("modalCamGeofenceEnabled").checked = Boolean(geo.enabled);
        document.getElementById("modalCamGeofenceMode").value = geo.mode || "inclusion";
        currentModalGeofencePoints = Array.isArray(geo.points) ? [...geo.points] : [];
        updateGeofencePointsCounter();

        // Grabación de Evidencia en Alertas (MP4)
        const rec = cam.event_recording || {};
        document.getElementById("modalCamRecEnabled").checked = Boolean(rec.enabled);
        document.getElementById("modalCamRecPreSec").value = rec.pre_alarm_sec ?? 3.0;
        document.getElementById("modalCamRecPostSec").value = rec.post_alarm_sec ?? 5.0;

    } else {
        detectedCameraPresets = [];
        const hintBox = document.getElementById("detectedPresetsHint");
        if (hintBox) hintBox.classList.add("hidden");
        title.innerHTML = `<i data-lucide="plus" class="w-4 h-4 text-emerald-400"></i> Agregar Nueva Cámara`;
        const newId = `cam_${(currentConfig.cameras ? currentConfig.cameras.length + 1 : 1)}`;
        document.getElementById("modalCamId").value = newId;
        document.getElementById("modalCamName").value = `Cámara ${newId}`;
        document.getElementById("modalCamIp").value = "";
        document.getElementById("modalCamRtspPort").value = 554;
        document.getElementById("modalCamUser").value = "admin";
        document.getElementById("modalCamPass").value = "";
        document.getElementById("modalCamChannel").value = 1;
        document.getElementById("modalCamSubtype").value = 1;
        document.getElementById("modalCamFrameSkip").value = 1;
        document.getElementById("modalCamPtzEnabled").checked = false;
        document.getElementById("modalCamPtzDriver").value = "onvif";
        document.getElementById("modalCamOnvifPort").value = 57858;
        document.getElementById("modalCamSweepTime").value = 14.0;
        document.getElementById("modalCamPatrolSpeed").value = 0.35;
        document.getElementById("modalCamTiltTime").value = 3.0;
        document.getElementById("modalCamTiltSpeed").value = 0.35;
        document.getElementById("modalCamStaticTimeout").value = 3.0;
        document.getElementById("modalCamManualPause").value = 1.0;
        document.getElementById("modalCamTrackingEnabled").checked = true;
        document.getElementById("modalCamPatrolEnabled").checked = true;
        document.getElementById("modalCamPosMode").value = "native_preset";
        document.getElementById("modalCamHomePreset").value = "1";
        document.getElementById("modalCamPatrolType").value = "panoramic";
        togglePatrolTypeFields("panoramic");
        currentModalTour = [{"preset": "1", "dwell_time_sec": 8.0}, {"preset": "2", "dwell_time_sec": 8.0}];
        renderTourSteps(currentModalTour);
        document.getElementById("modalCamInitialHoming").checked = true;
        togglePtzFields(false);

        // Prioridad por defecto para cámara nueva
        currentModalPriority = ["person", "car", "motorcycle", "dog"];
        renderTrackingPriorityList(currentModalPriority);

        // Reset campos de Spotlight para cámara nueva
        document.getElementById("modalCamSpotlightEnabled").checked = false;
        toggleSpotlightFields(false);
        document.getElementById("modalCamSpotlightCondition").value = "night_only";
        document.querySelectorAll(".spotlight-target-cb").forEach(cb => {
            cb.checked = (cb.value === "person");
        });
        document.getElementById("modalCamSpotlightDelay").value = 3.0;
        document.getElementById("modalCamSpotlightDuration").value = 3.0;
        document.getElementById("modalCamSpotlightOneShot").checked = true;
        document.getElementById("modalCamSpotlightCooldown").value = 8;

        // Reset Auto-Zoom & Suavizado
        document.getElementById("modalCamAutoZoomEnabled").checked = false;
        document.getElementById("modalCamZoomMinRatio").value = 0.18;
        document.getElementById("modalCamZoomMaxRatio").value = 0.50;
        document.getElementById("modalCamZoomSpeed").value = 0.35;
        document.getElementById("modalCamSmoothingEnabled").checked = true;
        document.getElementById("modalCamSmoothingAlpha").value = 0.35;
        document.getElementById("modalCamSmoothingAlphaVal").innerText = "0.35";

        // Reset Geocerca
        document.getElementById("modalCamGeofenceEnabled").checked = false;
        document.getElementById("modalCamGeofenceMode").value = "inclusion";
        currentModalGeofencePoints = [];
        updateGeofencePointsCounter();

        // Reset Grabación
        document.getElementById("modalCamRecEnabled").checked = false;
        document.getElementById("modalCamRecPreSec").value = 3.0;
        document.getElementById("modalCamRecPostSec").value = 5.0;
    }

    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
}

let currentModalTour = [];
let detectedCameraPresets = [];

function togglePatrolTypeFields(patrolType) {
    const panPanel = document.getElementById("panelPatrolPanoramic");
    const presetsPanel = document.getElementById("panelPatrolPresets");
    if (!panPanel || !presetsPanel) return;

    if (patrolType === "preset_tour") {
        panPanel.classList.add("hidden");
        presetsPanel.classList.remove("hidden");
    } else {
        panPanel.classList.remove("hidden");
        presetsPanel.classList.add("hidden");
    }
}

function getPresetDisplayName(token) {
    const found = detectedCameraPresets.find(p => String(p.token) === String(token));
    if (found && found.name && found.name !== `Preset ${token}`) {
        return found.name;
    }
    return "";
}

let draggedTourIdx = null;

function renderTourSteps(tour) {
    const container = document.getElementById("tourStepsContainer");
    if (!container) return;

    container.innerHTML = tour.map((step, idx) => {
        const customName = getPresetDisplayName(step.preset);
        const nameBadge = customName ? `<span class="text-[10px] text-cyan-400 font-normal truncate max-w-[90px]" title="${customName}">(${customName})</span>` : '';

        return `
        <div class="tour-row flex items-center gap-2 bg-dark-800 hover:bg-dark-750 p-1.5 rounded border border-dark-600 text-xs transition duration-150"
             draggable="true"
             data-tour-idx="${idx}"
             ondragstart="handleTourDragStart(event, ${idx})"
             ondragover="handleTourDragOver(event)"
             ondragenter="handleTourDragEnter(event)"
             ondragleave="handleTourDragLeave(event)"
             ondrop="handleTourDrop(event, ${idx})"
             ondragend="handleTourDragEnd(event)">
            
            <!-- Handle Drag & Drop -->
            <div class="drag-handle text-gray-500 hover:text-gray-300 flex items-center justify-center p-0.5" title="Arrastra para reordenar la secuencia">
                <i data-lucide="grip-vertical" class="w-4 h-4 pointer-events-none"></i>
            </div>

            <span class="text-[10px] font-mono text-gray-500 w-4">${idx + 1}</span>

            <!-- Preset ID + Nombre -->
            <div class="flex items-center gap-1">
                <span class="text-gray-400">Preset:</span>
                <input type="text" value="${step.preset}" onchange="currentModalTour[${idx}].preset = this.value.trim(); renderTourSteps(currentModalTour);" class="w-12 bg-dark-700 border border-dark-500 rounded px-1.5 py-0.5 text-center font-mono font-bold text-blue-300">
                ${nameBadge}
            </div>

            <!-- Dwell Time -->
            <div class="flex items-center gap-1 flex-1 justify-end">
                <span class="text-gray-400">Segundos:</span>
                <input type="number" step="1" min="1" value="${step.dwell_time_sec}" onchange="currentModalTour[${idx}].dwell_time_sec = parseFloat(this.value) || 5.0" class="w-16 bg-dark-700 border border-dark-500 rounded px-1.5 py-0.5 text-center font-mono font-bold text-amber-300">
                <span class="text-gray-500 text-[10px]">s</span>
            </div>

            <!-- Botón Eliminar -->
            <button type="button" onclick="removeTourStepRow(${idx})" class="p-1 text-red-400 hover:text-red-300 hover:bg-red-500/20 rounded transition" title="Eliminar parada">
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
        </div>
        `;
    }).join("");

    if (window.lucide) lucide.createIcons();
}

function handleTourDragStart(e, idx) {
    draggedTourIdx = idx;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", idx);
    const row = e.target.closest(".tour-row");
    if (row) {
        setTimeout(() => row.classList.add("dragging"), 0);
    }
}

function handleTourDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
}

function handleTourDragEnter(e) {
    const row = e.target.closest(".tour-row");
    if (row && row.getAttribute("data-tour-idx") != draggedTourIdx) {
        row.classList.add("drag-over");
    }
}

function handleTourDragLeave(e) {
    const row = e.target.closest(".tour-row");
    if (row) {
        row.classList.remove("drag-over");
    }
}

function handleTourDrop(e, targetIdx) {
    e.preventDefault();
    const row = e.target.closest(".tour-row");
    if (row) {
        row.classList.remove("drag-over");
    }

    if (draggedTourIdx === null || draggedTourIdx === targetIdx) {
        return;
    }

    // Reordenar array currentModalTour
    const movedItem = currentModalTour.splice(draggedTourIdx, 1)[0];
    currentModalTour.splice(targetIdx, 0, movedItem);

    draggedTourIdx = null;
    renderTourSteps(currentModalTour);
}

function handleTourDragEnd(e) {
    const row = e.target.closest(".tour-row");
    if (row) {
        row.classList.remove("dragging");
    }
    document.querySelectorAll(".tour-row").forEach(r => r.classList.remove("drag-over", "dragging"));
    draggedTourIdx = null;
}

function addTourStepRow(presetVal = null) {
    const nextPreset = presetVal !== null ? presetVal.toString() : (currentModalTour.length + 1).toString();
    currentModalTour.push({ preset: nextPreset, dwell_time_sec: 8.0 });
    renderTourSteps(currentModalTour);
}

function removeTourStepRow(idx) {
    if (currentModalTour.length <= 1) {
        showToast("La ronda debe tener al menos 1 parada.", "warning");
        return;
    }
    currentModalTour.splice(idx, 1);
    renderTourSteps(currentModalTour);
}

async function populatePresetNames() {
    const camId = document.getElementById("modalCamId").value.trim();
    if (!camId) return;

    try {
        const res = await fetch(`/api/ptz/${camId}/presets`);
        const data = await res.json();
        if (data.success && data.presets && data.presets.length > 0) {
            detectedCameraPresets = data.presets;
            const hintBox = document.getElementById("detectedPresetsHint");
            const badgesSpan = document.getElementById("detectedPresetsBadges");
            const listStr = data.presets.map(p => {
                const isGeneric = !p.name || p.name === `Preset ${p.token}`;
                return isGeneric ? `P${p.token}` : `P${p.token} (${p.name})`;
            }).join(", ");

            if (hintBox && badgesSpan) {
                hintBox.classList.remove("hidden");
                badgesSpan.innerText = listStr;
            }
            // Solo refrescamos los nombres visuales en las filas existentes SIN tocar el orden ni los segundos
            renderTourSteps(currentModalTour);
        }
    } catch (err) {
        console.debug("No se pudieron cargar nombres de presets en segundo plano:", err);
    }
}

async function fetchCameraPresets() {
    const camId = document.getElementById("modalCamId").value.trim();
    if (!camId) {
        showToast("Por favor ingresa primero el ID de la cámara.", "warning");
        return;
    }

    if (currentModalTour.length > 0) {
        if (!confirm("¿Deseas reemplazar tu secuencia y tiempos actuales con los presets detectados en la cámara?")) {
            return;
        }
    }

    try {
        showToast("Consultando presets físicos de la cámara...", "info");
        const res = await fetch(`/api/ptz/${camId}/presets`);
        const data = await res.json();

        const hintBox = document.getElementById("detectedPresetsHint");
        const badgesSpan = document.getElementById("detectedPresetsBadges");

        if (data.success && data.presets && data.presets.length > 0) {
            detectedCameraPresets = data.presets;
            const listStr = data.presets.map(p => {
                const isGeneric = !p.name || p.name === `Preset ${p.token}`;
                return isGeneric ? `P${p.token}` : `P${p.token} (${p.name})`;
            }).join(", ");

            if (hintBox && badgesSpan) {
                hintBox.classList.remove("hidden");
                badgesSpan.innerText = listStr;
            }

            // Sincronizar ronda con los presets reales encontrados en la cámara
            currentModalTour = data.presets.map(p => ({
                preset: String(p.token),
                dwell_time_sec: 8.0
            }));
            renderTourSteps(currentModalTour);
            showToast(`¡Se cargaron ${data.presets.length} presets reales de la cámara!`, "success");
        } else {
            detectedCameraPresets = [];
            if (hintBox) hintBox.classList.add("hidden");
            showToast("La cámara respondió pero no tiene presets guardados aún.", "info");
        }
    } catch (err) {
        console.error("Error al consultar presets de la cámara:", err);
        showToast("No se pudo conectar a la cámara para leer sus presets.", "error");
    }
}

async function autoDetectCameraSN() {

    const ip = document.getElementById("modalCamIp").value.trim();
    if (!ip) {
        showToast("Por favor ingresa primero la IP de la cámara para consultar su SN.", "warning");
        return;
    }

    const port = parseInt(document.getElementById("modalCamOnvifPort").value) || 80;
    const user = document.getElementById("modalCamUser").value.trim();
    const pass = document.getElementById("modalCamPass").value;
    const driver = document.getElementById("modalCamPtzDriver").value || "onvif";
    const channel = parseInt(document.getElementById("modalCamChannel").value) || 1;

    try {
        showToast("Conectando a la cámara para leer su Número de Serie (SN)...", "info");
        const res = await fetch("/api/camera/detect_sn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                camera_ip: ip,
                port: port,
                username: user,
                password: pass,
                driver: driver,
                channel: channel
            })
        });

        const data = await res.json();
        if (data.success && data.serial_number) {
            document.getElementById("modalCamId").value = data.serial_number;
            showToast(`¡Número de Serie (SN) detectado y asignado como ID!: ${data.serial_number}`, "success");
        } else if (data.fallback_id) {
            document.getElementById("modalCamId").value = data.fallback_id;
            showToast(`La cámara no expuso el SN. Se asignó ID sugerido: ${data.fallback_id}`, "info");
        } else {
            showToast(data.message || "No se pudo obtener el SN de la cámara.", "error");
        }
    } catch (err) {
        console.error("Error detectando SN de la cámara:", err);
        showToast("Error de conexión al consultar el SN de la cámara.", "error");
    }
}



function closeCameraModal() {
    const modal = document.getElementById("cameraModal");
    if (modal) modal.classList.add("hidden");
}

function togglePtzFields(enabled) {
    const fields = document.getElementById("modalPtzFields");
    if (fields) {
        if (enabled) fields.classList.remove("hidden");
        else fields.classList.add("hidden");
    }
}

// -------------------------------------------------------------------------
// GESTOR DE PRIORIDAD DE SEGUIMIENTO (PTZ)
// -------------------------------------------------------------------------

const AVAILABLE_TRACKING_CLASSES = [
    { id: "person", label: "Personas" },
    { id: "car", label: "Autos / Vehículos" },
    { id: "motorcycle", label: "Motos" },
    { id: "truck", label: "Camiones" },
    { id: "bus", label: "Colectivos / Buses" },
    { id: "dog", label: "Perros" },
    { id: "cat", label: "Gatos" }
];
let currentModalPriority = [];

function renderTrackingPriorityList(priorityList) {
    const container = document.getElementById("modalTrackingPriorityList");
    if (!container) return;

    const activeIds = (priorityList || []).map(c => String(c).toLowerCase());

    // Crear lista ordenada completa: primero las clases activas en orden de prioridad, luego las inactivas
    const ordered = [];
    activeIds.forEach(id => {
        const item = AVAILABLE_TRACKING_CLASSES.find(c => c.id === id) || { id, label: id };
        ordered.push({ ...item, active: true });
    });
    AVAILABLE_TRACKING_CLASSES.forEach(item => {
        if (!activeIds.includes(item.id)) {
            ordered.push({ ...item, active: false });
        }
    });

    container.innerHTML = "";
    ordered.forEach((cls) => {
        const row = document.createElement("div");
        row.className = `flex items-center justify-between p-1.5 rounded border text-xs ${cls.active ? 'bg-dark-800 border-dark-600' : 'bg-dark-900/40 border-dark-800 opacity-60'}`;
        const rank = activeIds.indexOf(cls.id);

        row.innerHTML = `
            <div class="flex items-center gap-2">
                <input type="checkbox" ${cls.active ? 'checked' : ''} onchange="togglePriorityClass('${cls.id}', this.checked)" class="accent-emerald-500 rounded cursor-pointer">
                <span class="font-bold text-[10px] w-5 text-center ${cls.active ? 'text-emerald-400 font-mono' : 'text-gray-500'}">${cls.active ? (rank + 1) + 'º' : '-'}</span>
                <span class="text-gray-200 font-medium">${cls.label}</span>
                <span class="text-[10px] text-gray-500 font-mono">(${cls.id})</span>
            </div>
            <div class="flex items-center gap-1">
                <button type="button" onclick="movePriorityClass('${cls.id}', -1)" ${(!cls.active || rank === 0) ? 'disabled' : ''} class="px-2 py-0.5 rounded bg-dark-700 hover:bg-dark-600 disabled:opacity-20 disabled:cursor-not-allowed text-gray-200 text-[10px] font-bold" title="Subir prioridad">▲</button>
                <button type="button" onclick="movePriorityClass('${cls.id}', 1)" ${(!cls.active || rank === activeIds.length - 1) ? 'disabled' : ''} class="px-2 py-0.5 rounded bg-dark-700 hover:bg-dark-600 disabled:opacity-20 disabled:cursor-not-allowed text-gray-200 text-[10px] font-bold" title="Bajar prioridad">▼</button>
            </div>
        `;
        container.appendChild(row);
    });

    if (window.lucide) lucide.createIcons();
}

function togglePriorityClass(classId, isChecked) {
    if (isChecked) {
        if (!currentModalPriority.includes(classId)) {
            currentModalPriority.push(classId);
        }
    } else {
        currentModalPriority = currentModalPriority.filter(c => c !== classId);
    }
    renderTrackingPriorityList(currentModalPriority);
}

function movePriorityClass(classId, direction) {
    const idx = currentModalPriority.indexOf(classId);
    if (idx === -1) return;
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= currentModalPriority.length) return;

    const temp = currentModalPriority[idx];
    currentModalPriority[idx] = currentModalPriority[newIdx];
    currentModalPriority[newIdx] = temp;

    renderTrackingPriorityList(currentModalPriority);
}

function toggleSpotlightFields(enabled) {
    const fields = document.getElementById("modalSpotlightFields");
    if (fields) {
        if (enabled) fields.classList.remove("hidden");
        else fields.classList.add("hidden");
    }
}

function selectSpotlightTargetsPreset(preset) {
    document.querySelectorAll(".spotlight-target-cb").forEach(cb => {
        if (preset === "all") {
            cb.checked = true;
        } else if (preset === "none") {
            cb.checked = false;
        } else if (preset === "person") {
            cb.checked = (cb.value === "person");
        } else if (preset === "vehicles") {
            cb.checked = ["person", "car", "motorcycle", "truck", "bus"].includes(cb.value);
        }
    });
}

async function testModalSpotlight() {
    const camId = document.getElementById("modalCamId").value.trim();
    if (!camId) {
        showToast("Ingresa primero el ID de la cámara.", "warning");
        return;
    }
    const btn = document.getElementById("btnTestSpotlight");
    if (btn) btn.disabled = true;
    showToast("Enviando comando óptico de prueba (5s)...", "info");

    try {
        const res = await fetch(`/api/ptz/${camId}/spotlight`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ test: true, duration_sec: 5.0 })
        });
        const data = await res.json();
        if (data.success) {
            showToast("¡Flash óptico encendido por 5 segundos!", "success");
        } else {
            showToast(data.message || "La cámara no respondió al comando de flash.", "warning");
        }
    } catch (err) {
        showToast("Error de conexión al enviar comando de flash.", "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function testActiveCameraSpotlight() {
    const targetId = activeCameraId === "grid" ? (currentConfig.cameras && currentConfig.cameras[0] ? currentConfig.cameras[0].id : null) : activeCameraId;
    if (!targetId) {
        showToast("Selecciona primero una cámara para probar el flash.", "warning");
        return;
    }
    const btn = document.getElementById("btnSpotlightTest");
    if (btn) btn.disabled = true;
    showToast(`Probando flash óptico en ${targetId} (5s)...`, "info");

    try {
        const res = await fetch(`/api/ptz/${targetId}/spotlight`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ test: true, duration_sec: 5.0 })
        });
        const data = await res.json();
        if (data.success) {
            showToast("¡Flash encendido durante 5 segundos!", "success");
        } else {
            showToast(data.message || "La cámara no respondió al comando de flash.", "warning");
        }
    } catch (err) {
        showToast("Error comunicando con la cámara.", "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function saveCameraModal() {
    const index = parseInt(document.getElementById("modalCamIndex").value);
    const camId = document.getElementById("modalCamId").value.trim();
    const camName = document.getElementById("modalCamName").value.trim();
    const camIp = document.getElementById("modalCamIp").value.trim();
    const rtspPort = parseInt(document.getElementById("modalCamRtspPort").value) || 554;
    const user = document.getElementById("modalCamUser").value.trim();
    const pass = document.getElementById("modalCamPass").value;
    const channel = parseInt(document.getElementById("modalCamChannel").value) || 1;
    const subtype = parseInt(document.getElementById("modalCamSubtype").value) || 0;
    const frameSkip = parseInt(document.getElementById("modalCamFrameSkip").value) || 1;
    const hasPtz = document.getElementById("modalCamPtzEnabled").checked;
    const ptzDriver = document.getElementById("modalCamPtzDriver").value;
    const onvifPort = parseInt(document.getElementById("modalCamOnvifPort").value) || 57858;

    if (!camId || !camName || !camIp) {
        showToast("Por favor completa ID, Nombre e IP de la cámara.", "error");
        return;
    }

    if (!currentConfig.cameras) {
        currentConfig.cameras = [];
    }

    const patrolType = document.getElementById("modalCamPatrolType").value;

    // Disuasión Activa / Flash Óptico Modular
    const spotEnabled = document.getElementById("modalCamSpotlightEnabled").checked;
    const spotCond = document.getElementById("modalCamSpotlightCondition").value;
    const spotDelay = parseFloat(document.getElementById("modalCamSpotlightDelay").value);
    const spotDuration = parseFloat(document.getElementById("modalCamSpotlightDuration").value);
    const spotOneShot = document.getElementById("modalCamSpotlightOneShot").checked;
    const spotCooldown = parseFloat(document.getElementById("modalCamSpotlightCooldown").value) || 8.0;

    // Obtener las clases marcadas (modular: varios, algunos o ninguno)
    const selectedTargetClasses = Array.from(document.querySelectorAll(".spotlight-target-cb:checked")).map(cb => cb.value);

    const finalPriority = (currentModalPriority && currentModalPriority.length > 0)
        ? [...currentModalPriority]
        : ["person", "car", "motorcycle", "dog"];

    const newCamData = {
        id: camId,
        name: camName,
        type: "rtsp",
        camera_ip: camIp,
        rtsp_port: rtspPort,
        username: user,
        password: pass,
        channel: channel,
        subtype: subtype,
        frame_skip: frameSkip,
        reconnect_interval_sec: 5,
        tracking_priority: finalPriority,
        spotlight: {
            enabled: spotEnabled,
            night_only: (spotCond === "night_only"),
            trigger_classes: selectedTargetClasses,
            trigger_delay_sec: isNaN(spotDelay) ? 3.0 : spotDelay,
            flash_duration_sec: isNaN(spotDuration) ? 3.0 : spotDuration,
            one_shot_per_target: spotOneShot,
            cooldown_sec: spotCooldown
        },
        ptz: hasPtz ? {
            enabled: "auto",
            driver: ptzDriver,
            onvif_port: onvifPort,
            patrol_interval_sec: 12,
            patrol_type: patrolType,
            preset_tour: (currentModalTour && currentModalTour.length > 0) ? currentModalTour : [{"preset": "1", "dwell_time_sec": 8.0}],
            patrol_speed: parseFloat(document.getElementById("modalCamPatrolSpeed").value) || 0.35,
            panoramic_patrol: true,
            sweep_pan_time_sec: parseFloat(document.getElementById("modalCamSweepTime").value) || 14.0,
            tilt_transition_time_sec: parseFloat(document.getElementById("modalCamTiltTime").value) || 3.0,
            patrol_tilt_speed: parseFloat(document.getElementById("modalCamTiltSpeed").value) || 0.35,
            tracking_speed: 0.65,
            deadzone_x: 0.08,
            deadzone_y: 0.08,
            static_target_timeout_sec: parseFloat(document.getElementById("modalCamStaticTimeout").value) || 3.0,
            manual_override_pause_sec: parseFloat(document.getElementById("modalCamManualPause").value) || 1.0,
            tracking_enabled: document.getElementById("modalCamTrackingEnabled").checked,
            patrol_enabled: document.getElementById("modalCamPatrolEnabled").checked,
            positioning_mode: document.getElementById("modalCamPosMode").value,
            home_preset: document.getElementById("modalCamHomePreset").value.trim() || "1",
            initial_homing: document.getElementById("modalCamInitialHoming").checked,
            tracking_priority: finalPriority,
            auto_zoom: {
                enabled: document.getElementById("modalCamAutoZoomEnabled").checked,
                target_height_ratio_min: parseFloat(document.getElementById("modalCamZoomMinRatio").value) || 0.18,
                target_height_ratio_max: parseFloat(document.getElementById("modalCamZoomMaxRatio").value) || 0.50,
                zoom_speed: parseFloat(document.getElementById("modalCamZoomSpeed").value) || 0.35
            },
            smoothing: {
                enabled: document.getElementById("modalCamSmoothingEnabled").checked,
                alpha: parseFloat(document.getElementById("modalCamSmoothingAlpha").value) || 0.35
            }
        } : {
            enabled: false,
            digital_ptz: false
        },
        geofence: {
            enabled: document.getElementById("modalCamGeofenceEnabled").checked,
            mode: document.getElementById("modalCamGeofenceMode").value,
            points: currentModalGeofencePoints || []
        },
        event_recording: {
            enabled: document.getElementById("modalCamRecEnabled").checked,
            pre_alarm_sec: parseFloat(document.getElementById("modalCamRecPreSec").value) || 3.0,
            post_alarm_sec: parseFloat(document.getElementById("modalCamRecPostSec").value) || 5.0
        }
    };



    if (index >= 0 && index < currentConfig.cameras.length) {
        currentConfig.cameras[index] = newCamData;
    } else {
        currentConfig.cameras.push(newCamData);
    }

    closeCameraModal();
    await saveConfig();
    renderCamerasList();
    showToast(`Cámara '${camName}' guardada exitosamente.`, "success");
}

async function deleteCamera(index) {
    if (!currentConfig || !currentConfig.cameras || !currentConfig.cameras[index]) return;
    const camName = currentConfig.cameras[index].name;

    if (!confirm(`¿Estás seguro de que deseas eliminar la cámara "${camName}"?`)) {
        return;
    }

    currentConfig.cameras.splice(index, 1);
    await saveConfig();
    renderCamerasList();
    showToast(`Cámara '${camName}' eliminada.`, "info");
}

async function saveConfig() {
    if (!currentConfig) return;

    try {
        // 1. Detección
        currentConfig.detection.model_name = document.getElementById("cfgModelName").value;
        currentConfig.detection.confidence = parseFloat(document.getElementById("cfgConfidence").value);
        const classCheckboxes = document.querySelectorAll(".cfg-class-checkbox");
        let activeClasses = [];
        if (classCheckboxes.length > 0) {
            activeClasses = Array.from(document.querySelectorAll(".cfg-class-checkbox:checked")).map(cb => cb.value);
        } else if (Array.isArray(currentConfig.detection.target_classes)) {
            activeClasses = [...currentConfig.detection.target_classes];
        }

        // Incorporar siempre cualquier clase activa en las cámaras configuradas
        (currentConfig.cameras || []).forEach(c => {
            const pri = c.tracking_priority || (c.ptz && c.ptz.tracking_priority);
            if (Array.isArray(pri)) {
                pri.forEach(cls => {
                    if (cls && !activeClasses.includes(cls)) activeClasses.push(cls);
                });
            }
            const spot = c.spotlight && c.spotlight.trigger_classes;
            if (Array.isArray(spot)) {
                spot.forEach(cls => {
                    if (cls && !["all", "vehicles", "animals"].includes(cls) && !activeClasses.includes(cls)) {
                        activeClasses.push(cls);
                    }
                });
            }
        });

        if (activeClasses.length === 0) {
            activeClasses = ["person", "car", "motorcycle", "truck", "bus", "dog", "cat"];
        }
        currentConfig.detection.target_classes = activeClasses;

        // 2. Configuración de Logs en Consola
        if (!currentConfig.logging) {
            currentConfig.logging = {};
        }
        currentConfig.logging.quiet = document.getElementById("cfgQuietLogs").checked;
        currentConfig.logging.level = currentConfig.logging.quiet ? "ERROR" : "INFO";

        // 3. Seguridad del Panel Web
        if (!currentConfig.security) {
            currentConfig.security = {};
        }
        const authCb = document.getElementById("cfgAuthEnabled");
        if (authCb) currentConfig.security.auth_enabled = authCb.checked;
        const userInp = document.getElementById("cfgAdminUsername");
        if (userInp) currentConfig.security.admin_username = userInp.value.trim() || "admin";
        const passInp = document.getElementById("cfgAdminPassword");
        if (passInp && passInp.value) currentConfig.security.admin_password = passInp.value;

        // 4. Enviar a servidor
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ config: currentConfig })
        });

        const data = await res.json();
        if (res.ok) {
            showToast("Configuración guardada y aplicada en caliente.", "success");
        } else {
            showToast(`Error al guardar: ${data.detail}`, "error");
        }

    } catch (err) {
        console.error("Error al guardar:", err);
        showToast("Error de conexión al guardar configuración", "error");
    }
}

// ---------------------------------------------------------------------------
// 7. NOTIFICACIONES TOAST FLOTANTES
// ---------------------------------------------------------------------------
function showToast(message, type = "info") {
    const container = document.getElementById("toastContainer");
    if (!container) return;

    const toast = document.createElement("div");
    const colors = {
        success: "bg-emerald-600 text-white border-emerald-500",
        error: "bg-red-600 text-white border-red-500",
        info: "bg-blue-600 text-white border-blue-500"
    };

    toast.className = `${colors[type] || colors.info} px-4 py-2.5 rounded-lg shadow-xl text-xs font-semibold border flex items-center gap-2 pointer-events-auto transition transform translate-y-2 opacity-0`;
    toast.innerText = message;

    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
    });

    setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ---------------------------------------------------------------------------
// 8. EDITOR INTERACTIVO DE GEOCERCAS (CANVAS NORMALIZADO)
// ---------------------------------------------------------------------------
let currentModalGeofencePoints = [];

function updateGeofencePointsCounter() {
    const counter = document.getElementById("modalCamGeofencePointsInfo");
    if (counter) {
        counter.innerText = `Puntos definidos: ${currentModalGeofencePoints.length}`;
    }
}

function openGeofenceDrawingModal() {
    const camId = document.getElementById("modalCamId").value.trim();
    if (!camId) {
        showToast("Ingresa primero el ID de la cámara.", "warning");
        return;
    }

    const modal = document.getElementById("geofenceModal");
    const bgImg = document.getElementById("geofenceStreamBg");
    const canvas = document.getElementById("geofenceCanvas");

    // Asignar stream en vivo al fondo
    bgImg.src = `/api/stream/${camId}`;

    bgImg.onload = () => {
        canvas.width = bgImg.clientWidth || bgImg.naturalWidth;
        canvas.height = bgImg.clientHeight || bgImg.naturalHeight;
        redrawGeofenceCanvas();
    };

    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();

    // Configurar listener de clics sobre el canvas
    canvas.onclick = (e) => {
        const rect = canvas.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const clickY = e.clientY - rect.top;

        // Normalizar a rango [0.0, 1.0]
        const normX = Math.max(0, Math.min(1, clickX / rect.width));
        const normY = Math.max(0, Math.min(1, clickY / rect.height));

        currentModalGeofencePoints.push({ x: parseFloat(normX.toFixed(4)), y: parseFloat(normY.toFixed(4)) });
        redrawGeofenceCanvas();
    };
}

function redrawGeofenceCanvas() {
    const canvas = document.getElementById("geofenceCanvas");
    const bgImg = document.getElementById("geofenceStreamBg");
    if (!canvas || !bgImg) return;

    const w = canvas.width;
    const h = canvas.height;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    const counter = document.getElementById("geofencePointsCounter");
    if (counter) counter.innerText = `${currentModalGeofencePoints.length} vértices trazados`;

    if (currentModalGeofencePoints.length === 0) return;

    ctx.lineWidth = 2;
    ctx.strokeStyle = "#f97316"; // Naranja
    ctx.fillStyle = "rgba(249, 115, 22, 0.25)";

    ctx.beginPath();
    currentModalGeofencePoints.forEach((pt, i) => {
        const px = pt.x * w;
        const py = pt.y * h;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
    });

    if (currentModalGeofencePoints.length >= 3) {
        ctx.closePath();
        ctx.fill();
    }
    ctx.stroke();

    // Dibujar círculos en cada vértice
    currentModalGeofencePoints.forEach((pt, i) => {
        const px = pt.x * w;
        const py = pt.y * h;
        ctx.beginPath();
        ctx.arc(px, py, i === 0 ? 6 : 4, 0, 2 * Math.PI);
        ctx.fillStyle = (i === 0) ? "#ef4444" : "#f97316";
        ctx.fill();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 1;
        ctx.stroke();
    });
}

function clearGeofencePoints() {
    currentModalGeofencePoints = [];
    redrawGeofenceCanvas();
    updateGeofencePointsCounter();
}

function closeGeofenceModal() {
    const modal = document.getElementById("geofenceModal");
    const bgImg = document.getElementById("geofenceStreamBg");
    if (bgImg) bgImg.src = ""; // Detener stream para liberar RAM
    if (modal) modal.classList.add("hidden");
}

function applyGeofencePoints() {
    if (currentModalGeofencePoints.length > 0 && currentModalGeofencePoints.length < 3) {
        showToast("Un polígono debe tener al menos 3 vértices.", "warning");
        return;
    }
    updateGeofencePointsCounter();
    closeGeofenceModal();
    showToast(`Polígono definido con ${currentModalGeofencePoints.length} vértices.`, "success");
}

// ---------------------------------------------------------------------------
// 9. GALERÍA Y REPRODUCTOR DE CLIPS DE EVIDENCIA (MP4)
// ---------------------------------------------------------------------------
async function loadEventsGallery() {
    const container = document.getElementById("eventsGalleryContainer");
    if (!container) return;

    try {
        const res = await fetch("/api/events");
        const data = await res.json();
        const events = data.events || [];

        if (events.length === 0) {
            container.innerHTML = `<p class="col-span-full text-gray-500 italic text-center py-10">No hay clips de evidencia grabados aún. Los clips se graban al activarse una alerta o luz de advertencia (flash).</p>`;
            return;
        }

        container.innerHTML = events.map(ev => {
            const dateStr = ev.datetime || new Date(ev.timestamp * 1000).toLocaleString();
            const duration = ev.duration_sec ? `${ev.duration_sec}s` : '--';
            const targetCls = (ev.target && ev.target.class_name) ? ev.target.class_name.toUpperCase() : 'ALERTA';

            return `
                <div class="bg-dark-900/80 border border-dark-700/80 rounded-xl p-4 flex flex-col justify-between shadow-lg hover:border-dark-600 transition">
                    <div>
                        <div class="flex items-start justify-between mb-2">
                            <span class="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30 text-[10px] font-bold">
                                ${ev.cam_name || ev.cam_id}
                            </span>
                            <span class="text-[10px] text-gray-500 font-mono">${duration}</span>
                        </div>
                        <h4 class="font-bold text-sm text-gray-100 flex items-center gap-1.5 mb-1">
                            <i data-lucide="alert-triangle" class="w-4 h-4 text-amber-400"></i> ${ev.reason || 'Alerta'} (${targetCls})
                        </h4>
                        <p class="text-[11px] text-gray-400 font-mono">${dateStr}</p>
                    </div>

                    <div class="mt-4 pt-3 border-t border-dark-800 flex items-center justify-between">
                        <button onclick="playEventClip('${ev.filename}', '${ev.cam_name || ev.cam_id}', '${dateStr}', '${duration}')" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i data-lucide="play" class="w-3.5 h-3.5"></i> Reproducir
                        </button>
                        <div class="flex items-center gap-1.5">
                            <a href="/api/events/${ev.filename}" download class="p-1.5 bg-dark-700 hover:bg-dark-600 text-gray-300 rounded-lg text-xs transition" title="Descargar MP4">
                                <i data-lucide="download" class="w-4 h-4"></i>
                            </a>
                            <button onclick="deleteEventClip('${ev.filename}')" class="p-1.5 bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-500/30 rounded-lg text-xs transition" title="Eliminar Clip">
                                <i data-lucide="trash-2" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }).join("");

        if (window.lucide) lucide.createIcons();

    } catch (err) {
        console.error("Error al cargar galería de eventos:", err);
        container.innerHTML = `<p class="col-span-full text-red-400 text-center py-6 text-xs">Error cargando galería de eventos.</p>`;
    }
}

function playEventClip(filename, camName, dateStr, duration) {
    const modal = document.getElementById("eventPlayerModal");
    const video = document.getElementById("eventPlayerVideo");
    const title = document.getElementById("eventPlayerTitle");
    const sub = document.getElementById("eventPlayerSubtitle");
    const dlBtn = document.getElementById("eventPlayerDownloadBtn");
    const details = document.getElementById("eventPlayerDetails");

    title.innerHTML = `<i data-lucide="film" class="w-4 h-4 text-blue-400"></i> ${camName} - Clip de Evidencia`;
    sub.innerText = `${filename} (${dateStr})`;
    details.innerText = `Duración estimada: ${duration}`;
    dlBtn.href = `/api/events/${filename}`;

    video.src = `/api/events/${filename}`;
    modal.classList.remove("hidden");
    if (window.lucide) lucide.createIcons();
}

function closeEventPlayerModal() {
    const modal = document.getElementById("eventPlayerModal");
    const video = document.getElementById("eventPlayerVideo");
    if (video) {
        video.pause();
        video.src = "";
    }
    if (modal) modal.classList.add("hidden");
}

async function deleteEventClip(filename) {
    if (!confirm(`¿Eliminar clip de evidencia ${filename}?`)) return;
    try {
        const res = await fetch(`/api/events/${filename}`, { method: "DELETE" });
        if (res.ok) {
            showToast("Clip de evidencia eliminado.", "info");
            loadEventsGallery();
        } else {
            showToast("No se pudo eliminar el clip.", "error");
        }
    } catch (err) {
        showToast("Error de conexión al eliminar clip.", "error");
    }
}

// ---------------------------------------------------------------------------
// 10. GESTIÓN DE AUTENTICACIÓN Y SEGURIDAD DEL PANEL
// ---------------------------------------------------------------------------
function handleAuthButtonClick() {
    openAuthModal();
}

function openAuthModal() {
    const modal = document.getElementById("authModal");
    if (modal) {
        modal.classList.remove("hidden");
        document.getElementById("loginUsername").focus();
    }
}

function closeAuthModal() {
    const modal = document.getElementById("authModal");
    if (modal) modal.classList.add("hidden");
}

async function submitLogin(e) {
    e.preventDefault();
    const user = document.getElementById("loginUsername").value.trim();
    const pass = document.getElementById("loginPassword").value;

    try {
        const res = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: user, password: pass })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("¡Autenticación exitosa!", "success");
            closeAuthModal();
            setTimeout(() => window.location.reload(), 600);
        } else {
            showToast(data.detail || "Credenciales incorrectas.", "error");
        }
    } catch (err) {
        showToast("Error de conexión con el servidor de autenticación.", "error");
    }
}
