"""Nhận diện rác bằng model Teachable Machine (đã chuyển sang ONNX).

Model: MobileNetV2 (alpha 0.35), ảnh vào 224x224, giá trị pixel trong [-1, 1].
Nhãn đọc từ models/metadata.json (đúng thứ tự lúc huấn luyện).
"""
import io
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

MODEL_DIR = Path(__file__).parent / "models"
IMAGE_SIZE = 224

# Nhãn của model -> mã trong bảng waste_types.
# None nghĩa là "không có vật thể / nền" -> luôn trả "unknown".
LABEL_TO_CODE = {
    "Trash": "trash",
    "Plastic": "plastic",
    "Paper": "paper",
    "Metal": "metal",
    "Glass": "glass",
    "Cardboard": "cardboard",
    "None": "unknown",
}

with open(MODEL_DIR / "metadata.json", encoding="utf-8") as f:
    LABELS = json.load(f)["labels"]

_missing = [l for l in LABELS if l not in LABEL_TO_CODE]
if _missing:
    raise RuntimeError(f"Nhãn chưa được ánh xạ trong LABEL_TO_CODE: {_missing}")

# Nạp model đúng một lần khi server khởi động
_session = ort.InferenceSession(
    str(MODEL_DIR / "waste_model.onnx"), providers=["CPUExecutionProvider"]
)
_input_name = _session.get_inputs()[0].name


def _preprocess(image_bytes: bytes) -> np.ndarray:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)          # xoay đúng chiều ảnh chụp từ điện thoại
        img = img.convert("RGB")
    except Exception as exc:                          # ảnh hỏng, bị cắt cụt, quá lớn...
        raise ValueError("Không đọc được ảnh.") from exc
    # Cắt vuông ở giữa rồi co về 224x224 (giống cách Teachable Machine xử lý)
    img = ImageOps.fit(img, (IMAGE_SIZE, IMAGE_SIZE), Image.BILINEAR)
    arr = np.asarray(img, dtype=np.float32)
    arr = arr / 127.5 - 1.0                           # đưa về [-1, 1]
    return arr[np.newaxis, ...]                       # (1, 224, 224, 3)


def predict(image_bytes: bytes) -> dict:
    """Trả {"label": mã loại rác hoặc "unknown", "confidence": float, "scores": {...}}."""
    x = _preprocess(image_bytes)
    probs = _session.run(None, {_input_name: x})[0][0]
    top = int(np.argmax(probs))
    return {
        "label": LABEL_TO_CODE[LABELS[top]],
        "confidence": round(float(probs[top]), 4),
        "scores": {LABEL_TO_CODE[l]: round(float(p), 4) for l, p in zip(LABELS, probs)},
    }
