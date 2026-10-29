"""Persist only actual measurements and actual captured calibration pairs."""
from pathlib import Path
import csv
import json
import time
import uuid
import cv2
from .geometry import StereoProfile, SIZE, chessboard_corners


class CaptureStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.calibration_dir = self.root / "calibration_pairs"
        self.calibration_dir.mkdir(exist_ok=True)
        self.profile_path = self.root / "stereo_calibration.json"
        self.samples: list[dict] = []
        self.active = False
        self.session_id: str | None = None

    def save_pair(self, a, b, serial: int) -> None:
        if a.shape[:2] != (SIZE[1], SIZE[0]) or b.shape[:2] != (SIZE[1], SIZE[0]):
            raise ValueError("Unexpected calibration frame size")
        path1 = self.calibration_dir / f"{serial:03d}_mac.jpg"
        path2 = self.calibration_dir / f"{serial:03d}_iphone.jpg"
        encoded = []
        for frame in (a, b):
            ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR) if ok else None
            if decoded is None or chessboard_corners(decoded) is None:
                raise ValueError("Chessboard would not survive JPEG storage; improve focus or board size")
            encoded.append(jpeg.tobytes())
        temp1, temp2 = path1.with_suffix(".tmp.jpg"), path2.with_suffix(".tmp.jpg")
        try:
            temp1.write_bytes(encoded[0])
            temp2.write_bytes(encoded[1])
            temp1.replace(path1)
            temp2.replace(path2)
        except OSError:
            for path in (temp1, temp2):
                path.unlink(missing_ok=True)
            raise

    def load_pairs(self):
        result=[]
        for a in sorted(self.calibration_dir.glob("*_mac.jpg")):
            b = a.with_name(a.name.replace("_mac.jpg", "_iphone.jpg"))
            if b.exists():
                x, y = cv2.imread(str(a)), cv2.imread(str(b))
                if x is not None and y is not None:
                    result.append((x, y))
        return result

    def clear_pairs(self):
        for p in self.calibration_dir.glob("*.jpg"):
            p.unlink()

    def save_profile(self, p: StereoProfile):
        self.profile_path.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")

    def load_profile(self):
        if not self.profile_path.exists():
            return None
        try:
            return StereoProfile.from_dict(json.loads(self.profile_path.read_text(encoding="utf-8")))
        except (KeyError, TypeError, ValueError):
            return None

    def start(self):
        if self.active:
            raise ValueError("Recording already active")
        self.active = True
        self.samples = []
        self.session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        return self.session_id

    def add(self, sample: dict):
        if self.active:
            self.samples.append(sample)

    def stop(self):
        if not self.active:
            raise ValueError("No recording in progress")
        self.active = False
        folder = self.root / "sessions"
        folder.mkdir(exist_ok=True)
        path = folder / (self.session_id + ".csv")
        fields = ["timestamp_mac_s", "x_m", "y_m", "z_m", "reprojection_error_px", "sync_error_ms", "clock_uncertainty_ms"]
        with path.open("w", encoding="utf-8", newline="") as output:
            writer=csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.samples)
        return path

    def history(self):
        return [{"name": p.name, "size": p.stat().st_size} for p in sorted((self.root/"sessions").glob("*.csv"), reverse=True)] if (self.root/"sessions").exists() else []
