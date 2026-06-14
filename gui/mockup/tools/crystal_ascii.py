"""
crystal_ascii.py -- GOSE Core ASCII crystal generator (v3 -- real mesh)
========================================================================
Reads the real GOSE crystal OBJ point cloud (tripo_convert_*.obj),
renders 60 rotating coloured ASCII frames, and emits crystal-frames.js.

Vertex format in OBJ: `v x y z r g b`  (position + baked linear RGB 0..1)
Only `v ` lines are read; `vn`, `vt`, `f` are skipped.

Usage:
    py -3.11 gui/mockup/tools/crystal_ascii.py

Requirements: Python 3 stdlib + numpy
"""

import math
import os
import sys
import random

import numpy as np

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

OBJ_PATH = r"D:\Wren\scratch\gose_crystal\tripo_convert_4bea7571-171c-4a8f-8649-5cf3b732cbe6.obj"

GRID_COLS  = 96
GRID_ROWS  = 58
N_FRAMES   = 60          # 60 × 6° = 360° full rotation
MAX_POINTS = 500_000     # subsample cap for performance

# char ramp: index 0 = sparse/dark, last = dense/bright. NOTE: '+' is deliberately NOT
# in the ramp so the ONLY '+' in a frame is the gated core marker (front/back faces only).
RAMP = " .:-=o*#%@"

CYAN_EDGE  = "#9fe4ff"
CORE_CYAN  = "#9fe4ff"
CORE_WHITE = "#ffffff"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rgb_to_hex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(r))),
        max(0, min(255, int(g))),
        max(0, min(255, int(b))),
    )

def lerp_color_hex(c1, c2, t):
    """Linear-interpolate two '#rrggbb' colors."""
    def _parse(h):
        h = h.lstrip('#')
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r1, g1, b1 = _parse(c1)
    r2, g2, b2 = _parse(c2)
    return rgb_to_hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)

# ---------------------------------------------------------------------------
# Load OBJ (vertex-only, with colour)
# ---------------------------------------------------------------------------

def load_obj(path):
    """Read only 'v x y z r g b' lines.  Returns (pos, col) as float32 arrays."""
    positions = []
    colors    = []
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line.startswith('v '):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            if len(parts) >= 7:
                r, g, b = float(parts[4]), float(parts[5]), float(parts[6])
            else:
                r, g, b = 0.5, 0.5, 1.0
            positions.append((x, y, z))
            colors.append((r, g, b))

    pts = np.array(positions, dtype=np.float32)
    col = np.array(colors,    dtype=np.float32)
    return pts, col

# ---------------------------------------------------------------------------
# Subsample
# ---------------------------------------------------------------------------

def subsample(pts, col, n):
    if len(pts) <= n:
        return pts, col
    idx = np.random.choice(len(pts), n, replace=False)
    return pts[idx], col[idx]

# ---------------------------------------------------------------------------
# Render one frame
# ---------------------------------------------------------------------------

def render_frame(frame_idx, pts_centered, col, scale_h, scale_v, N_FRAMES):
    """
    pts_centered: (N,3) float32, already centered on bbox midpoint.
    scale_h, scale_v: mapping from world units to grid cells.
    Returns (shade_buf, color_buf) as list-of-lists.
    """
    angle = frame_idx * (2.0 * math.pi / N_FRAMES)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    # Rotate about Y axis
    x_rot = cos_a * pts_centered[:, 0] - sin_a * pts_centered[:, 2]
    y_rot = pts_centered[:, 1]
    z_rot = sin_a * pts_centered[:, 0] + cos_a * pts_centered[:, 2]

    # Orthographic project to grid indices
    # horizontal: x_rot → col; vertical: y_rot → row (invert Y so +Y is up)
    col_f = x_rot * scale_h + GRID_COLS / 2.0
    row_f = -y_rot * scale_v + GRID_ROWS / 2.0

    # Valid points inside grid
    ci = np.round(col_f).astype(np.int32)
    ri = np.round(row_f).astype(np.int32)
    valid = (ci >= 0) & (ci < GRID_COLS) & (ri >= 0) & (ri < GRID_ROWS)

    ci = ci[valid]
    ri = ri[valid]
    z_v = z_rot[valid]
    col_v = col[valid]   # (M,3) float RGB

    # Flatten grid index
    flat = ri * GRID_COLS + ci   # (M,)

    # Z-buffer: sort back-to-front (largest z first → camera at -z sees smallest z)
    order = np.argsort(z_v)[::-1]  # descending z → back first
    flat_s  = flat[order]
    z_s     = z_v[order]
    col_s   = col_v[order]   # (M,3)

    # Scatter: later (smaller z = closer) overwrites
    zbuf    = np.full(GRID_ROWS * GRID_COLS, np.inf, dtype=np.float32)
    r_buf   = np.zeros(GRID_ROWS * GRID_COLS, dtype=np.float32)
    g_buf   = np.zeros(GRID_ROWS * GRID_COLS, dtype=np.float32)
    b_buf   = np.zeros(GRID_ROWS * GRID_COLS, dtype=np.float32)
    filled  = np.zeros(GRID_ROWS * GRID_COLS, dtype=bool)

    zbuf[flat_s]   = z_s
    r_buf[flat_s]  = col_s[:, 0]
    g_buf[flat_s]  = col_s[:, 1]
    b_buf[flat_s]  = col_s[:, 2]
    filled[flat_s] = True

    # Also track point density per cell (for char ramp selection)
    density = np.zeros(GRID_ROWS * GRID_COLS, dtype=np.int32)
    np.add.at(density, flat_s, 1)

    # Reshape
    zbuf_2d    = zbuf.reshape(GRID_ROWS, GRID_COLS)
    r_2d       = r_buf.reshape(GRID_ROWS, GRID_COLS)
    g_2d       = g_buf.reshape(GRID_ROWS, GRID_COLS)
    b_2d       = b_buf.reshape(GRID_ROWS, GRID_COLS)
    filled_2d  = filled.reshape(GRID_ROWS, GRID_COLS)
    density_2d = density.reshape(GRID_ROWS, GRID_COLS)

    # Depth normalisation for char selection
    z_filled = zbuf_2d[filled_2d]
    if len(z_filled) > 0:
        z_min = z_filled.min()
        z_max = z_filled.max()
        z_range = z_max - z_min if z_max > z_min else 1.0
    else:
        z_min, z_range = 0.0, 1.0

    # Density normalisation
    d_max = density_2d.max() if density_2d.max() > 0 else 1

    # Build shade and color buffers (lists of lists)
    shade_buf = [[' '] * GRID_COLS for _ in range(GRID_ROWS)]
    color_buf = [['#888888'] * GRID_COLS for _ in range(GRID_ROWS)]

    RAMP_LEN = len(RAMP) - 1

    for row in range(GRID_ROWS):
        for col_idx in range(GRID_COLS):
            if not filled_2d[row, col_idx]:
                continue
            # Depth 0=front 1=back
            depth_norm = (zbuf_2d[row, col_idx] - z_min) / z_range
            # Density 0..1
            dens_norm = density_2d[row, col_idx] / d_max
            # Combined: closer + denser → denser char
            combined = 0.5 * (1.0 - depth_norm) + 0.5 * dens_norm
            combined = max(0.0, min(1.0, combined))
            # Never use index 0 (space) for filled cells
            char_idx = max(1, int(combined * RAMP_LEN))
            shade_buf[row][col_idx] = RAMP[char_idx]

            # Brighten baked RGB by 1.2×, clamp to 255
            rv = min(255, int(r_2d[row, col_idx] * 255 * 1.2))
            gv = min(255, int(g_2d[row, col_idx] * 255 * 1.2))
            bv = min(255, int(b_2d[row, col_idx] * 255 * 1.2))
            color_buf[row][col_idx] = rgb_to_hex(rv, gv, bv)

    # --- Silhouette edge detection ---
    for row in range(GRID_ROWS):
        for col_idx in range(GRID_COLS):
            if not filled_2d[row, col_idx]:
                continue
            is_edge = False
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = row + dr, col_idx + dc
                    if nr < 0 or nr >= GRID_ROWS or nc < 0 or nc >= GRID_COLS:
                        is_edge = True
                        break
                    if not filled_2d[nr, nc]:
                        is_edge = True
                        break
                if is_edge:
                    break
            if is_edge:
                color_buf[row][col_idx] = CYAN_EDGE
                shade_buf[row][col_idx] = '#'

    # --- Core: a STABLE glowing "+" at the crystal's heart (the GOSE Core) ---
    # The real GOSE crystal has a white core+plus at its centre. Pin it to the grid
    # centre (= the rotation axis + the bbox mid, i.e. the gem's heart) so it stays put
    # instead of wandering with the per-frame centroid (that wander read as a "weird +").
    # A soft cyan lift on the surrounding facet cells makes it glow like a heart.
    # The core+plus sits on the FRONT (and back) face only. Show it when the broad face
    # is toward the camera (|sin(angle)| high) and hide it when the crystal turns edge-on
    # (left/right profile, angle ~0/180). Zeke 2026-06-14.
    face = abs(math.sin(angle))
    if face > 0.6:
        pulse = 0.5 + 0.5 * math.sin(frame_idx / N_FRAMES * 2.0 * math.pi)
        core_color = lerp_color_hex(CORE_CYAN, CORE_WHITE, pulse)
        cr = GRID_ROWS // 2
        cc = GRID_COLS // 2
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                r2, c2 = cr + dr, cc + dc
                if 0 <= r2 < GRID_ROWS and 0 <= c2 < GRID_COLS and filled_2d[r2, c2]:
                    color_buf[r2][c2] = lerp_color_hex(color_buf[r2][c2], CORE_CYAN, (0.15 + 0.45 * pulse) * face)
        if 0 <= cr < GRID_ROWS and 0 <= cc < GRID_COLS:
            shade_buf[cr][cc] = '+'
            color_buf[cr][cc] = core_color

    return shade_buf, color_buf

# ---------------------------------------------------------------------------
# Build HTML string from buffers (run-length-encoded spans)
# ---------------------------------------------------------------------------

def buf_to_html(shade_buf, color_buf):
    lines = []
    for row in range(GRID_ROWS):
        row_html = ''
        run_char  = ''
        run_color = ''
        for col_idx in range(GRID_COLS):
            ch  = shade_buf[row][col_idx]
            col = color_buf[row][col_idx]
            if ch == ' ':
                if run_char:
                    row_html += '<span style="color:{}">{}</span>'.format(run_color, run_char)
                    run_char = ''; run_color = ''
                row_html += ' '
            elif col == run_color:
                run_char += ch
            else:
                if run_char:
                    row_html += '<span style="color:{}">{}</span>'.format(run_color, run_char)
                run_char = ch; run_color = col
        if run_char:
            row_html += '<span style="color:{}">{}</span>'.format(run_color, run_char)
        lines.append(row_html)
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Render the frames to an animated GIF (generation-time, PIL) so the full spin
# can be previewed/shared. Mirrors the terminal look: monospace, dark bg.
# ---------------------------------------------------------------------------

def render_gif(frame_bufs, out_path, cell_w=8, cell_h=16, bg=(7, 7, 15), duration_ms=80):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for fp in (r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\lucon.ttf",
               r"C:\Windows\Fonts\cour.ttf"):
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, 15)
            break
    if font is None:
        font = ImageFont.load_default()

    W, H = GRID_COLS * cell_w, GRID_ROWS * cell_h
    imgs = []
    for sb, cb in frame_bufs:
        im = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(im)
        for row in range(GRID_ROWS):
            for col_idx in range(GRID_COLS):
                ch = sb[row][col_idx]
                if ch == ' ':
                    continue
                d.text((col_idx * cell_w, row * cell_h), ch,
                       fill=cb[row][col_idx], font=font)
        imgs.append(im)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:],
                 duration=duration_ms, loop=0, optimize=True, disposal=2)
    print(f"Written GIF: {out_path}  ({os.path.getsize(out_path)//1024} KB, {W}x{H}, {len(imgs)} frames)")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    np.random.seed(42)

    # --- Load mesh ---
    print(f"Loading OBJ: {OBJ_PATH}")
    sys.stdout.flush()
    pts, col = load_obj(OBJ_PATH)
    n_total = len(pts)
    print(f"Vertices parsed: {n_total:,}")

    # Bounding box
    bbox_min = pts.min(axis=0)
    bbox_max = pts.max(axis=0)
    extents  = bbox_max - bbox_min
    print(f"Bbox X: {bbox_min[0]:.4f} .. {bbox_max[0]:.4f}  (range {extents[0]:.4f})")
    print(f"Bbox Y: {bbox_min[1]:.4f} .. {bbox_max[1]:.4f}  (range {extents[1]:.4f})")
    print(f"Bbox Z: {bbox_min[2]:.4f} .. {bbox_max[2]:.4f}  (range {extents[2]:.4f})")

    # Detect vertical axis (largest extent)
    axis_names = ['X', 'Y', 'Z']
    vert_axis  = int(np.argmax(extents))
    print(f"Vertical axis detected: {axis_names[vert_axis]}")

    # Center on bbox midpoint
    midpoint   = (bbox_min + bbox_max) / 2.0
    pts_c      = pts - midpoint

    # Compute scale factors
    # Horizontal axis is X (index 0), vertical is Y (index 1)
    # Account for terminal char aspect ratio (~2.0 tall:wide)
    CHAR_ASPECT = 2.0   # cell height / cell width
    margin_cols = 4
    margin_rows = 4
    avail_cols  = GRID_COLS - margin_cols
    avail_rows  = GRID_ROWS - margin_rows

    # Horizontal extent must be the WIDEST of the two non-vertical axes (X, Z):
    # as the gem rotates about Y, the projected width ranges up to max(ext_x, ext_z),
    # so size to that to keep every frame in-bounds (and not squish it thin).
    ext_horiz = max(extents[0], extents[2])
    ext_y     = extents[1]   # vertical world extent (Y)

    # A char cell is ~CHAR_ASPECT× taller than wide, so vertical world units map to
    # FEWER rows per unit than horizontal units map to cols:  scale_v = scale_h / CHAR_ASPECT.
    # Then visual aspect (rows*cellH):(cols*cellW) == world aspect ext_y:ext_horiz.
    # Constraints: ext_horiz*scale_h <= avail_cols ; ext_y*scale_v <= avail_rows.
    scale_h_cap_cols = avail_cols / ext_horiz if ext_horiz > 0 else 1.0
    scale_h_cap_rows = (avail_rows * CHAR_ASPECT) / ext_y if ext_y > 0 else 1.0

    scale_h = min(scale_h_cap_cols, scale_h_cap_rows)
    scale_v = scale_h / CHAR_ASPECT

    print(f"Scale: h={scale_h:.2f} cols/unit, v={scale_v:.2f} rows/unit")

    # --- Subsample ---
    if n_total > MAX_POINTS:
        pts_s, col_s = subsample(pts_c, col, MAX_POINTS)
        print(f"Subsampled to {MAX_POINTS:,} points")
    else:
        pts_s, col_s = pts_c, col
        print(f"Using all {n_total:,} points (under subsample cap)")

    # --- Render frames ---
    frame_html = []
    frame_bufs = []
    print(f"Rendering {N_FRAMES} frames ({360//N_FRAMES} deg per frame) ...")
    sys.stdout.flush()

    for i in range(N_FRAMES):
        if i % 10 == 0:
            print(f"  frame {i}/{N_FRAMES} ...", flush=True)
        sb, cb = render_frame(i, pts_s, col_s, scale_h, scale_v, N_FRAMES)
        frame_bufs.append((sb, cb))
        frame_html.append(buf_to_html(sb, cb))

    # --- Verify dimensions ---
    for fi, fh in enumerate(frame_html):
        lines = fh.split('\n')
        assert len(lines) == GRID_ROWS, \
            f"Frame {fi}: expected {GRID_ROWS} lines, got {len(lines)}"

    print(f"All {N_FRAMES} frames verified: {GRID_COLS} cols × {GRID_ROWS} rows")

    # --- Emit crystal-frames.js ---
    out_path = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     '..', 'crystal-frames.js'))

    with open(out_path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('// Auto-generated by crystal_ascii.py v3 (real mesh) -- do not edit\n')
        fh.write(f'// Real GOSE crystal OBJ: {n_total:,} vertices, subsampled to {len(pts_s):,}\n')
        fh.write(f'// {N_FRAMES} frames, {GRID_COLS} cols x {GRID_ROWS} rows, '
                 f'{360 // N_FRAMES} deg/frame, 360 deg loop\n')
        fh.write('window.CRYSTAL_FRAMES = [\n')
        for idx, frame in enumerate(frame_html):
            esc   = frame.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
            comma = ',' if idx < len(frame_html) - 1 else ''
            fh.write('  `' + esc + '`' + comma + '\n')
        fh.write('];\n')

    size_kb = os.path.getsize(out_path) // 1024
    print(f"Written: {out_path}  ({size_kb} KB)")
    print(f"\nSummary:")
    print(f"  Vertices parsed   : {n_total:,}")
    print(f"  Points used       : {len(pts_s):,}")
    print(f"  Vertical axis     : {axis_names[vert_axis]}")
    print(f"  Grid              : {GRID_COLS} cols x {GRID_ROWS} rows")
    print(f"  Frames            : {N_FRAMES}  ({360 // N_FRAMES} deg/frame)")
    print(f"  crystal-frames.js : {size_kb} KB")

    # --- Also emit an animated GIF of the full spin (for preview/sharing) ---
    gif_path = r"D:\Wren\scratch\gose_crystal\crystal-spin.gif"
    try:
        render_gif(frame_bufs, gif_path)
    except Exception as e:
        print(f"GIF render skipped: {e!r}")


if __name__ == '__main__':
    main()
