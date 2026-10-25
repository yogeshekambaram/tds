"""Build a textured 3D head (GLB) from one photo using MediaPipe Face Mesh landmarks.

Steps: align the canonical face template to the detected 3D landmarks, undo the head rotation (frontalize),
symmetrize the poorly observed far side, texture each vertex from the photo (mirrored for hidden-side vertices),
then add a simple skull/ears/neck coloured from the photo and export a GLB under 2 MB.
"""
import json
import sys

import numpy as np
import trimesh
from PIL import Image

PHOTO = "photo.png"
OUT = sys.argv[1] if len(sys.argv) > 1 else "face.glb"
TEX = 1024


# ----------------------------------------------------------------------------- inputs
def load_canonical(path="canonical_face_model.obj"):
    v, faces = [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            v.append([float(x) for x in p[1:4]])
        elif p[0] == "f":
            faces.append([int(t.split("/")[0]) - 1 for t in p[1:4]])
    return np.array(v), np.array(faces)


img = Image.open(PHOTO).convert("RGB")
W, H = img.size
lm = np.load("landmarks.npy")[:468]
C, F = load_canonical()

# detected landmarks in pixels, converted to a right-handed y-up / z-toward-viewer frame
D = np.stack([lm[:, 0] * W, -lm[:, 1] * H, -lm[:, 2] * W], axis=1)


def similarity(src, dst):
    """Find s, R, t minimising |s R src + t - dst| (Umeyama)."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    U, S, Vt = np.linalg.svd(b.T @ a)
    d = np.sign(np.linalg.det(U @ Vt))
    R = U @ np.diag([1, 1, d]) @ Vt
    s = (S * [1, 1, d]).sum() / (a ** 2).sum()
    return s, R, md - s * R @ ms


s, R, t = similarity(C, D)
yaw = np.degrees(np.arctan2(R[0, 2], R[2, 2]))
print(f"scale {s:.2f} px/cm, head yaw {yaw:.1f} deg")

# frontalized person-specific shape, back in canonical units (cm)
P = ((D - t) @ R) / s

# mirror pairs from the symmetric canonical template
mirror = np.array([np.argmin(((C - C[i] * [-1, 1, 1]) ** 2).sum(1)) for i in range(len(C))])

# visibility in the photo: camera-space normals of the posed (unfrontalized) mesh
posed = trimesh.Trimesh(D, F, process=False)
vis = posed.vertex_normals[:, 2]  # +z faces the camera

# symmetrize: trust the better-seen side of each mirror pair
Pm = P[mirror] * [-1, 1, 1]
w = np.clip((vis - vis[mirror]) * 2 + 0.5, 0.15, 0.85)[:, None]
P = w * P + (1 - w) * Pm
P[:, 2] = 0.65 * P[:, 2] + 0.35 * C[:, 2]  # MediaPipe depth is weak: lean on the template profile

# --- texture baking -------------------------------------------------------------------------------
# UV layout: frontal projection of the frontalized shape (one UV per vertex, no seams).
# cylindrical around a vertical axis behind the face, so steep cheeks/temples get texture resolution
_zc = P[:, 2].min() - 0.45 * (P[:, 0].max() - P[:, 0].min())
_r = np.hypot(P[:, 0], P[:, 2] - _zc).mean()
xy = np.stack([np.arctan2(P[:, 0], P[:, 2] - _zc) * _r, P[:, 1]], 1)
lo, hi = xy.min(0), xy.max(0)
pad = 0.03 * (hi - lo).max()
lo, hi = lo - pad, hi + pad
uv = (xy - lo) / (hi - lo).max()
uv_px = np.stack([lm[:, 0] * W, lm[:, 1] * H], axis=1)  # where each vertex appears in the photo
photo = np.asarray(img, dtype=np.float32)


def photo_at(px, py):
    """Bilinear sample of the photo at float pixel coordinates."""
    x = np.clip(px, 0, W - 2); y = np.clip(py, 0, H - 2)
    x0 = x.astype(int); y0 = y.astype(int); fx = (x - x0)[:, None]; fy = (y - y0)[:, None]
    return (photo[y0, x0] * (1 - fx) * (1 - fy) + photo[y0, x0 + 1] * fx * (1 - fy)
            + photo[y0 + 1, x0] * (1 - fx) * fy + photo[y0 + 1, x0 + 1] * fx * fy)


# visibility weight per vertex: prefer the directly observed pixel, fall back smoothly to the mirror side
wv = np.clip((vis - vis[mirror]) * 3.0 + 0.5, 0.0, 1.0)
wv = np.where(vis < 0.05, 0.0, wv)
tex_arr = np.zeros((TEX, TEX, 3), np.float32)
filled = np.zeros((TEX, TEX), bool)
U = uv * (TEX - 1)
for (i, j, k) in F:
    xs, ys = U[[i, j, k], 0], (1 - uv[[i, j, k], 1]) * (TEX - 1)
    x0, x1 = int(np.floor(xs.min())), int(np.ceil(xs.max()))
    y0, y1 = int(np.floor(ys.min())), int(np.ceil(ys.max()))
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
    d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
    if abs(d) < 1e-9:
        continue
    b0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
    b1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
    b2 = 1 - b0 - b1
    m = (b0 >= -0.02) & (b1 >= -0.02) & (b2 >= -0.02)
    if not m.any():
        continue
    B = np.stack([b0[m], b1[m], b2[m]], 1)
    tri, mtri = np.array([i, j, k]), mirror[[i, j, k]]
    direct = photo_at(B @ uv_px[tri, 0], B @ uv_px[tri, 1])
    mirrored = photo_at(B @ uv_px[mtri, 0], B @ uv_px[mtri, 1])
    w = (B @ wv[tri])[:, None]
    gy_m, gx_m = gy[m], gx[m]
    tex_arr[gy_m, gx_m] = w * direct + (1 - w) * mirrored
    filled[gy_m, gx_m] = True
# bleed edge colours outwards so mip/bilinear sampling never pulls in black
from scipy import ndimage
idx = ndimage.distance_transform_edt(~filled, return_distances=False, return_indices=True)
tex_arr = tex_arr[idx[0], idx[1]]
import io
_buf = io.BytesIO()
Image.fromarray(np.clip(tex_arr, 0, 255).astype(np.uint8)).save(_buf, "JPEG", quality=90)
tex = Image.open(io.BytesIO(_buf.getvalue()))
tex.save("texture_baked.jpg")
print("baked texture: mirror-weighted texels", float((1 - wv).mean().round(2)))


def sample(px, py, r=6):
    """Median colour of the photo around (px, py)."""
    a = np.array(img.crop((int(px - r), int(py - r), int(px + r), int(py + r))))
    return np.median(a.reshape(-1, 3), axis=0)


# colours for the generated head parts
def tex_at(vertex_ids):
    """Median baked-texture colour at the given mask vertices."""
    cols = [tex_arr[int((1 - uv[v, 1]) * (TEX - 1)), int(uv[v, 0] * (TEX - 1))] for v in vertex_ids]
    return np.median(np.array(cols), axis=0)


edge_ids = [10, 338, 297, 332, 284, 109, 67, 103, 54, 151]  # upper forehead ring
skin_forehead = tex_at(edge_ids)
skin = 0.5 * skin_forehead + 0.5 * tex_at([425, 205, 50, 280, 123, 352])  # forehead + cheeks
hair = np.median([sample(x, y, r=15) for x, y in [(1800, 420), (2000, 520), (2150, 700)]], axis=0)  # crown pixels (hand covers the forehead's top-left)
print("skin", skin.round(), "hair", hair.round())

face_mesh = trimesh.Trimesh(P, F, process=False,
                            visual=trimesh.visual.TextureVisuals(uv=uv, image=tex))
# consistent winding, facing outwards (+z): reversed triangles would show the skull through holes
trimesh.repair.fix_winding(face_mesh)
if face_mesh.face_normals[:, 2].mean() < 0:
    face_mesh.faces = face_mesh.faces[:, ::-1]
print("face triangles facing backwards:", int((face_mesh.face_normals[:, 2] < -0.2).sum()))


# ----------------------------------------------------------------------------- generated head parts
def ellipsoid(center, radii, n=48, color=(128, 128, 128), keep=None, subdivisions=4):
    m = trimesh.creation.icosphere(subdivisions=subdivisions)
    m.vertices = m.vertices * radii + center
    if keep is not None:
        keep(m)
    m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=np.tile([*color, 255], (len(m.vertices), 1)).astype(np.uint8))
    return m


xmin, xmax = P[:, 0].min(), P[:, 0].max()
ymin, ymax = P[:, 1].min(), P[:, 1].max()
zmin, zmax = P[:, 2].min(), P[:, 2].max()
fw, fh = xmax - xmin, ymax - ymin
brow_y = P[[9, 151], 1].mean()
ear_y = P[[234, 454], 1].mean()

parts = [face_mesh]
from scipy.spatial import cKDTree
_mask_xy = cKDTree(P[:, :2])


from scipy.interpolate import LinearNDInterpolator
_mask_depth = LinearNDInterpolator(P[:, :2], P[:, 2])  # exact mask surface depth inside its outline


def push_behind(m, margin=0.35):
    """Move any generated vertex that pokes through the face mask to just behind it (keeps parts closed)."""
    zs = _mask_depth(m.vertices[:, :2])
    inside = ~np.isnan(zs)
    over = inside & (m.vertices[:, 2] > np.nan_to_num(zs) - margin)
    v = m.vertices.copy()
    v[over, 2] = zs[over] - margin
    m.vertices = v
    return m


depth = 0.95 * fw  # front-to-back head depth (cm), typical adult proportion
# skull: hair-coloured ellipsoid, its front just behind the forehead/temples
skull_c = np.array([0.0, brow_y + 0.06 * fh, zmax - 0.56 * depth])
skull_r = np.array([0.53 * fw, 0.62 * fh, 0.54 * depth])
skull = ellipsoid(skull_c, skull_r, color=tuple(int(c) for c in hair), keep=push_behind, subdivisions=5)
_hairline = ymax + 0.07 * fh  # high forehead: hair starts a little above the mask's top edge
_sv = skull.vertices
_front = (_sv[:, 2] - skull_c[2]) / skull_r[2]
_skin_band = (_sv[:, 1] < _hairline - 0.10 * fh * np.abs(_sv[:, 0] / skull_r[0]) ** 2) & (_front > 0.15)
_t = np.clip((_hairline - _sv[:, 1]) / (0.05 * fh), 0, 1)[:, None]
_edge = np.unique(face_mesh.edges_sorted[trimesh.grouping.group_rows(face_mesh.edges_sorted, require_count=1)])
_edge_tree = cKDTree(P[_edge, :2])
_, _ni = _edge_tree.query(_sv[:, :2])
_edge_cols = np.array([tex_at([v]) for v in _edge])
_band_col = 0.6 * _edge_cols[_ni] + 0.4 * skin_forehead
_cols = np.where(_skin_band[:, None], _t * _band_col + (1 - _t) * hair, hair)
skull.visual = trimesh.visual.ColorVisuals(skull, vertex_colors=np.hstack([_cols, np.full((len(_sv), 1), 255)]).astype(np.uint8))
parts.append(skull)
# lower back of head / jaw fill in skin tone
jaw_c = np.array([0.0, ear_y - 0.10 * fh, zmax - 0.66 * depth])
parts.append(ellipsoid(jaw_c, np.array([0.46 * fw, 0.38 * fh, 0.42 * depth]),
                       color=tuple(int(c) for c in skin), keep=push_behind))
# ears, at the mask's side edges, slightly behind
for side, idx in ((-1, 234), (1, 454)):
    ex = P[idx, 0] + side * 0.02 * fw
    parts.append(ellipsoid(np.array([ex, ear_y + 0.02 * fh, P[idx, 2] - 0.10 * depth]),
                           np.array([0.05 * fw, 0.16 * fh, 0.10 * depth]), color=tuple(int(c * 0.9) for c in skin)))
# neck: upright tapered tube, its top hidden inside the jaw/skull
neck_top, neck_bot = ear_y - 0.18 * fh, ymin - 0.30 * fh
neck = trimesh.creation.cylinder(radius=1.0, height=1.0, sections=48)  # along Z
neck.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))  # Z -> Y
v = neck.vertices.copy()
t = v[:, 1] + 0.5  # 0 at bottom, 1 at top
r = (0.27 + (0.23 - 0.27) * t) * fw
v[:, 0] *= r
v[:, 2] *= r * 1.05
v[:, 1] = neck_bot + t * (neck_top - neck_bot)
v[:, 2] += zmax - 0.66 * depth
neck.vertices = v
push_behind(neck)
neck.visual = trimesh.visual.ColorVisuals(neck, vertex_colors=np.tile([*[int(c * 0.85) for c in skin], 255], (len(neck.vertices), 1)).astype(np.uint8))
parts.append(neck)

# suit shoulders, white collar, dark tie (as in the photo)
neck_z = zmax - 0.66 * depth
_ry = 0.42 * fh
_suit_c = np.array([0, neck_bot - _ry + 0.06 * fh, neck_z - 0.05 * depth])
suit = ellipsoid(_suit_c, np.array([1.30 * fw, _ry, 0.60 * depth]), color=(30, 30, 38),
                 keep=lambda m: m.update_faces(m.triangles_center[:, 1] > _suit_c[1] - 0.15 * _ry) or m.remove_unreferenced_vertices())
parts.append(suit)
collar = trimesh.creation.cylinder(radius=1.0, height=1.0, sections=48)
collar.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
cv = collar.vertices.copy()
ct = cv[:, 1] + 0.5
cv[:, 0] *= (0.30 - 0.02 * ct) * fw
cv[:, 2] *= (0.30 - 0.02 * ct) * fw * 1.05
cv[:, 1] = neck_bot + 0.02 * fh + ct * 0.12 * fh
cv[:, 2] += neck_z
collar.vertices = cv
collar.visual = trimesh.visual.ColorVisuals(collar, vertex_colors=np.tile([238, 236, 230, 255], (len(collar.vertices), 1)).astype(np.uint8))
parts.append(collar)
# tie: a slim box lying on the front of the shoulder dome, below the collar
_tz = _suit_c[2] + 0.60 * depth * np.sqrt(max(1e-6, 1 - ((neck_bot - 0.12 * fh - _suit_c[1]) / _ry) ** 2))
tie = trimesh.creation.box(extents=[0.10 * fw, 0.30 * fh, 0.05 * fw])
tie.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-28), [1, 0, 0]))
tie.apply_translation([0, neck_bot - 0.10 * fh, max(_tz, neck_z + 0.30 * fw) + 0.01 * fw])
tie.visual = trimesh.visual.ColorVisuals(tie, vertex_colors=np.tile([22, 22, 26, 255], (len(tie.vertices), 1)).astype(np.uint8))
parts.append(tie)


# --- 3D browline glasses (heavy dark upper rim, thin metal lower rim, bridge, temples) -------------
def tube(points, radius, color, closed=False):
    pts = list(points) + ([points[0]] if closed else [])
    segs = []
    for a, b in zip(pts[:-1], pts[1:]):
        if np.linalg.norm(b - a) < 1e-6:
            continue
        c = trimesh.creation.cylinder(radius=radius, segment=[a, b], sections=10)
        segs.append(c)
        segs.append(trimesh.creation.icosphere(subdivisions=1, radius=radius).apply_translation(b))
    m = trimesh.util.concatenate(segs)
    m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=np.tile([*color, 255], (len(m.vertices), 1)).astype(np.uint8))
    return m


frame_dark, frame_metal = (24, 18, 16), (176, 150, 104)
eyes = {"r": [33, 133, 159, 145, 160, 144, 158, 153], "l": [362, 263, 386, 374, 385, 380, 387, 373]}
lens_z = P[168, 2] - 0.15  # lens plane just in front of the eyes, at the nose-bridge depth
rims = {}
for k, idx in eyes.items():
    E = P[idx]
    cxy = E[:, :2].mean(0) + np.array([0, 0.02 * fh])
    ew = np.ptp(E[:, 0])
    a, b = 1.05 * ew, 0.62 * ew  # half-width / half-height of the lens
    ang = np.linspace(0, 2 * np.pi, 41)[:-1]
    c_, s_ = np.cos(ang), np.sin(ang)
    e = 2.0 / 3.2  # superellipse: flat top like browline frames
    ring = np.stack([cxy[0] + a * np.sign(c_) * np.abs(c_) ** e, cxy[1] + b * np.sign(s_) * np.abs(s_) ** e,
                     np.full_like(ang, lens_z)], 1)
    # curve the lens plane back towards the temples
    ring[:, 2] -= 0.25 * ((ring[:, 0] - P[168, 0]) / fw) ** 2 * fw
    rims[k] = ring
    upper = ring[(np.sin(ang) > 0.05)]
    upper = upper[np.argsort(np.arctan2(upper[:, 1] - cxy[1], upper[:, 0] - cxy[0]))]
    parts.append(tube(upper, 0.28, frame_dark))  # browline
    parts.append(tube(ring, 0.07, frame_metal, closed=True))  # thin metal lower rim
# bridge between the inner rim tops
inner_r = rims["r"][np.argmax(rims["r"][:, 0])]
inner_l = rims["l"][np.argmin(rims["l"][:, 0])]
parts.append(tube([inner_r, P[168] + [0, 0.15, 0.25], inner_l], 0.12, frame_metal))
# temple arms from the outer rim edge back to the ears
for k, ear in (("r", 234), ("l", 454)):
    ring = rims[k]
    outer = ring[np.argmax(np.abs(ring[:, 0] - P[168, 0]))]
    hinge = outer + [0, 0.1 * fh * 0.1, 0]
    ear_pt = np.array([P[ear, 0] * 1.02, ear_y + 0.10 * fh, P[ear, 2] - 0.12 * depth])
    parts.append(tube([hinge, (hinge + ear_pt) / 2 + [0, 0.01 * fh, 0], ear_pt], 0.16, frame_dark))

scene = trimesh.Scene()
for i, m in enumerate(parts):
    scene.add_geometry(m, node_name=f"part{i}", geom_name=f"part{i}")
# metres, centred on the face
scene.apply_translation(-np.array([0, brow_y, (zmin + zmax) / 2]))
scene.apply_scale(0.01)
glb = scene.export(file_type="glb")
open(OUT, "wb").write(glb)
json.dump({"yaw": float(yaw), "skin": skin.tolist(), "hair": hair.tolist()}, open("build_info.json", "w"))
print(f"wrote {OUT}: {len(glb):,} bytes")
