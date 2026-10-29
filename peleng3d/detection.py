"""Image-only color marker detection: no synthetic coordinates."""
from dataclasses import dataclass
import cv2
import numpy as np

@dataclass(frozen=True)
class Marker:
    x: float
    y: float
    area: float


def green_marker(bgr: np.ndarray, hsv_lower=(35, 80, 70), hsv_upper=(90, 255, 255)) -> Marker | None:
    if bgr is None or bgr.size == 0:
        return None
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lower, np.uint8), np.array(hsv_upper, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5,5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)
        if area < 35 or area > 50000:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4*np.pi*area/(perimeter*perimeter) if perimeter else 0
        if circularity < 0.48:
            continue
        m = cv2.moments(contour)
        if m["m00"] > 0:
            return Marker(m["m10"]/m["m00"], m["m01"]/m["m00"], float(area))
    return None
