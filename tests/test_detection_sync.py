"""Synthetic image and timing tests; no virtual streams used in runtime."""
import cv2
import numpy as np
from peleng3d.detection import green_marker
from peleng3d.sync import FrameMatcher, TimedFrame, estimate_offset


def test_green_ball_center():
    im=np.zeros((480,640,3),dtype=np.uint8)
    cv2.circle(im,(200,170),24,(0,255,0),-1)
    marker=green_marker(im)
    assert marker is not None
    assert abs(marker.x-200)<1 and abs(marker.y-170)<1


def test_black_image_has_no_marker():
    assert green_marker(np.zeros((480,640,3),dtype=np.uint8)) is None


def test_clock_offset_recovers_known_offset():
    # iPhone time=10, Mac time=110; symmetric 20ms one-way travel
    offset,uncertainty=estimate_offset(10,110.02,110.021,10.041)
    assert abs(offset-100)<0.001
    assert abs(uncertainty-0.02)<0.001


def test_frame_matching_single_use_and_tolerance():
    matcher=FrameMatcher(tolerance_s=.08)
    for seq in range(3):matcher.add(TimedFrame(seq,10+seq*.05,str(seq)))
    picked=matcher.match(10.052, .01)
    assert picked is not None and picked[0].seq==1
    assert matcher.match(10.053,.01)[0].seq==2  # cannot reuse frame #1
    assert matcher.match(10.054,.01) is None
    assert matcher.match(10.055,.07) is None


def test_frame_matching_rejects_high_uncertainty():
    matcher=FrameMatcher(tolerance_s=.09)
    matcher.add(TimedFrame(1,20.0,None))
    assert matcher.match(20.0,.08) is None
