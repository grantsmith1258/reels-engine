"""Satellite basemap for the Reels Engine.

Builds real, cloud-free satellite imagery with terrain relief, in the engine's map space
("merc-degrees": x = longitude, y = Mercator northing in degrees), at a few resolutions.

Sources (all open data, fetched over HTTPS from AWS Open Data; credit in the video's Sources):
  * Sentinel-2 L2A 120 m mosaic 2020, Sentinel Hub (CC BY 4.0)    -> wide and regional views
  * ESA WorldCover 2021 Sentinel-2 10 m composites (CC BY 4.0)    -> close views (towns, rivers)
  * Terrain Tiles (Mapzen/AWS terrarium, public sources)          -> hillshade relief + ocean depth
Attribution line: "Imagery: Contains modified Copernicus Sentinel data (2020-2021), Sentinel Hub /
ESA WorldCover. Terrain: AWS Terrain Tiles. Borders: Natural Earth."

Levels are cached under data/sat_cache (tiles) and build/<storyboard>/base_*.npz (mosaics).
"""
import io, math, os, json, hashlib, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("VSI_CACHE", "TRUE")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "data", "sat_cache")
K = 6378137 * math.pi / 180          # metres (EPSG:3857) per merc-degree
MOSAIC = "https://sentinel-s2-l2a-mosaic-120.s3.amazonaws.com/2020"
ESA = "https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com/rgbnir/2021"
TERR = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium"
LEVELS = [16, 64, 256, 1024, 4096]   # pixels per merc-degree
BANDS = "CDEFGHJKLMNPQRSTUVWX"        # UTM latitude bands, 8 degrees each from -80


def merc_y(lat):
    lat = max(-85, min(85, lat))
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def _get(url, path):
    if os.path.exists(path):
        return open(path, "rb").read() or None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            data = r.read()
    except Exception as e:
        if getattr(e, "code", None) in (403, 404):
            data = b""
        else:
            raise
    open(path, "wb").write(data)
    return data or None


# ---------------------------------------------------------------- terrain (terrarium tiles, web mercator)
def terrain(x0, x1, y0, y1, res):
    """Elevation (m) on the level grid; bathymetry is negative."""
    w, h = int(round((x1 - x0) * res)), int(round((y1 - y0) * res))
    z = int(max(0, min(13, round(math.log2(res * 360 / 256)))))
    n = 2 ** z
    tx0 = int(math.floor((x0 + 180) / 360 * n)); tx1 = int(math.floor((x1 + 180) / 360 * n - 1e-9))
    ty0 = int(math.floor((1 - merc_y(inv_merc_y(y1)) / 180) / 2 * n)); ty1 = int(math.floor((1 - y0 / 180) / 2 * n - 1e-9))
    ty0, ty1 = max(0, ty0), min(n - 1, ty1)
    jobs = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]

    def fetch(j):
        tx, ty = j
        d = _get(f"{TERR}/{z}/{tx % n}/{ty}.png", os.path.join(CACHE, "terr", str(z), f"{tx % n}_{ty}.png"))
        if not d:
            return j, np.zeros((256, 256), np.float32)
        from PIL import Image
        a = np.asarray(Image.open(io.BytesIO(d)).convert("RGB")).astype(np.float32)
        return j, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    big = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    with ThreadPoolExecutor(16) as ex:
        for (tx, ty), a in ex.map(fetch, jobs):
            big[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = a
    # big covers merc x [tx0/n*360-180 ...], merc y top = 180*(1-2*ty0/n)
    bx0 = tx0 / n * 360 - 180; by1 = 180 * (1 - 2 * ty0 / n); s = 256 * n / 360
    import cv2
    M = np.array([[s / res, 0, (x0 - bx0) * s], [0, s / res, (by1 - y1) * s]], np.float32)
    return cv2.warpAffine(big, M, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


# ---------------------------------------------------------------- imagery
def _reproject_into(dst, url, bands, overview, x0, y1, res):
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    try:
        kw = {"overview_level": overview} if overview is not None and overview >= 0 else {}
        with rasterio.open("/vsicurl/" + url, **kw) as d:
            src = d.read(bands).astype(np.float32)
            st, sc = d.transform, d.crs
    except Exception:
        return False
    tr = from_origin(x0 * K, y1 * K, K / res, K / res)
    for i in range(len(bands)):
        out = np.zeros(dst.shape[1:], np.float32)
        reproject(src[i], out, src_transform=st, src_crs=sc, dst_transform=tr, dst_crs="EPSG:3857",
                  resampling=Resampling.bilinear, src_nodata=0, dst_nodata=0)
        m = out > 0
        dst[i][m] = out[m]
    return True


def imagery(x0, x1, y0, y1, res):
    """Reflectance x10000 (3, h, w) float32, 0 where no data."""
    w, h = int(round((x1 - x0) * res)), int(round((y1 - y0) * res))
    dst = np.zeros((3, h, w), np.float32)
    lat0, lat1 = inv_merc_y(y0), inv_merc_y(y1)
    jobs = []
    if res >= 700:   # ESA WorldCover 10 m composites, 1-degree tiles in EPSG:4326 (12000 px/deg)
        ov = max(-1, min(3, int(math.floor(math.log2(12000 / (res * 1.6)))) - 1))
        for la in range(int(math.floor(lat0)), int(math.floor(lat1)) + 1):
            for lo in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
                ns = f"N{la:02d}" if la >= 0 else f"S{-la:02d}"
                ew = f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}"
                jobs.append((f"{ESA}/{ns}/ESA_WorldCover_10m_2021_v200_{ns}{ew}_S2RGBNIR.tif", [1, 2, 3], ov))
    else:            # Sentinel-2 120 m mosaic, UTM grid zones (6 x 8 degrees), one file per band
        native = 111320 / 120          # ~px per degree
        ov = max(-1, min(4, int(math.floor(math.log2(native / (res * 1.3)))) - 1))
        for bi, b in enumerate(BANDS):
            bl0, bl1 = -80 + bi * 8, -80 + bi * 8 + (12 if b == "X" else 8)
            if bl1 <= lat0 or bl0 >= lat1:
                continue
            date = "7/12" if bl0 >= 0 else "1/11"
            for zn in range(1, 61):
                zl0 = -180 + (zn - 1) * 6
                if zl0 + 6 <= x0 or zl0 >= x1:
                    continue
                for band, k in (("B04", 0), ("B03", 1), ("B02", 2)):
                    jobs.append((f"{MOSAIC}/{date}/{zn:02d}{b}/{band}.tif", k, ov))
    tmp = {}

    def run(j):
        url, bands, ovl = j
        if isinstance(bands, int):
            one = np.zeros((1, h, w), np.float32)
            ok = _reproject_into(one, url, [1], ovl, x0, y1, res)
            return j, (one if ok else None)
        part = np.zeros((3, h, w), np.float32)
        ok = _reproject_into(part, url, bands, ovl, x0, y1, res)
        return j, (part if ok else None)
    with ThreadPoolExecutor(24) as ex:
        for (url, bands, ovl), part in ex.map(run, jobs):
            if part is None:
                continue
            if isinstance(bands, int):
                m = part[0] > 0
                dst[bands][m] = part[0][m]
            else:
                m = part[0] > 0
                for i in range(3):
                    dst[i][m] = part[i][m]
    return dst


def colorize(refl, elev, res, lat_mid, gain=2400.0, lat_rows=None):
    """Natural-colour image with terrain shading and a depth-tinted ocean. Returns uint8 RGB (h, w, 3)."""
    import cv2
    have = (refl[0] > 0) & (refl[1] > 0) & (refl[2] > 0)
    # open sea: the mosaics' water pixels are noisy, so paint depth-tinted water instead
    # (only where the pixel is not land-coloured, so land below sea level stays land)
    sea = (elev < -8) & (refl[2] >= refl[0] * 0.9)
    have &= ~sea
    rgb = np.clip(np.moveaxis(refl, 0, -1) / gain, 0, 1.2)
    rgb = np.power(np.clip(rgb, 0, None), 0.72)
    lum = rgb.mean(-1, keepdims=True)
    rgb = np.clip(lum + (rgb - lum) * 1.18, 0, 1)
    rgb = rgb * rgb * (3 - 2 * rgb) * 0.55 + rgb * 0.45          # gentle S-curve
    # ocean / missing data: depth-tinted water
    d = np.clip(-elev, 0, 6000)
    shallow, deep = np.array([0.09, 0.30, 0.38]), np.array([0.02, 0.07, 0.15])
    k = np.clip(np.log1p(d) / np.log1p(5000), 0, 1)[..., None]
    water = shallow * (1 - k) + deep * k
    # land with no imagery (gaps, polar ice): ice-white near the poles, earth tones elsewhere
    if lat_rows is not None:
        polar = (np.abs(lat_rows) > 58).astype(np.float32)[:, None, None]
        landfill = polar * np.array([0.86, 0.89, 0.93]) + (1 - polar) * np.array([0.36, 0.38, 0.27])
        island = (elev > 0)[..., None]
        water = np.where(island & ~have[..., None], landfill, water)
    a = cv2.GaussianBlur(have.astype(np.float32), (0, 0), 1.5)[..., None]
    img = rgb * a + water * (1 - a)
    # hillshade (sun from the north-west), exaggerated more on wide views
    px_m = K / res * math.cos(math.radians(lat_mid))
    ex = 3.0 if res < 100 else 2.2 if res < 700 else 1.6
    e = np.where(elev > 0, elev, elev * 0.15) * ex
    gy, gx = np.gradient(e, px_m)
    slope = np.arctan(np.hypot(gx, gy)); aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(315), math.radians(42)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs = np.clip(hs / math.sin(alt), 0.35, 1.5)[..., None]
    img = img * (0.62 + 0.38 * hs)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def build_level(x0, x1, y0, y1, res, path):
    if os.path.exists(path):
        z = np.load(path)
        return z["img"], tuple(z["box"])
    refl = imagery(x0, x1, y0, y1, res)
    elev = terrain(x0, x1, y0, y1, res)
    rows = np.array([inv_merc_y(y1 - (i + 0.5) / res) for i in range(elev.shape[0])])
    img = colorize(refl, elev, res, inv_merc_y((y0 + y1) / 2), 1750.0 if res >= 700 else 2400.0, rows)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez(path, img=img, box=np.array([x0, x1, y0, y1, res], float))
    return img, (x0, x1, y0, y1, res)


def world_level():
    """Whole-world level at 16 px per merc-degree, cached once per workspace."""
    return build_level(-180, 180, merc_y(-72), merc_y(83), 16, os.path.join(CACHE, "world16c.npz"))


def plan_levels(views, margin=1.25, max_px=36e6):
    """views: list of (cx, cy, half_w, half_h, zoom) in merc-degrees. Returns [(res, box)]."""
    out = {}
    for cx, cy, hw, hh, z in views:
        need = [r for r in LEVELS[1:] if r >= z * 0.8]
        if not need or z < LEVELS[0] * 1.5:
            continue
        r = need[0]
        b = out.setdefault(r, [1e9, -1e9, 1e9, -1e9])
        b[0] = min(b[0], cx - hw * margin); b[1] = max(b[1], cx + hw * margin)
        b[2] = min(b[2], cy - hh * margin); b[3] = max(b[3], cy + hh * margin)
    res = []
    for r, b in sorted(out.items()):
        b[2] = max(b[2], merc_y(-75)); b[3] = min(b[3], merc_y(84))
        px = (b[1] - b[0]) * (b[3] - b[2]) * r * r
        if px > max_px:   # shrink around the centre if the union is huge
            s = math.sqrt(max_px / px)
            cx, cy = (b[0] + b[1]) / 2, (b[2] + b[3]) / 2
            b = [cx - (cx - b[0]) * s, cx + (b[1] - cx) * s, cy - (cy - b[2]) * s, cy + (b[3] - cy) * s]
        res.append((r, b))
    return res
