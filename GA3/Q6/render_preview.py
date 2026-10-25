"""Render a GLB from several yaw angles with a tiny NumPy z-buffer rasterizer (textures + vertex colours)."""
import sys

import numpy as np
import trimesh
from PIL import Image

SIZE = 360


def load(path):
    scene = trimesh.load(path, force="scene")
    meshes = []
    for name in scene.graph.nodes_geometry:
        T, gname = scene.graph[name]
        m = scene.geometry[gname].copy()
        m.apply_transform(T)
        meshes.append(m)
    return meshes


def colours_and_uv(m):
    vis = m.visual
    if isinstance(vis, trimesh.visual.TextureVisuals) and vis.uv is not None:
        img = vis.material.image if hasattr(vis.material, "image") and vis.material.image is not None else vis.material.baseColorTexture
        return None, np.asarray(vis.uv), np.asarray(img.convert("RGB"), dtype=np.float32)
    vc = vis.to_color().vertex_colors if not hasattr(vis, "vertex_colors") else vis.vertex_colors
    return np.asarray(vc)[:, :3].astype(np.float32), None, None


def render(meshes, yaw_deg, pitch_deg=0):
    a, b = np.radians(yaw_deg), np.radians(pitch_deg)
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    R = Rx @ Ry
    allv = np.vstack([m.vertices for m in meshes])
    ctr = (allv.max(0) + allv.min(0)) / 2
    span = (allv.max(0) - allv.min(0)).max()
    img = np.full((SIZE, SIZE, 3), 235.0)
    zb = np.full((SIZE, SIZE), -np.inf)
    light = np.array([0.3, 0.4, 1.0]); light /= np.linalg.norm(light)
    for m in meshes:
        V = (m.vertices - ctr) @ R.T
        px = (V[:, 0] / span * 0.9 + 0.5) * SIZE
        py = (0.5 - V[:, 1] / span * 0.9) * SIZE
        vc, uv, tex = colours_and_uv(m)
        fn = m.face_normals @ R.T
        for fi, (i, j, k) in enumerate(m.faces):
            if fn[fi, 2] <= 0:
                continue
            xs, ys = px[[i, j, k]], py[[i, j, k]]
            x0, x1 = int(max(0, np.floor(xs.min()))), int(min(SIZE - 1, np.ceil(xs.max())))
            y0, y1 = int(max(0, np.floor(ys.min()))), int(min(SIZE - 1, np.ceil(ys.max())))
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-9:
                continue
            w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not inside.any():
                continue
            z = w0 * V[i, 2] + w1 * V[j, 2] + w2 * V[k, 2]
            yy, xx = gy[inside].astype(int), gx[inside].astype(int)
            zz = z[inside]
            closer = zz > zb[yy, xx]
            if not closer.any():
                continue
            yy, xx, zz = yy[closer], xx[closer], zz[closer]
            ww = np.stack([w0[inside][closer], w1[inside][closer], w2[inside][closer]], 1)
            shade = 0.55 + 0.45 * max(0.0, float(fn[fi] @ light))
            if tex is not None:
                u = ww @ uv[[i, j, k], 0]
                v = ww @ uv[[i, j, k], 1]
                th, tw = tex.shape[:2]
                col = tex[np.clip(((1 - v) * th).astype(int), 0, th - 1), np.clip((u * tw).astype(int), 0, tw - 1)]
                col = col * (0.8 + 0.2 * shade)
            else:
                col = (ww @ vc[[i, j, k]]) * shade
            zb[yy, xx] = zz
            img[yy, xx] = col
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


if __name__ == "__main__":
    path, out = sys.argv[1], sys.argv[2]
    meshes = load(path)
    views = [(-75, 0), (-40, 0), (0, 0), (40, 0), (75, 0), (0, 25)]
    sheet = Image.new("RGB", (SIZE * 4, SIZE * 2), (255, 255, 255))
    photo = Image.open("photo.png").convert("RGB")
    photo.thumbnail((SIZE * 2, SIZE * 2))
    sheet.paste(photo.resize((int(photo.width * SIZE * 2 / photo.height), SIZE * 2)), (0, 0))
    for n, (yw, pt) in enumerate(views):
        r, c = divmod(n, 3)
        sheet.paste(render(meshes, yw, pt), (int(SIZE * 1.0) + c * SIZE, r * SIZE))
    sheet.save(out)
    print("saved", out)
