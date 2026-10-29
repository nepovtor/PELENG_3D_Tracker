"""SYNTHETIC UNIT TESTS ONLY — not evidence of physical-camera accuracy."""
import cv2
import numpy as np
import pytest
from peleng3d.geometry import StereoProfile, SIZE, crop_resize, triangulate, object_grid, chessboard_corners


def profile():
    k = np.array([[700.,0,320],[0,700,240],[0,0,1.]])
    return StereoProfile(k,np.zeros(5),k,np.zeros(5),np.eye(3),np.array([[-0.3],[0.],[0.]]),SIZE,0,"device-test","back-wide-landscape-640x480-v1",0.2,16)


def test_triangulate_known_synthetic_point():
    p=profile()
    target=np.array([.10,-.04,1.8])
    mac, _=cv2.projectPoints(target.reshape(1,3),np.zeros(3),np.zeros(3),p.K1,p.D1)
    iphone, _=cv2.projectPoints(target.reshape(1,3),np.zeros(3),p.T,p.K2,p.D2)
    result=triangulate(p,tuple(mac.reshape(2)),tuple(iphone.reshape(2)))
    assert result is not None
    xyz,error=result
    assert np.linalg.norm(xyz-target) < 1e-6
    assert error < 1e-5


def test_synthetic_corrupt_pair_rejected():
    p=profile()
    assert triangulate(p, (350,230),(750,230)) is None


def test_synthetic_wrong_vertical_correspondence_rejected():
    p=profile()
    assert triangulate(p,(350,230),(250,300)) is None


def test_profile_roundtrip_and_binding():
    p=profile();q=StereoProfile.from_dict(p.to_dict())
    assert np.allclose(q.K1,p.K1)
    assert np.allclose(q.T,p.T)
    assert q.size==(640,480)
    assert q.iphone_device_id=="device-test"


def test_crop_resize_preserves_ratio_without_stretch():
    frame=np.zeros((720,1280,3),np.uint8)
    frame[:, :160]=255
    out=crop_resize(frame)
    assert out.shape==(480,640,3)
    assert np.max(out)==0


def test_board_grid_has_meter_units():
    grid=object_grid()
    assert grid.shape==(54,3)
    assert grid[:,0].max()==pytest.approx(.2)
    assert grid[:,1].max()==pytest.approx(.125)
    with pytest.raises(ValueError): object_grid(square_m=0)


def test_chessboard_corner_count():
    board=np.full((480,640,3),255,np.uint8)
    for row in range(7):
        for col in range(10):
            if (col+row)%2==0:
                cv2.rectangle(board,(100+col*38,100+row*38),(100+(col+1)*38,100+(row+1)*38),(0,0,0),-1)
    corners=chessboard_corners(board)
    assert corners is not None and corners.shape==(54,2)


def test_chessboard_uses_sb_detector_when_available(monkeypatch):
    board=np.full((480,640,3),255,np.uint8)
    expected=np.arange(108,dtype=np.float32).reshape(54,1,2)
    called={"sb":False}
    def fake_sb(gray, pattern, flags):
        called["sb"]=True
        assert pattern==(9,6)
        assert flags & cv2.CALIB_CB_ACCURACY
        return True, expected
    monkeypatch.setattr(cv2,"findChessboardCornersSB",fake_sb)
    corners=chessboard_corners(board)
    assert called["sb"] and np.array_equal(corners,expected.reshape(54,2))
