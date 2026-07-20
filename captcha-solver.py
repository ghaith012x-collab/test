import cv2
import numpy as np
from PIL import Image
import io
import math
from typing import Union, Tuple, Optional


def solve_slide_puzzle(background_bytes: bytes, piece_bytes: bytes) -> Optional[float]:
    """Return the x-offset (in px) of the puzzle piece within the background.

    Ported from xtekky/TikTok-Captcha-Solver (OpenCV Sobel + template matching,
    no external API required). Accepts raw image bytes.
    """
    try:
        bg = _decode(background_bytes)
        piece = _decode(piece_bytes)
        if bg is None or piece is None:
            return None
        bg_proc = _sobel(bg)
        piece_proc = _sobel(piece)
        matched = cv2.matchTemplate(bg_proc, piece_proc, cv2.TM_CCOEFF_NORMED)
        _, _, _, max_loc = cv2.minMaxLoc(matched)
        return float(max_loc[0])
    except Exception as e:
        if debug_flag:
            print(f"[slide_solver] error: {e}")
        return None


def _decode(buf: bytes):
    arr = np.frombuffer(buf, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _sobel(img):
    ddepth = cv2.CV_16S
    img = cv2.GaussianBlur(img, (3, 3), 0)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grad_x = cv2.Sobel(gray, ddepth, 1, 0, ksize=3, borderType=cv2.BORDER_DEFAULT)
    grad_y = cv2.Sobel(gray, ddepth, 0, 1, ksize=3, borderType=cv2.BORDER_DEFAULT)
    abs_x = cv2.convertScaleAbs(grad_x)
    abs_y = cv2.convertScaleAbs(grad_y)
    return cv2.addWeighted(abs_x, 0.5, abs_y, 0.5, 0)


debug_flag = False


def _load_image(img):
    try:
        if isinstance(img, str):
            img = cv2.imread(img, cv2.IMREAD_GRAYSCALE)
        elif isinstance(img, bytes):
            arr = np.frombuffer(img, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        elif isinstance(img, Image.Image):
            img = np.array(img.convert('L'))
        elif isinstance(img, np.ndarray):
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if img is None or img.size == 0:
            return None
        return img
    except Exception:
        return None

def _preprocess(img):
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = 400 / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(img, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    edges = cv2.Canny(thresh, 50, 150)
    return edges

def _circular_mask(img):
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    center = (w // 2, h // 2)
    radius = min(h, w) // 2 - 5
    cv2.circle(mask, center, radius, 255, -1)
    return mask

def _compute_edge_continuity_score(outer_edges, inner_edges_rotated):
    if outer_edges is None or inner_edges_rotated is None:
        return 0.0
    mask = _circular_mask(outer_edges)
    outer_masked = cv2.bitwise_and(outer_edges, outer_edges, mask=mask)
    inner_masked = cv2.bitwise_and(inner_edges_rotated, inner_edges_rotated, mask=mask)
    combined = cv2.bitwise_or(outer_masked, inner_masked)
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    total_length = 0
    good_contours = 0
    for cnt in contours:
        length = cv2.arcLength(cnt, False)
        total_length += length
        if length > 25:
            good_contours += 1
    edge_pixels = np.sum(combined > 0)
    area = np.sum(mask > 0)
    density = edge_pixels / max(area, 1)
    score = (good_contours * 2.5) + (total_length / 30) + (density * 180)
    return float(score)

def _feature_matching_score(outer, inner_rotated):
    try:
        orb = cv2.ORB_create(nfeatures=800, scaleFactor=1.2, nlevels=8)
        kp1, des1 = orb.detectAndCompute(outer, None)
        kp2, des2 = orb.detectAndCompute(inner_rotated, None)
        if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
            return 0.0
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = bf.match(des1, des2)
        if len(matches) < 8:
            return 0.0
        matches = sorted(matches, key=lambda x: x.distance)
        good_matches = [m for m in matches if m.distance < 45]
        if not good_matches:
            return len(matches) * 0.3
        avg_dist = np.mean([m.distance for m in good_matches])
        match_score = len(good_matches) * (1 / (avg_dist + 1)) * 3.5
        return float(match_score)
    except Exception:
        return 0.0

def _estimate_rotation_angle(outer, inner, angle_step=2, search_range=180):
    if outer is None or inner is None:
        return 0.0, 0.0
    outer_edges = _preprocess(outer)
    inner_edges_base = _preprocess(inner)
    if outer_edges is None or inner_edges_base is None:
        return 0.0, 0.0
    h, w = outer_edges.shape[:2]
    inner_edges_base = cv2.resize(inner_edges_base, (w, h))
    best_angle = 0.0
    best_score = -1.0
    scores = []
    angles = list(range(-search_range, search_range + 1, angle_step))
    center = (w // 2, h // 2)
    mask = _circular_mask(outer_edges)
    
    for angle in angles:
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        inner_rotated = cv2.warpAffine(inner_edges_base, M, (w, h),
                                       flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        inner_rotated = cv2.bitwise_and(inner_rotated, inner_rotated, mask=mask)
        continuity_score = _compute_edge_continuity_score(outer_edges, inner_rotated)
        feature_score = _feature_matching_score(outer, inner_rotated)
        total_score = (continuity_score * 0.65) + (feature_score * 0.35)
        scores.append((angle, total_score))
        if total_score > best_score:
            best_score = total_score
            best_angle = angle
    
    if angle_step > 1:
        refine_center = int(best_angle)
        refine_range = angle_step * 3
        refined_scores = []
        for a in range(refine_center - refine_range, refine_center + refine_range + 1):
            M = cv2.getRotationMatrix2D(center, a, 1.0)
            inner_rotated = cv2.warpAffine(inner_edges_base, M, (w, h),
                                           flags=cv2.INTER_LINEAR,
                                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            inner_rotated = cv2.bitwise_and(inner_rotated, inner_rotated, mask=mask)
            cont = _compute_edge_continuity_score(outer_edges, inner_rotated)
            feat = _feature_matching_score(outer, inner_rotated)
            refined_scores.append((a, cont * 0.65 + feat * 0.35))
        if refined_scores:
            best_angle, best_score = max(refined_scores, key=lambda x: x[1])
    
    best_angle = best_angle % 360
    if best_angle > 180:
        best_angle -= 360
    confidence = min(100.0, max(0.0, (best_score / 45.0) * 100))
    return round(best_angle, 1), round(confidence, 1)

def solve_rotate_captcha(outer, inner, debug=False):
    outer_img = _load_image(outer)
    inner_img = _load_image(inner)
    if outer_img is None or inner_img is None:
        if debug:
            print("[captcha_solver] Failed to load images")
        return 0.0, 0.0
    angle, confidence = _estimate_rotation_angle(outer_img, inner_img)
    if debug:
        print(f"[captcha_solver] angle={angle}° confidence={confidence}%")
    return angle, confidence

def solve_rotate_captcha_robust(outer, inner, debug=False):
    results = []
    a1, c1 = solve_rotate_captcha(outer, inner, debug=debug)
    results.append((a1, c1, "edge+orb"))
    try:
        outer_img = _load_image(outer)
        inner_img = _load_image(inner)
        if outer_img is not None and inner_img is not None:
            outer_e = cv2.Canny(outer_img, 30, 180)
            inner_e = cv2.Canny(inner_img, 30, 180)
            h, w = outer_e.shape[:2]
            inner_e = cv2.resize(inner_e, (w, h))
            angle2, conf2 = _estimate_rotation_angle(outer_e, inner_e)
            results.append((angle2, conf2, "strong-canny"))
    except:
        pass
    if not results:
        return 0.0, 0.0
    best = max(results, key=lambda x: x[1])
    if debug:
        print(f"[captcha_solver] Best: {best[2]} → {best[0]}° ({best[1]}%)")
    return best[0], best[1]

if __name__ == "__main__":
    print("Captcha solver ready")
