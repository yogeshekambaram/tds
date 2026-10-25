"""Morph the face in from.jpg into the face in to.jpg (landmark-based, Delaunay piecewise-affine warp).

Steps: detect 468 MediaPipe landmarks on the largest face in each photo, align both photos on the eyes
into the same square frame, then for each frame interpolate the landmark positions, warp both photos to
that intermediate shape triangle by triangle, and cross-blend. Frames are written to frames/ for encoding.

Usage: python morph.py [size] [morph_frames] [hold_frames]
"""
import os
import sys

import cv2
import mediapipe as mp
import numpy as np
from scipy.spatial import Delaunay

S = int(sys.argv[1]) if len(sys.argv) > 1 else 256
N = int(sys.argv[2]) if len(sys.argv) > 2 else 48
HOLD = int(sys.argv[3]) if len(sys.argv) > 3 else 8
LEFT_EYE, RIGHT_EYE = [33, 133, 160, 159, 158, 144, 145, 153], [362, 263, 387, 386, 385, 373, 374, 380]


def landmarks(img):
    """468 landmarks (pixels) of the largest face in a BGR image."""
    with mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=4, refine_landmarks=False) as fm:
        res = fm.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    if not res.multi_face_landmarks:
        raise SystemExit("no face found")
    h, w = img.shape[:2]
    faces = [np.array([[p.x * w, p.y * h] for p in f.landmark[:468]]) for f in res.multi_face_landmarks]
    return max(faces, key=lambda f: np.ptp(f[:, 0]) * np.ptp(f[:, 1]))


def align(img, pts):
    """Similarity transform putting the eye centres at fixed positions in an S x S frame."""
    le, re_ = pts[LEFT_EYE].mean(0), pts[RIGHT_EYE].mean(0)
    src = np.float32([le, re_])
    dst = np.float32([[S * 0.35, S * 0.40], [S * 0.65, S * 0.40]])
    # 2-point similarity: rotation + uniform scale + translation
    d_src, d_dst = src[1] - src[0], dst[1] - dst[0]
    scale = np.linalg.norm(d_dst) / np.linalg.norm(d_src)
    ang = np.arctan2(d_dst[1], d_dst[0]) - np.arctan2(d_src[1], d_src[0])
    c, s = scale * np.cos(ang), scale * np.sin(ang)
    M = np.array([[c, -s, 0], [s, c, 0]], np.float64)
    M[:, 2] = dst[0] - M[:, :2] @ src[0]
    out = cv2.warpAffine(img, M, (S, S), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_REFLECT)
    return out, pts @ M[:, :2].T + M[:, 2]


def with_border(pts):
    """Add frame corners/edge points so the whole image (hair, background) warps smoothly."""
    e = S - 1
    k = np.linspace(0, e, 5)
    border = [[x, 0] for x in k] + [[x, e] for x in k] + [[0, y] for y in k[1:-1]] + [[e, y] for y in k[1:-1]]
    return np.vstack([pts, np.array(border, float)])


def warp_to(img, src_pts, dst_pts, tris):
    """Piecewise-affine warp of img so that src_pts land on dst_pts."""
    out = np.zeros_like(img, dtype=np.float32)
    for t in tris:
        s, d = np.float32(src_pts[t]), np.float32(dst_pts[t])
        x, y, w, h = cv2.boundingRect(d)
        if w == 0 or h == 0:
            continue
        sx, sy, sw, sh = cv2.boundingRect(s)
        sx, sy = max(sx - 1, 0), max(sy - 1, 0)
        patch = img[sy:sy + sh + 2, sx:sx + sw + 2]
        if patch.size == 0:
            continue
        M = cv2.getAffineTransform(np.float32(s - [sx, sy]), np.float32(d - [x, y]))
        warped = cv2.warpAffine(patch, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
        mask = np.zeros((h, w), np.float32)
        cv2.fillConvexPoly(mask, np.int32(np.round(d - [x, y])), 1.0, cv2.LINE_AA)
        y2, x2 = min(y + h, S), min(x + w, S)
        m = mask[:y2 - y, :x2 - x, None]
        region = out[y:y2, x:x2]
        out[y:y2, x:x2] = region * (1 - m) + warped[:y2 - y, :x2 - x] * m
    return out


def ease(t):
    return 0.5 - 0.5 * np.cos(np.pi * t)


a_img, b_img = cv2.imread("from.jpg"), cv2.imread("to.jpg")
a, pa = align(a_img, landmarks(a_img))
b, pb = align(b_img, landmarks(b_img))
pa, pb = with_border(np.clip(pa, 0, S - 1)), with_border(np.clip(pb, 0, S - 1))
tris = Delaunay((pa + pb) / 2).simplices

os.makedirs("frames", exist_ok=True)
for f in os.listdir("frames"):
    os.remove(os.path.join("frames", f))
frames = [0.0] * HOLD + [ease(i / (N - 1)) for i in range(N)] + [1.0] * HOLD
for i, t in enumerate(frames):
    pts = (1 - t) * pa + t * pb
    wa = a.astype(np.float32) if t == 0 else warp_to(a, pa, pts, tris)
    wb = b.astype(np.float32) if t == 1 else warp_to(b, pb, pts, tris)
    frame = (1 - t) * wa + t * wb
    cv2.imwrite(os.path.join("frames", f"f{i:03d}.png"), np.clip(frame, 0, 255).astype(np.uint8))
cv2.imwrite("aligned_from.png", a)
cv2.imwrite("aligned_to.png", b)
print(f"{len(frames)} frames at {S}x{S}, {len(tris)} triangles")
