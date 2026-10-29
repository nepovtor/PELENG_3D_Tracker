"""Real stereo calibration and reconstruction. Coordinate frame is camera 1 (Mac)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import cv2
import numpy as np

PATTERN = (9, 6)  # internal chessboard corners (columns, rows)
SQUARE_METERS = 0.025
SIZE = (640, 480)


def crop_resize(frame: np.ndarray, size: tuple[int, int] = SIZE) -> np.ndarray:
    """Center-crop without geometric stretching, then resize to target image size."""
    if frame is None or frame.size == 0:
        raise ValueError("Empty camera frame")
    h, w = frame.shape[:2]
    target_ratio = size[0] / size[1]
    if w / h > target_ratio:
        nw = int(h * target_ratio)
        frame = frame[:, (w - nw)//2:(w - nw)//2 + nw]
    else:
        nh = int(w / target_ratio)
        frame = frame[(h - nh)//2:(h - nh)//2 + nh, :]
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def chessboard_corners(image: np.ndarray, pattern: tuple[int, int] = PATTERN) -> np.ndarray | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if hasattr(cv2, "findChessboardCornersSB"):
        flags = cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_EXHAUSTIVE + cv2.CALIB_CB_ACCURACY
        found, corners = cv2.findChessboardCornersSB(gray, pattern, flags)
        if not found:
            return None
        return corners.reshape(-1, 2).astype(np.float32)
    # Compatibility fallback for OpenCV builds that predate the SB detector.
    found, corners = cv2.findChessboardCorners(gray, pattern, cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return None
    cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 35, 0.001))
    return corners.reshape(-1, 2).astype(np.float32)


def object_grid(pattern: tuple[int, int] = PATTERN, square_m: float = SQUARE_METERS) -> np.ndarray:
    if square_m <= 0:
        raise ValueError("Square side must be positive")
    grid = np.zeros((pattern[0]*pattern[1], 3), np.float32)
    grid[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2)*square_m
    return grid


@dataclass(frozen=True)
class StereoProfile:
    K1: np.ndarray
    D1: np.ndarray
    K2: np.ndarray
    D2: np.ndarray
    R: np.ndarray  # camera-1 to camera-2 rotation
    T: np.ndarray  # camera-1 to camera-2 translation in meters
    size: tuple[int, int]
    mac_camera_index: int
    iphone_device_id: str
    iphone_camera_mode: str
    rms: float
    pairs: int

    def to_dict(self) -> dict:
        return {**{k: np.asarray(getattr(self, k)).tolist() for k in ("K1", "D1", "K2", "D2", "R", "T")},
                "size": list(self.size), "mac_camera_index": self.mac_camera_index,
                "iphone_device_id": self.iphone_device_id, "iphone_camera_mode": self.iphone_camera_mode,
                "rms": self.rms, "pairs": self.pairs, "baseline_m": float(np.linalg.norm(self.T)),
                "coordinate_frame": "mac-camera: +X right, +Y down, +Z forward; meters"}

    @classmethod
    def from_dict(cls, obj: dict) -> "StereoProfile":
        arrays = {k: np.array(obj[k], dtype=np.float64) for k in ("K1", "D1", "K2", "D2", "R", "T")}
        return cls(**arrays, size=tuple(obj["size"]), mac_camera_index=int(obj["mac_camera_index"]),
                   iphone_device_id=str(obj["iphone_device_id"]),
                   iphone_camera_mode=str(obj["iphone_camera_mode"]),
                   rms=float(obj["rms"]), pairs=int(obj["pairs"]))


def calibrate_stereo(pairs: Sequence[tuple[np.ndarray, np.ndarray]], square_m: float = SQUARE_METERS,
                     mac_camera_index: int = 0, iphone_device_id: str = "", iphone_camera_mode: str = "back-wide-640x480") -> StereoProfile:
    if len(pairs) < 12:
        raise ValueError("Need at least 12 accepted chessboard pairs")
    obj = object_grid(PATTERN, square_m)
    obj_points, p1, p2 = [], [], []
    for a, b in pairs:
        if a.shape != b.shape or a.shape[:2] != (SIZE[1], SIZE[0]):
            raise ValueError("Calibration frame sizes do not match 640x480")
        c1, c2 = chessboard_corners(a), chessboard_corners(b)
        if c1 is not None and c2 is not None:
            obj_points.append(obj.copy())
            p1.append(c1.reshape(-1, 1, 2))
            p2.append(c2.reshape(-1, 1, 2))
    if len(obj_points) < 12:
        raise ValueError(f"Only {len(obj_points)} usable pairs; at least 12 needed")
    criterion = (cv2.TERM_CRITERIA_MAX_ITER + cv2.TERM_CRITERIA_EPS, 80, 1e-6)
    _, K1, D1, _, _ = cv2.calibrateCamera(obj_points, p1, SIZE, None, None)
    _, K2, D2, _, _ = cv2.calibrateCamera(obj_points, p2, SIZE, None, None)
    rms, K1, D1, K2, D2, R, T, _, _ = cv2.stereoCalibrate(
        obj_points, p1, p2, K1, D1, K2, D2, SIZE, flags=cv2.CALIB_FIX_INTRINSIC, criteria=criterion)
    baseline = float(np.linalg.norm(T))
    if not np.isfinite(rms) or not 0.02 <= baseline <= 3.0 or rms > 3.0:
        raise ValueError(f"Rejected calibration: RMS={rms:.2f}px (max 3.0), baseline={baseline:.3f}m")
    return StereoProfile(K1, D1, K2, D2, R, T, SIZE, mac_camera_index, iphone_device_id, iphone_camera_mode, float(rms), len(obj_points))


def triangulate(profile: StereoProfile, mac_xy: tuple[float, float], iphone_xy: tuple[float, float],
                max_error_px: float = 3.5) -> tuple[np.ndarray, float] | None:
    """Undistort → triangulate → cheirality and real reprojection-error gate."""
    a = np.array(mac_xy, np.float64).reshape(1, 1, 2)
    b = np.array(iphone_xy, np.float64).reshape(1, 1, 2)
    un1 = cv2.undistortPoints(a, profile.K1, profile.D1).reshape(2, 1)
    un2 = cv2.undistortPoints(b, profile.K2, profile.D2).reshape(2, 1)
    P1 = np.column_stack([np.eye(3), np.zeros((3, 1))])
    P2 = np.column_stack([profile.R, profile.T.reshape(3, 1)])
    homogeneous = cv2.triangulatePoints(P1, P2, un1, un2).reshape(4)
    if not np.isfinite(homogeneous).all() or abs(homogeneous[3]) < 1e-9:
        return None
    point = homogeneous[:3] / homogeneous[3]
    z1 = float(point[2]); z2 = float((profile.R @ point + profile.T.reshape(3))[2])
    if not 0.1 < z1 < 10.0 or not 0.1 < z2 < 10.0:
        return None
    rvec2, _ = cv2.Rodrigues(profile.R)
    pred1, _ = cv2.projectPoints(point.reshape(1,3), np.zeros((3, 1)), np.zeros((3, 1)), profile.K1, profile.D1)
    pred2, _ = cv2.projectPoints(point.reshape(1,3), rvec2, profile.T.reshape(3,1), profile.K2, profile.D2)
    error = float(max(np.linalg.norm(pred1.reshape(2) - a.reshape(2)), np.linalg.norm(pred2.reshape(2) - b.reshape(2))))
    if not np.isfinite(error) or error > max_error_px:
        return None
    return point, error
