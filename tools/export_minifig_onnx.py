"""Convert the door-to-door minifig model (YOLOv8n, models/best.pt) to ONNX
for the game's opponent select screen. Same weights, no retraining.

The game runs the model with OpenCV's DNN module, so it doesn't need
ultralytics or torch. This script DOES need them, so run it with the
mini-fig-yolo project's venv, which has both:

    ~/mini-fig-yolo/.venv/bin/python tools/export_minifig_onnx.py

Writes assets/models/minifig_yolov8n.onnx (640 x 640 input, ONNX opset 12).
The export needs the `onnx` package; to leave that venv untouched it can be
installed to a separate folder and put on PYTHONPATH:
    ~/mini-fig-yolo/.venv/bin/python -m pip install --target /tmp/onnxdeps onnx
    (then delete /tmp/onnxdeps/numpy*, and run with PYTHONPATH=/tmp/onnxdeps
    YOLO_AUTOINSTALL=false)

Checked 2026-10-12: on the dataset's 9 test + 6 validation photos the ONNX
model in OpenCV finds the same minifigs with the same confidences (to 2
decimals) as ultralytics with best.pt.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", default=str(Path.home() / "mini-fig-yolo" / "models" / "best.pt"))
    ap.add_argument("--out", default=str(REPO_ROOT / "assets" / "models" / "minifig_yolov8n.onnx"))
    a = ap.parse_args()

    from ultralytics import YOLO
    with tempfile.TemporaryDirectory() as tmp:
        pt = Path(tmp) / "minifig.pt"
        shutil.copy(a.weights, pt)            # export writes next to the weights
        model = YOLO(str(pt))
        print("classes:", model.names)        # expect {0: 'blue-minifig', 1: 'green-minifig'}
        onnx = model.export(format="onnx", imgsz=640, opset=12, simplify=False, dynamic=False)
        shutil.copy(onnx, a.out)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
