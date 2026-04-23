import base64
import math
from pathlib import Path

import cv2
import numpy as np


MODEL_DIR = Path(__file__).resolve().parent / "ml_models"
YUNET_MODEL_PATH = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_MODEL_PATH = MODEL_DIR / "face_recognition_sface_2021dec.onnx"


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _decode_base64_to_bgr(image_base64):
    raw = str(image_base64 or "").strip()
    if not raw:
        raise ValueError("image_base64 missing")
    if raw.startswith("data:image/"):
        raw = raw.split(",", 1)[1] if "," in raw else ""
    if not raw:
        raise ValueError("image_base64 invalid")
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("image_base64 decode failed") from exc
    image_array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image payload is not a valid image")
    return image


def _has_sface_pipeline():
    return (
        YUNET_MODEL_PATH.exists()
        and SFACE_MODEL_PATH.exists()
        and hasattr(cv2, "FaceDetectorYN_create")
        and hasattr(cv2, "FaceRecognizerSF_create")
    )


def _create_yunet(image_w, image_h):
    return cv2.FaceDetectorYN_create(
        str(YUNET_MODEL_PATH),
        "",
        (int(image_w), int(image_h)),
        score_threshold=0.85,
        nms_threshold=0.3,
        top_k=500,
    )


def _detect_best_face(image_bgr):
    h, w = image_bgr.shape[:2]
    detector = _create_yunet(w, h)
    detector.setInputSize((w, h))
    _, faces = detector.detect(image_bgr)
    if faces is None or len(faces) == 0:
        return None
    best_idx = int(np.argmax(faces[:, 14]))
    return faces[best_idx]


def _extract_aligned_face(image_bgr):
    face = _detect_best_face(image_bgr)
    if face is None:
        return None, "no face detected"
    recognizer = cv2.FaceRecognizerSF_create(str(SFACE_MODEL_PATH), "")
    aligned = recognizer.alignCrop(image_bgr, face)
    if aligned is None or aligned.size == 0:
        return None, "failed to align face"
    return aligned, ""


def _heuristic_liveness(face_bgr):
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    mean_luma = float(np.mean(gray))
    contrast = float(np.std(gray))
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    brightness_score = 1.0 - abs(mean_luma - 128.0) / 128.0
    contrast_score = contrast / 64.0
    focus_score = lap_var / 800.0

    live_score = (
        0.35 * _clamp(brightness_score)
        + 0.30 * _clamp(contrast_score)
        + 0.35 * _clamp(focus_score)
    )
    return _clamp(live_score)


def run_liveness_check(image_base64, threshold=0.60, min_size=60, min_neighbors=5, pad=0.15):
    del min_neighbors, pad
    try:
        image = _decode_base64_to_bgr(image_base64)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    h, w = image.shape[:2]
    if h < int(min_size) or w < int(min_size):
        return {"ok": False, "error": "image size is too small"}

    if _has_sface_pipeline():
        face_aligned, face_err = _extract_aligned_face(image)
        if face_aligned is None:
            return {"ok": False, "error": face_err}
        live_score = _heuristic_liveness(face_aligned)
    else:
        # Fallback when ONNX models are not present.
        live_score = _heuristic_liveness(image)

    is_live = live_score >= float(threshold)
    return {
        "ok": True,
        "is_live": bool(is_live),
        "live_score": round(float(live_score), 4),
    }


def _vectorize_face(face_bgr):
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
    vector = resized.astype(np.float32).reshape(-1)
    vector -= float(np.mean(vector))
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-8:
        return vector
    return vector / norm


def _fallback_similarity(face_a, face_b):
    vec_a = _vectorize_face(face_a)
    vec_b = _vectorize_face(face_b)
    cosine = float(np.dot(vec_a, vec_b))
    cosine = _clamp((cosine + 1.0) / 2.0)

    orb = cv2.ORB_create(nfeatures=500)
    kpa, desa = orb.detectAndCompute(face_a, None)
    kpb, desb = orb.detectAndCompute(face_b, None)
    if desa is None or desb is None or len(kpa) == 0 or len(kpb) == 0:
        return cosine
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(desa, desb)
    if not matches:
        return cosine
    matches = sorted(matches, key=lambda x: x.distance)
    top = matches[: min(60, len(matches))]
    avg_dist = sum(m.distance for m in top) / float(len(top))
    orb_similarity = 1.0 - _clamp(avg_dist / 128.0)
    return _clamp(0.65 * cosine + 0.35 * orb_similarity)


def run_face_compare(image1_base64, image2_base64, threshold=0.35, min_size=60, min_neighbors=5, pad=0.15):
    del min_neighbors, pad
    try:
        image1 = _decode_base64_to_bgr(image1_base64)
        image2 = _decode_base64_to_bgr(image2_base64)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if min(image1.shape[:2]) < int(min_size) or min(image2.shape[:2]) < int(min_size):
        return {"ok": False, "error": "image size is too small"}

    if _has_sface_pipeline():
        face1, err1 = _extract_aligned_face(image1)
        if face1 is None:
            return {"ok": False, "error": err1}
        face2, err2 = _extract_aligned_face(image2)
        if face2 is None:
            return {"ok": False, "error": err2}
        recognizer = cv2.FaceRecognizerSF_create(str(SFACE_MODEL_PATH), "")
        feat1 = recognizer.feature(face1)
        feat2 = recognizer.feature(face2)
        score = float(recognizer.match(feat1, feat2, cv2.FaceRecognizerSF_FR_COSINE))
        similarity = _clamp(score)
        effective_threshold = max(0.30, float(threshold))
    else:
        similarity = _fallback_similarity(image1, image2)
        effective_threshold = 0.78

    return {
        "ok": True,
        "is_match": bool(similarity >= effective_threshold),
        "similarity": round(float(similarity), 4),
    }
