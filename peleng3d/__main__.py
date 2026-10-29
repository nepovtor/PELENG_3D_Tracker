"""Start the actual MacBook tracking service."""
import argparse
from pathlib import Path
import sys
import uvicorn
from .server import Engine, build_app, local_ip


def preflight_macos_camera(camera_index: int) -> None:
    """Request AVFoundation camera access on the process main thread.

    OpenCV cannot present the macOS privacy prompt from Engine's capture
    worker.  A short main-thread open lets an interactive LaunchAgent request
    permission once; the real capture is still created and owned by Engine.
    """
    if sys.platform != "darwin":
        return
    try:
        import cv2
        capture = cv2.VideoCapture(camera_index, cv2.CAP_AVFOUNDATION)
        capture.release()
    except Exception as exc:
        print(f"PELENG camera permission preflight failed: {exc}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="PELENG: real multi-camera stereo tracker")
    parser.add_argument("serve", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--camera", type=int, action="append", dest="cameras",
                        help="camera index; repeat for local stereo, e.g. --camera 1 --camera 2")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, default=Path.home()/"Documents"/"PELENG3D")
    args = parser.parse_args()
    cameras = args.cameras or [0]
    for camera in cameras:
        preflight_macos_camera(camera)
    engine = Engine(args.data, cameras)
    print("\n========================================", flush=True)
    print(" PELENG 3D — actual cameras only", flush=True)
    print(" Mac dashboard: http://127.0.0.1:%d" % args.port, flush=True)
    print(" iPhone Mac address: http://%s:%d" % (local_ip(),args.port), flush=True)
    print(" Pairing PIN: %s" % engine.pin, flush=True)
    print(" Recordings: %s" % args.data, flush=True)
    print(" Camera mode: %s" % ("local indexes " + ", ".join(map(str, cameras)) if len(cameras) > 1 else "Mac + iPhone"), flush=True)
    print("========================================\n", flush=True)
    uvicorn.run(build_app(engine), host="0.0.0.0", port=args.port, log_level="warning")

if __name__ == "__main__":
    main()
