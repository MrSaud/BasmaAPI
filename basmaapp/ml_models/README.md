Place OpenCV ONNX models here to enable higher-accuracy face matching.

Expected files:
- `face_detection_yunet_2023mar.onnx`
- `face_recognition_sface_2021dec.onnx`

Behavior:
- If both files are present, the app uses YuNet + SFace (more accurate).
- If missing, the app falls back to a lightweight OpenCV-only matcher.
