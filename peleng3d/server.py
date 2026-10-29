"""Mac-side camera, local dashboard and authenticated iPhone frame ingestion."""
from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
import ipaddress
import json
import secrets
import socket
import subprocess
import threading
import time

import cv2
import numpy as np
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .detection import green_marker
from .geometry import SIZE, StereoProfile, calibrate_stereo, chessboard_corners, crop_resize, triangulate
from .store import CaptureStore
from .sync import FrameMatcher, TimedFrame

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_ip() -> str:
    """Try to identify LAN IP without contacting the internet."""
    if __import__("sys").platform == "darwin":
        try:
            result = subprocess.run(
                ["ipconfig", "getifaddr", "en0"], capture_output=True, text=True,
                timeout=1, check=False,
            )
            candidate = result.stdout.strip()
            address = ipaddress.ip_address(candidate)
            if address.version == 4 and address.is_private and not address.is_loopback:
                return candidate
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class MacCamera:
    """OpenCV/AVFoundation camera source with a stable local identity."""

    def __init__(self, index: int = 0, on_frame=None):
        self.index = index
        self.source_id = f"uvc:{index}"
        self.label = "MacBook" if index == 0 else f"USB camera {index}"
        self.on_frame = on_frame
        self.running = False
        self.thread: threading.Thread | None = None
        self.error = "Camera not started"
        self.latest_frame: np.ndarray | None = None
        self.latest_time = 0.0
        self.seq = 0
        self.matcher: FrameMatcher[np.ndarray] = FrameMatcher()
        self.fps = 0.0
        self.lock = threading.RLock()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._capture, name="mac-camera", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def _capture(self):
        cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION) if __import__('sys').platform == 'darwin' else cv2.VideoCapture(self.index)
        if not cap.isOpened():
            with self.lock:
                self.error = f"Cannot open Mac camera #{self.index}. Check permission and device index."
            self.running = False
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        count, window_start = 0, time.monotonic()
        try:
            while self.running:
                ok, frame = cap.read()
                stamp = time.monotonic()
                if not ok:
                    with self.lock:
                        self.error = "Mac camera stopped providing frames"
                    time.sleep(0.15)
                    continue
                frame = crop_resize(frame)
                with self.lock:
                    self.seq += 1
                    timed = TimedFrame(self.seq, stamp, frame)
                    self.matcher.add(timed)
                    self.latest_frame = frame
                    self.latest_time = stamp
                    self.error = ""
                    count += 1
                    elapsed = stamp - window_start
                    if elapsed >= 1:
                        self.fps = count / elapsed
                        count, window_start = 0, stamp
                if self.on_frame is not None:
                    self.on_frame(self, timed)
        finally:
            cap.release()


class Engine:
    def __init__(self, data_dir: Path, camera_index: int | list[int] | tuple[int, ...] = 0):
        indexes = [camera_index] if isinstance(camera_index, int) else list(camera_index)
        if not indexes:
            raise ValueError("At least one camera index is required")
        if len(set(indexes)) != len(indexes):
            raise ValueError("Camera indexes must be unique")
        self.camera = MacCamera(indexes[0])
        self.local_cameras = [self.camera] + [MacCamera(index, self._receive_local_frame) for index in indexes[1:]]
        self.local_stereo = len(self.local_cameras) >= 2
        self.active_pair = (self.local_cameras[0], self.local_cameras[1]) if self.local_stereo else None
        self.store = CaptureStore(data_dir)
        self.lock = threading.RLock()
        self.pin = f"{secrets.randbelow(1000000):06d}"
        self.token: str | None = None
        self.monitor_tokens: dict[str, float] = {}
        self.device: str | None = self.local_cameras[1].source_id if self.local_stereo else None
        self.device_mode: str | None = "local-uvc-640x480-v1" if self.local_stereo else None
        self.last_iphone_time = 0.0
        self.last_iphone_seq = -1
        self.bad_pair_attempts: deque[float] = deque()
        self.remote_frame: np.ndarray | None = None
        self.latest_pair: tuple[np.ndarray, np.ndarray, float] | None = None
        self.last_pair_at = 0.0
        self.sync_error_ms: float | None = None
        self.uncertainty_ms: float | None = None
        self.remote_fps = 0.0
        self.remote_frame_window = deque(maxlen=100)
        self.matched_pairs = 0
        self.dropped_pairs = 0
        self.calibration_pairs = len(self.store.load_pairs())
        self.profile: StereoProfile | None = self.store.load_profile()
        self.last_detection: dict | None = None
        self.last_measurement: dict | None = None
        self.path: deque[list[float]] = deque(maxlen=2000)
        self.hsv_lo = (35, 80, 70)
        self.hsv_hi = (90, 255, 255)
        self.current_error = "Waiting for local stereo pair" if self.local_stereo else "Waiting for phone connection"

    def start_sources(self) -> None:
        for source in self.local_cameras:
            source.start()

    def stop_sources(self) -> None:
        for source in self.local_cameras:
            source.stop()

    def _receive_local_frame(self, source: MacCamera, frame: TimedFrame[np.ndarray]) -> None:
        if not self.active_pair or source is not self.active_pair[1]:
            return
        with self.active_pair[0].lock:
            match = self.active_pair[0].matcher.match(frame.timestamp, 0.012)
        if match is None:
            with self.lock:
                self.dropped_pairs += 1
                self.current_error = "No synchronized local camera pair"
            return
        chosen, delta_s = match
        self._process_pair(chosen.value, frame.value, frame.timestamp, delta_s, 0.0)

    def status(self) -> dict:
        now = time.monotonic()
        with self.camera.lock:
            mac_online = self.camera.latest_frame is not None and now - self.camera.latest_time < 2
            mac_error = self.camera.error
            mac_fps = round(self.camera.fps, 1)
        with self.lock:
            phone_online = (self.local_stereo and self.local_cameras[1].latest_frame is not None and
                            now - self.local_cameras[1].latest_time < 2) or now - self.last_iphone_time < 3
            source_states = []
            for source in self.local_cameras:
                with source.lock:
                    source_states.append({"id": source.source_id, "index": source.index, "label": source.label,
                                          "online": source.latest_frame is not None and now-source.latest_time < 2,
                                          "fps": round(source.fps, 1), "error": source.error})
            measurement = self.last_measurement if self.last_measurement and now - self.last_measurement["arrival_monotonic"] < 3 else None
            result = {"mac_online": mac_online, "mac_error": mac_error, "mac_fps": mac_fps,
                    "iphone_online": phone_online, "iphone_fps": round(self.remote_fps, 1) if phone_online else 0,
                    "device_id": self.device if phone_online else None,
                    "calibration_pairs": self.calibration_pairs,
                    "calibrated": self._profile_matches(),
                    "calibration_rms_px": round(self.profile.rms, 3) if self._profile_matches() else None,
                    "baseline_m": round(float(np.linalg.norm(self.profile.T)), 3) if self._profile_matches() else None,
                    "sync_error_ms": round(self.sync_error_ms, 1) if phone_online and self.sync_error_ms is not None else None,
                    "clock_uncertainty_ms": round(self.uncertainty_ms, 1) if phone_online and self.uncertainty_ms is not None else None,
                    "matched_pairs": self.matched_pairs, "dropped_pairs": self.dropped_pairs,
                    "recording": self.store.active, "samples_recorded": len(self.store.samples),
                    "session_id": self.store.session_id if self.store.active else None,
                    "measurement": {k: v for k,v in measurement.items() if k != "arrival_monotonic"} if measurement else None,
                    "path": list(self.path)[-180:],
                    "error": self.current_error,
                    "marker_hsv_lower": list(self.hsv_lo), "marker_hsv_upper": list(self.hsv_hi),
                    "iphone_mode": self.device_mode if phone_online else None}
            result["sources"] = source_states
            result["capture_mode"] = "local-multicamera" if self.local_stereo else "mac-iphone"
            result["active_pair"] = [s.source_id for s in self.active_pair] if self.active_pair else [self.camera.source_id, "iphone"]
            if self.local_stereo:
                result["iphone_fps"] = source_states[1]["fps"] if source_states[1]["online"] else 0
                result["device_id"] = self.device
                result["clock_uncertainty_ms"] = 0.0 if phone_online else None
            return result

    def _profile_matches(self) -> bool:
        return (self.profile is not None and self.profile.size == SIZE and
                self.profile.mac_camera_index == self.camera.index and
                self.profile.iphone_device_id == self.device and
                self.profile.iphone_camera_mode == self.device_mode)

    def pair(self, pin: str, device_id: str, mode: str) -> str:
        if self.local_stereo:
            raise HTTPException(409, "Local camera pair is active; use monitor pairing on iPhone")
        now = time.monotonic()
        with self.lock:
            while self.bad_pair_attempts and self.bad_pair_attempts[0] < now - 60:
                self.bad_pair_attempts.popleft()
            if len(self.bad_pair_attempts) >= 8:
                raise HTTPException(429, "Too many failed pairing attempts; retry in one minute")
            if not secrets.compare_digest(pin, self.pin):
                self.bad_pair_attempts.append(now)
                raise HTTPException(403, "Pairing code incorrect")
            if self.device is not None and self.device != device_id and now - self.last_iphone_time < 15:
                raise HTTPException(409, "Another iPhone is connected. Stop it first")
            self.device, self.device_mode = device_id, mode
            self.token = secrets.token_urlsafe(32)
            self.last_iphone_seq = -1
            self.last_iphone_time = now
            self.remote_frame = None
            self.latest_pair = None
            self.last_measurement = None
            self.path.clear()
            self.current_error = "Connected. Waiting for first video frame."
            return self.token

    def receive(self, body: bytes, *, token: str, device_id: str, mode: str, seq: int,
                capture_mono: float, clock_offset: float, uncertainty: float) -> None:
        if not 100 < len(body) <= 600_000:
            raise HTTPException(413, "JPEG must be smaller than 600 KB")
        arrival = time.monotonic()
        with self.lock:
            if self.token is None or not secrets.compare_digest(token, self.token):
                raise HTTPException(401, "Invalid session token")
            if device_id != self.device or mode != self.device_mode:
                raise HTTPException(409, "Camera mode/device changed; re-pair and recalibrate")
            if seq <= self.last_iphone_seq:
                raise HTTPException(409, "Out-of-order frame ID")
            self.last_iphone_seq = seq
        frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape[:2] != (SIZE[1], SIZE[0]):
            raise HTTPException(422, "Expected valid JPEG frame 640x480")
        stamp_mac = capture_mono + clock_offset
        with self.camera.lock:
            match = self.camera.matcher.match(stamp_mac, uncertainty)
        with self.lock:
            self.remote_frame = frame
            self.last_iphone_time = arrival
            self.remote_frame_window.append(arrival)
            window = self.remote_frame_window
            if len(window) > 2 and window[-1] > window[0]:
                self.remote_fps = (len(window) - 1) / (window[-1] - window[0])
            self.uncertainty_ms = 1000*uncertainty
            if not np.isfinite(stamp_mac) or not np.isfinite(uncertainty) or abs(stamp_mac - arrival) > 1.5:
                self.dropped_pairs += 1
                self.current_error = "Clock offset invalid or frames delayed; re-sync iPhone"
                return
            if match is None:
                self.dropped_pairs += 1
                self.current_error = "No synchronized frame pair (clock uncertainty or latency too high)"
                return
            chosen, delta_s = match
        self._process_pair(chosen.value, frame, stamp_mac, delta_s, uncertainty)

    def _process_pair(self, first: np.ndarray, second: np.ndarray, stamp_mac: float,
                      delta_s: float, uncertainty: float) -> None:
        arrival = time.monotonic()
        with self.lock:
            self.sync_error_ms = delta_s * 1000
            self.uncertainty_ms = uncertainty * 1000
            self.latest_pair = (first.copy(), second.copy(), arrival)
            self.last_pair_at = arrival
            self.matched_pairs += 1
            self.current_error = "Calibration required" if not self._profile_matches() else "Tracking marker"
            lo, hi = self.hsv_lo, self.hsv_hi
            profile = self.profile if self._profile_matches() else None
        m1 = green_marker(first, lo, hi)
        m2 = green_marker(second, lo, hi)
        with self.lock:
            self.last_detection = {"mac": [m1.x, m1.y] if m1 else None,
                                   "iphone": [m2.x, m2.y] if m2 else None}
            if profile is None:
                return
            if m1 is None or m2 is None:
                self.current_error = "Green marker not visible in both cameras"
                return
            result = triangulate(profile, (m1.x,m1.y), (m2.x,m2.y))
            if result is None:
                self.current_error = "Triangulation rejected (depth or reprojection error)"
                return
            xyz, error_px = result
            item = {"timestamp_mac_s": round(stamp_mac, 6),
                    "x_m": round(float(xyz[0]), 6), "y_m": round(float(xyz[1]), 6),
                    "z_m": round(float(xyz[2]), 6),
                    "reprojection_error_px": round(error_px, 4),
                    "sync_error_ms": round(delta_s * 1000, 2),
                    "clock_uncertainty_ms": round(uncertainty * 1000, 2)}
            self.last_measurement = {**item, "arrival_monotonic": arrival}
            self.path.append([item["x_m"],item["y_m"],item["z_m"]])
            self.store.add(item)
            self.current_error = ""

    def preview(self, camera: str) -> bytes | None:
        if camera == "mac":
            with self.camera.lock:
                frame = None if self.camera.latest_frame is None or time.monotonic()-self.camera.latest_time>2 else self.camera.latest_frame.copy()
        elif camera == "iphone":
            if self.local_stereo:
                source = self.local_cameras[1]
                with source.lock:
                    frame = None if source.latest_frame is None or time.monotonic()-source.latest_time>2 else source.latest_frame.copy()
            else:
                with self.lock:
                    frame = None if self.remote_frame is None or time.monotonic()-self.last_iphone_time>3 else self.remote_frame.copy()
        elif camera.startswith("local-") and camera[6:].isdigit():
            index = int(camera[6:])
            source = next((item for item in self.local_cameras if item.index == index), None)
            if source is None:
                return None
            with source.lock:
                frame = None if source.latest_frame is None or time.monotonic()-source.latest_time>2 else source.latest_frame.copy()
        else:
            return None
        if frame is None:
            return None
        lo, hi = self.hsv_lo, self.hsv_hi
        marker = green_marker(frame, lo, hi)
        if marker:
            center = (round(marker.x), round(marker.y))
            cv2.circle(frame, center, 15, (255, 255, 255), 2)
            cv2.drawMarker(frame, center, (0, 0, 0), cv2.MARKER_CROSS, 12, 2)
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpeg.tobytes() if ok else None

    def capture_calibration_pair(self) -> int:
        with self.lock:
            p = self.latest_pair
            if p is None or time.monotonic() - p[2] > 2:
                raise ValueError("No fresh synchronized pair; check both cameras")
            a, b = p[0].copy(), p[1].copy()
            if self.calibration_pairs >= 50:
                raise ValueError("At most 50 calibration pairs supported; reset if necessary")
        corners_a, corners_b = chessboard_corners(a), chessboard_corners(b)
        if corners_a is None or corners_b is None:
            raise ValueError("Chessboard not found in BOTH frames. Show all 9x6 internal corners")
        for old_a, old_b in self.store.load_pairs():
            old_corners_a, old_corners_b = chessboard_corners(old_a), chessboard_corners(old_b)
            if old_corners_a is None or old_corners_b is None:
                continue
            distance_a = float(np.sqrt(np.mean(np.sum((corners_a-old_corners_a)**2, axis=1))))
            distance_b = float(np.sqrt(np.mean(np.sum((corners_b-old_corners_b)**2, axis=1))))
            if distance_a < 12 and distance_b < 12:
                raise ValueError("Calibration pose is too similar to a saved pair; move or tilt the board")
        with self.lock:
            serial = self.calibration_pairs + 1
            self.store.save_pair(a, b, serial)
            self.calibration_pairs = serial
            return serial

    def calibrate(self, square_m: float) -> StereoProfile:
        if square_m < 0.005 or square_m > 0.1:
            raise ValueError("Square size must be between 5 and 100 mm")
        with self.lock:
            if not self.device or not self.device_mode:
                raise ValueError("Connect the second camera first")
            device, mode = self.device, self.device_mode
        p = calibrate_stereo(self.store.load_pairs(), square_m, self.camera.index, device, mode)
        with self.lock:
            self.profile = p
            self.store.save_profile(p)
            self.path.clear()
            self.last_measurement = None
        return p


def build_app(engine: Engine, *, start_camera: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifecycle(_: FastAPI):
        if start_camera:
            engine.start_sources()
        yield
        engine.stop_sources()

    app = FastAPI(title="PELENG local camera engine", docs_url=None, redoc_url=None, lifespan=lifecycle)
    app.state.engine = engine

    def require_local(request: Request):
        host = request.client.host if request.client else ""
        if not is_loopback(host) and host != "testclient":
            raise HTTPException(403, "Mac dashboard is restricted to localhost")

    def require_monitor(request: Request):
        token = request.headers.get("X-Monitor-Token", "")
        with engine.lock:
            expires = engine.monitor_tokens.get(token, 0)
            if expires <= time.monotonic():
                engine.monitor_tokens.pop(token, None)
                raise HTTPException(401, "Monitor session expired; pair again")

    @app.get("/api/health")
    def health():
        return {"service": "PELENG 3D", "protocol": 1, "status": "running"}

    @app.post("/api/pair")
    async def pair(request: Request):
        data = await request.json()
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("pin", "device_id", "camera_mode")):
            raise HTTPException(400, "Invalid pairing request")
        if not (3 <= len(data["device_id"]) <= 128 and 3 <= len(data["camera_mode"]) <= 128):
            raise HTTPException(400, "Invalid device identity")
        token = engine.pair(data["pin"], data["device_id"], data["camera_mode"])
        return {"token": token, "protocol": 1, "frame_size": [640, 480]}

    @app.post("/api/monitor/pair")
    async def monitor_pair(request: Request):
        data = await request.json()
        pin = data.get("pin") if isinstance(data, dict) else None
        if not isinstance(pin, str) or not secrets.compare_digest(pin, engine.pin):
            raise HTTPException(403, "Pairing code incorrect")
        token = secrets.token_urlsafe(32)
        with engine.lock:
            engine.monitor_tokens[token] = time.monotonic() + 24 * 60 * 60
        return {"token": token, "protocol": 1, "mode": "monitor"}

    @app.get("/api/monitor/dashboard")
    def monitor_dashboard(request: Request):
        require_monitor(request)
        return engine.status()

    @app.get("/api/monitor/preview/{camera}")
    def monitor_preview(camera: str, request: Request):
        require_monitor(request)
        pic = engine.preview(camera)
        if pic is None:
            return Response(status_code=204)
        return Response(content=pic, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/monitor/record/{action}")
    def monitor_record(action: str, request: Request):
        require_monitor(request)
        if action == "start":
            if not engine._profile_matches():
                raise HTTPException(409, "Valid calibration is required before recording")
            try:
                return {"session_id": engine.store.start()}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        if action == "stop":
            try:
                return {"filename": engine.store.stop().name}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        raise HTTPException(404, "Unknown recording action")

    @app.get("/api/monitor/history")
    def monitor_history(request: Request):
        require_monitor(request)
        return engine.store.history()

    @app.get("/api/time")
    def device_clock(request: Request):
        first = time.monotonic()
        token = request.headers.get("X-Session-Token", "")
        with engine.lock:
            valid = engine.token is not None and secrets.compare_digest(token, engine.token)
        if not valid:
            raise HTTPException(401, "Not paired")
        return {"t_receive": first, "t_send": time.monotonic()}

    @app.post("/api/frame")
    async def frame(request: Request):
        try:
            seq = int(request.headers["X-Frame-Id"])
            mono = float(request.headers["X-Capture-Mono"])
            offset = float(request.headers["X-Clock-Offset"])
            uncertainty = float(request.headers["X-Clock-Uncertainty"])
            device = request.headers["X-Device-Id"]
            mode = request.headers["X-Camera-Mode"]
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, "Invalid frame metadata") from exc
        if not all(map(np.isfinite, (mono, offset, uncertainty))) or uncertainty < 0 or uncertainty > 2:
            raise HTTPException(400, "Invalid clock data")
        if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise HTTPException(415, "Expected image/jpeg")
        # Reject by content-length before reading when the client supplies it.
        if int(request.headers.get("content-length", 0)) > 600_000:
            raise HTTPException(413, "Frame too large")
        payload = await request.body()
        engine.receive(payload, token=request.headers.get("X-Session-Token", ""), device_id=device,
                       mode=mode, seq=seq, capture_mono=mono, clock_offset=offset, uncertainty=uncertainty)
        return {"received": True}

    @app.get("/")
    def index(request: Request):
        require_local(request)
        return FileResponse(WEB / "index.html")

    @app.get("/api/dashboard")
    def dashboard(request: Request):
        require_local(request)
        return {**engine.status(), "pin": engine.pin, "lan_ip": local_ip(), "port": request.url.port or 80}

    @app.get("/api/preview/{camera}")
    def preview(camera: str, request: Request):
        require_local(request)
        pic = engine.preview(camera)
        if pic is None:
            return Response(status_code=204)
        return Response(content=pic, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/calibration/capture")
    def capture(request: Request):
        require_local(request)
        try:
            count = engine.capture_calibration_pair()
            return {"pairs": count}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/calibration/run")
    async def calibrate(request: Request):
        require_local(request)
        data = await request.json()
        try:
            profile = engine.calibrate(float(data.get("square_m", 0.025)))
            return {"calibration": profile.to_dict()}
        except (ValueError, cv2.error) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/calibration/reset")
    def reset(request: Request):
        require_local(request)
        with engine.lock:
            engine.store.clear_pairs()
            engine.calibration_pairs = 0
        return {"pairs": 0, "note": "Saved profile is preserved until replaced by a new calibration"}

    @app.post("/api/marker")
    async def marker(request: Request):
        require_local(request)
        data = await request.json()
        lo, hi = data.get("lower"), data.get("upper")
        if not (isinstance(lo, list) and isinstance(hi, list) and len(lo)==3 and len(hi)==3 and
                all(isinstance(x, int) for x in lo+hi) and
                all(0 <= x <= mx for x,mx in zip(lo+hi, [179,255,255]*2)) and
                all(x <= y for x,y in zip(lo,hi))):
            raise HTTPException(400, "HSV must be 3 integer lower/upper values")
        with engine.lock:
            engine.hsv_lo = tuple(lo)
            engine.hsv_hi = tuple(hi)
        return {"lower":lo, "upper":hi}

    @app.post("/api/record/start")
    def start(request: Request):
        require_local(request)
        if not engine._profile_matches():
            raise HTTPException(409, "Valid calibration is required before recording")
        try:
            return {"session_id": engine.store.start()}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/record/stop")
    def stop(request: Request):
        require_local(request)
        try:
            return {"filename": engine.store.stop().name}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/history")
    def history(request: Request):
        require_local(request)
        return engine.store.history()

    @app.get("/api/history/{name}")
    def history_file(name: str, request: Request):
        require_local(request)
        if Path(name).name != name or not name.endswith(".csv"):
            raise HTTPException(400, "Invalid filename")
        path = engine.store.root / "sessions" / name
        if not path.is_file():
            raise HTTPException(404, "Session not found")
        return FileResponse(path, filename=name, media_type="text/csv")

    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
    return app
