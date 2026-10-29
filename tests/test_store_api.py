"""HTTP integration with generated JPEG, not a claim of live hardware testing."""
import csv
import time
from pathlib import Path
import cv2
import numpy as np
from fastapi.testclient import TestClient
from peleng3d.server import Engine, build_app, local_ip
from peleng3d.store import CaptureStore
from peleng3d.sync import TimedFrame


def calibration_board():
    board=np.full((480,640,3),255,np.uint8)
    for row in range(7):
        for col in range(10):
            if (col+row)%2==0:
                cv2.rectangle(board,(100+col*38,100+row*38),
                              (100+(col+1)*38,100+(row+1)*38),(0,0,0),-1)
    return board


def test_local_ip_prefers_macos_wifi_over_vpn(monkeypatch):
    class Result:
        stdout = "192.168.1.231\n"

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("peleng3d.server.subprocess.run", lambda *args, **kwargs: Result())
    assert local_ip() == "192.168.1.231"


def test_csv_recording_persists_actual_rows(tmp_path):
    store=CaptureStore(tmp_path)
    session=store.start()
    row={"timestamp_mac_s":4.0,"x_m":0.1,"y_m":.02,"z_m":1.5,
         "reprojection_error_px":.2,"sync_error_ms":9,"clock_uncertainty_ms":2}
    store.add(row)
    path=store.stop()
    assert session in path.name
    with path.open(newline="") as f:
        data=list(csv.DictReader(f))
    assert len(data)==1 and float(data[0]["z_m"])==1.5


def test_saved_calibration_jpeg_remains_detectable(tmp_path):
    store=CaptureStore(tmp_path)
    board=calibration_board()
    store.save_pair(board,board,1)
    assert len(store.load_pairs())==1


def test_duplicate_calibration_pose_is_rejected(tmp_path):
    engine=Engine(tmp_path)
    board=calibration_board()
    engine.latest_pair=(board,board,time.monotonic())
    assert engine.capture_calibration_pair()==1
    engine.latest_pair=(board.copy(),board.copy(),time.monotonic())
    try:
        engine.capture_calibration_pair()
    except ValueError as exc:
        assert "too similar" in str(exc)
    else:
        raise AssertionError("Duplicate calibration pose was accepted")


def test_http_pairing_and_one_generated_frame(tmp_path):
    engine=Engine(tmp_path)
    app=build_app(engine,start_camera=False)
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/dashboard').status_code==200
        assert client.post('/api/pair',json={'pin':'000000','device_id':'iphone-1','camera_mode':'back-wide-landscape-640x480-v1'}).status_code==403 if engine.pin != '000000' else True
        pair=client.post('/api/pair',json={'pin':engine.pin,'device_id':'iphone-1','camera_mode':'back-wide-landscape-640x480-v1'})
        assert pair.status_code==200
        token=pair.json()['token']
        assert client.get('/api/time',headers={'X-Session-Token':'wrong'}).status_code==401
        assert client.get('/api/time',headers={'X-Session-Token':token}).status_code==200
        image=np.zeros((480,640,3),dtype=np.uint8)
        cv2.circle(image,(350,220),18,(0,255,0),-1)
        ok,jpg=cv2.imencode('.jpg',image)
        assert ok
        now=time.monotonic()
        with engine.camera.lock:
            engine.camera.matcher.add(TimedFrame(1,now,image))
        headers={'Content-Type':'image/jpeg','X-Session-Token':token,'X-Device-Id':'iphone-1',
                 'X-Camera-Mode':'back-wide-landscape-640x480-v1', 'X-Frame-Id':'1',
                 'X-Capture-Mono':str(now), 'X-Clock-Offset':'0', 'X-Clock-Uncertainty':'0.001'}
        r=client.post('/api/frame',headers=headers,content=jpg.tobytes())
        assert r.status_code==200,r.text
        status=client.get('/api/dashboard').json()
        assert status['iphone_online'] and status['matched_pairs']==1
        assert status['measurement'] is None  # cannot fake 3D without calibration
        assert client.get('/api/preview/iphone').status_code==200
        assert client.post('/api/frame',headers=headers,content=jpg.tobytes()).status_code==409
        assert client.post('/api/record/start').status_code==409
        assert client.post('/api/calibration/capture').status_code==400
        assert client.get('/api/history').json()==[]


def test_synthetic_end_to_end_measurement_after_calibration_profile(tmp_path):
    """Synthetic fixture exercises HTTP -> synchronization -> detector -> triangulation -> CSV."""
    from peleng3d.geometry import StereoProfile, SIZE
    k=np.array([[700.,0,320.],[0,700.,240.],[0,0,1.]])
    mode='back-wide-landscape-640x480-v1'
    profile=StereoProfile(k,np.zeros(5),k,np.zeros(5),np.eye(3),np.array([[-.3],[0],[0]]),SIZE,0,'phone-123',mode,.18,20)
    engine=Engine(tmp_path)
    app=build_app(engine,start_camera=False)
    with TestClient(app) as client:
        p=client.post('/api/pair',json={'pin':engine.pin,'device_id':'phone-123','camera_mode':mode})
        assert p.status_code==200
        engine.profile=profile
        assert engine._profile_matches()
        assert client.post('/api/record/start').status_code==200
        # A green circle at projected point (0,0,1.5 meters), both real code paths.
        target=np.array([[0.,0.,1.5]])
        mac_xy,_=cv2.projectPoints(target,np.zeros(3),np.zeros(3),k,np.zeros(5))
        iphone_xy,_=cv2.projectPoints(target,np.zeros(3),profile.T,k,np.zeros(5))
        mac=np.zeros((480,640,3),np.uint8)
        remote=np.zeros_like(mac)
        cv2.circle(mac,tuple(np.rint(mac_xy.reshape(2)).astype(int)),18,(0,255,0),-1)
        cv2.circle(remote,tuple(np.rint(iphone_xy.reshape(2)).astype(int)),18,(0,255,0),-1)
        now=time.monotonic()
        with engine.camera.lock:engine.camera.matcher.add(TimedFrame(1,now,mac))
        ok,jpeg=cv2.imencode('.jpg',remote)
        headers={'Content-Type':'image/jpeg','X-Session-Token':p.json()['token'],'X-Device-Id':'phone-123','X-Camera-Mode':mode,
                 'X-Frame-Id':'1','X-Capture-Mono':str(now),'X-Clock-Offset':'0','X-Clock-Uncertainty':'0.002'}
        assert client.post('/api/frame',headers=headers,content=jpeg.tobytes()).status_code==200
        status=client.get('/api/dashboard').json()
        assert status['measurement'] is not None,status['error']
        assert abs(status['measurement']['z_m']-1.5)<.03
        assert status['samples_recorded']==1
        ending=client.post('/api/record/stop')
        assert ending.status_code==200
        filename=ending.json()['filename']
        downloaded=client.get('/api/history/'+filename)
        assert downloaded.status_code==200 and 'z_m' in downloaded.text
