"""Find a device screen in a camera frame, and tell when it leaves the view or goes dark."""
import cv2
import numpy as np

# Where the convex hull of the lit area runs straight: past rounded corners, across notches.
HULL_SPAN = (0.15, 0.85)
SIDE_SPAN = (0.02, 0.98)
SIDE_SAMPLES = 80
MIN_SIDE_SUPPORT = 0.15
HULL_GATE = 1.5
MAX_RESIDUAL = 1.0
MIN_CONTRAST = 0.08
ASPECT_TOLERANCE = 0.04
COARSE_ASPECT_TOLERANCE = 0.3
MIN_SCREEN_AREA = 0.01
MIN_SOLIDITY = 0.8
FIT_ROUNDS = 3
WORK_SIZE = 960
RING = (1.04, 1.18)
RING_MIN_TEXTURE = 6.0
RING_MIN_MATCH = 0.6
LIT_ABOVE_BLACK = 25
DARK_LEVEL = 50
MIN_LIT_FRACTION = 0.0005
SIDE_NAMES = ("top", "right", "bottom", "left")
GAIN_BINS = 160
GAIN_RANGE = (-1.0, 3.0)
GAIN_MODE_SHARE = 0.05
# A brightness change smaller than about 1.4x is not a screen at another level.
GAIN_MODE_GAP = 13


class ScreenNotFound(ValueError):
    """No screen of the expected shape in the frame; the message says why."""


def _gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame.astype(np.float32)


def brightness_signals(dim: np.ndarray, bright: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where the screen is, and its edge, from two frames at two screen brightness levels.

    The camera's own gain change is taken out first. The ratio of the frames marks every lit screen pixel whatever it
    shows; the difference keeps the edge where a blurred pixel is half screen, half bezel.
    """
    d, b = _gray(dim), _gray(bright)
    gain, static = _camera_gain(d, b)
    gained = b - gain * d
    kept = gained[static]
    noise = 1.4826 * float(np.median(np.abs(kept - np.median(kept)))) if kept.size > 1000 else 0.0
    lit = cv2.GaussianBlur(gained, (0, 0), 1.5) > max(3.0, 3 * noise)
    if lit.mean() < MIN_LIT_FRACTION:
        raise ScreenNotFound("the screen did not change when the driver pressed the brightness keys")
    region = np.where(lit, np.clip(np.log((b + 1) / (gain * d + 1)), 0, None), 0).astype(np.float32)
    return region, np.clip(gained, 0, None)


def _camera_gain(d: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray]:
    """The camera's own gain change between the frames, and the pixels whose light did not change.

    A pixel that kept its light changes by the gain alone, and a screen pixel by the gain times the screen's own
    change, so the lowest mode of the ratios is the gain. With one mode only, nothing static is bright enough to
    measure it, and the gain is taken as 1.
    """
    measured = (d > 12) & (b > 12)
    logs = np.log((b + 1) / (d + 1))
    counts, edges = np.histogram(logs[measured], bins=GAIN_BINS, range=GAIN_RANGE)
    mass = np.convolve(counts, np.ones(7), "same")
    peaks = [
        i for i in range(len(mass))
        if mass[i] >= GAIN_MODE_SHARE * measured.sum() and mass[i] == mass[max(0, i - 6): i + 7].max()
    ]
    log_gain = 0.0
    if len(peaks) >= 2 and peaks[-1] - peaks[0] >= GAIN_MODE_GAP:
        log_gain = float((edges[peaks[0]] + edges[peaks[0] + 1]) / 2)
    return float(np.exp(log_gain)), ~measured | (np.abs(logs - log_gain) < 0.1)


def frame_signals(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A lit screen is brighter than the dark bezel around it."""
    gray = _gray(frame)
    return gray, gray


def find_screen(region: np.ndarray, edge: np.ndarray, aspect: float) -> np.ndarray:
    """The screen's corners (top-left, top-right, bottom-right, bottom-left); ``aspect`` is its width / height."""
    scale = min(1.0, WORK_SIZE / max(region.shape))
    small = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else region
    quad, hull = _coarse_quad(_normalised(small), aspect)
    full = _normalised(cv2.GaussianBlur(edge, (0, 0), 1.0))
    lines = [_edge_line(full, _hull_line(hull, quad, side) / scale, quad / scale, side, HULL_GATE / scale) for side in range(4)]
    corners = np.array([_intersect(lines[(i - 1) % 4], lines[i]) for i in range(4)], dtype=np.float32)
    _check_shape(corners, aspect, region.shape)
    return corners


def _normalised(signal: np.ndarray) -> np.ndarray:
    top = float(np.percentile(signal, 99.5))
    if top <= 0:
        raise ScreenNotFound("nothing lit in the camera frame")
    return np.clip(signal / top, 0, 1).astype(np.float32)


def _coarse_quad(signal: np.ndarray, aspect: float) -> tuple[np.ndarray, np.ndarray]:
    blurred = cv2.GaussianBlur(signal, (0, 0), 2.0)
    _, mask = cv2.threshold((blurred * 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    size = max(3, int(max(mask.shape) * 0.01) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    reasons = []
    quads = []
    for contour in contours:
        quad, reason = _screen_candidate(contour, aspect, mask.shape)
        if quad is None:
            reasons.append(reason)
        else:
            quads.append((cv2.contourArea(quad), quad, cv2.convexHull(contour)))
    if not quads:
        found = next((r for r in reasons if r), "no lit area of the screen's shape")
        raise ScreenNotFound(found)
    _, quad, hull = max(quads, key=lambda q: q[0])
    return quad, hull.reshape(-1, 2).astype(np.float32)


def _screen_candidate(contour: np.ndarray, aspect: float, shape: tuple) -> tuple[np.ndarray | None, str | None]:
    area = cv2.contourArea(contour)
    if area < MIN_SCREEN_AREA * shape[0] * shape[1]:
        return None, None
    hull = cv2.convexHull(contour)
    if area / cv2.contourArea(hull) < MIN_SOLIDITY:
        return None, "the lit area is not one rectangle"
    x, y, w, h = cv2.boundingRect(hull)
    if x <= 1 or y <= 1 or x + w >= shape[1] - 1 or y + h >= shape[0] - 1:
        return None, "the screen reaches past the edge of the camera frame"
    quad = _four_corners(hull)
    if quad is None:
        return None, "the lit area has no four clear sides"
    try:
        _check_shape(quad, aspect, shape, COARSE_ASPECT_TOLERANCE)
    except ScreenNotFound as exc:
        return None, str(exc)
    return quad, None


def _four_corners(hull: np.ndarray) -> np.ndarray | None:
    perimeter = cv2.arcLength(hull, True)
    low, high = 0.0, 0.2 * perimeter
    for _ in range(30):
        epsilon = (low + high) / 2
        approx = cv2.approxPolyDP(hull, epsilon, True)
        if len(approx) == 4:
            return _ordered(approx.reshape(4, 2).astype(np.float32))
        if len(approx) > 4:
            low = epsilon
        else:
            high = epsilon
    return None


def _ordered(points: np.ndarray) -> np.ndarray:
    centre = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0])
    points = points[np.argsort(angles)]
    start = int(np.argmin(points.sum(axis=1)))
    return np.roll(points, -start, axis=0)


def _hull_line(hull: np.ndarray, quad: np.ndarray, side: int) -> np.ndarray:
    """The side as (point, unit direction), fitted to the hull, which bridges notches and skips rounded corners."""
    a, b = quad[side], quad[(side + 1) % 4]
    length = float(np.linalg.norm(b - a))
    direction = (b - a) / length
    normal = np.array([direction[1], -direction[0]])
    closed = np.vstack([hull, hull[:1]])
    points = np.vstack([
        start + np.linspace(0, 1, max(2, int(np.linalg.norm(end - start)) + 1))[:, None] * (end - start)
        for start, end in zip(closed[:-1], closed[1:])
    ])
    along = (points - a) @ direction / length
    near = (along > HULL_SPAN[0]) & (along < HULL_SPAN[1]) & (np.abs((points - a) @ normal) < 0.1 * length)
    if near.sum() < 2:
        return np.concatenate([a, direction]).astype(np.float32)
    vx, vy, x0, y0 = cv2.fitLine(points[near].astype(np.float32).reshape(-1, 1, 2), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0, vx, vy], np.float32)


def _edge_line(signal: np.ndarray, line: np.ndarray, quad: np.ndarray, side: int, gate: float) -> np.ndarray:
    """The side's edge as (point, unit direction), fitted to sub-pixel crossings of the screen's edge."""
    point, direction = line[:2], line[2:] / np.linalg.norm(line[2:])
    a, b = _project(quad[side], point, direction), _project(quad[(side + 1) % 4], point, direction)
    length = float(np.linalg.norm(b - a))
    if np.dot(b - a, direction) < 0:
        direction = -direction
    inward = np.array([direction[1], -direction[0]], dtype=np.float32)
    if np.dot(quad.mean(axis=0) - a, inward) < 0:
        inward = -inward
    depth = max(6.0, 0.015 * length, 2 * gate)
    for _ in range(FIT_ROUNDS):
        edge = _edge_points(signal, point, direction, inward, length, depth, a)
        # Rounded corners and notches leave the line; only points on it count.
        edge = edge[np.abs((edge - point) @ inward) <= gate]
        gate = MAX_RESIDUAL
        if len(edge) < MIN_SIDE_SUPPORT * SIDE_SAMPLES:
            raise ScreenNotFound(
                f"the {SIDE_NAMES[side]} edge of the screen is too dark to measure; show a bright screen"
            )
        vx, vy, x0, y0 = cv2.fitLine(edge.reshape(-1, 1, 2), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        point, fitted = np.array([x0, y0], np.float32), np.array([vx, vy], np.float32)
        direction = fitted if np.dot(fitted, direction) > 0 else -fitted
        depth = max(4.0, depth / 2)
    straight = np.abs((edge - point) @ np.array([direction[1], -direction[0]])) <= MAX_RESIDUAL
    if straight.sum() < MIN_SIDE_SUPPORT * SIDE_SAMPLES:
        raise ScreenNotFound(f"the {SIDE_NAMES[side]} edge of the screen is not a straight line; show a bright screen")
    return np.concatenate([point, direction])


def _project(corner: np.ndarray, point: np.ndarray, direction: np.ndarray) -> np.ndarray:
    return point + np.dot(corner - point, direction) * direction


def _edge_points(
    signal: np.ndarray, point: np.ndarray, direction: np.ndarray, inward: np.ndarray,
    length: float, depth: float, start: np.ndarray,
) -> np.ndarray:
    along = np.dot(start - point, direction) + np.linspace(*SIDE_SPAN, SIDE_SAMPLES) * length
    base = point + along[:, None] * direction
    offsets = np.arange(-depth, depth + 0.25, 0.25, dtype=np.float32)
    samples = base[:, None, :] + offsets[None, :, None] * inward
    profiles = cv2.remap(
        signal, samples[..., 0].astype(np.float32), samples[..., 1].astype(np.float32),
        cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
    )
    quarter = len(offsets) // 4
    outside = np.median(profiles[:, :quarter], axis=1)
    inside = np.median(profiles[:, -quarter:], axis=1)
    contrast = inside - outside
    level = (inside + outside) / 2
    found = []
    for row, line in enumerate(profiles):
        if contrast[row] < MIN_CONTRAST:
            continue
        above = np.nonzero(line >= level[row])[0]
        if not len(above) or above[0] == 0:
            continue
        i = above[0]
        step = line[i] - line[i - 1]
        fraction = (level[row] - line[i - 1]) / step if step > 0 else 0.0
        offset = offsets[i - 1] + fraction * (offsets[i] - offsets[i - 1])
        found.append(base[row] + offset * inward)
    return np.array(found, dtype=np.float32).reshape(-1, 2)


def _intersect(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    p, r = first[:2], first[2:]
    q, s = second[:2], second[2:]
    cross = r[0] * s[1] - r[1] * s[0]
    if abs(cross) < 1e-6:
        raise ScreenNotFound("two sides of the screen are parallel")
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / cross
    return p + t * r


def _check_shape(corners: np.ndarray, aspect: float, shape: tuple, tolerance: float = ASPECT_TOLERANCE) -> None:
    if not cv2.isContourConvex(corners.reshape(-1, 1, 2)):
        raise ScreenNotFound("the screen's corners do not form a convex shape")
    height, width = shape[:2]
    if (corners < -1).any() or (corners[:, 0] > width).any() or (corners[:, 1] > height).any():
        raise ScreenNotFound("the screen reaches past the edge of the camera frame")
    found = _rectangle_aspect(corners, shape)
    if abs(np.log(found / aspect)) > np.log(1 + tolerance):
        raise ScreenNotFound(
            f"the screen in the camera is {found:.2f} wide per unit of height, not {aspect:.2f}"
            " (out_width / out_height); check the size, or set rotation"
        )


def _rectangle_aspect(corners: np.ndarray, shape: tuple) -> float:
    """Width / height of the rectangle whose perspective image is ``corners`` (Zhang and He, whiteboard scanning)."""
    centre = np.array([shape[1] / 2, shape[0] / 2])
    m1, m2, m4, m3 = (np.append(c - centre, 1.0) for c in corners.astype(np.float64))
    k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
    k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    n2, n3 = k2 * m2 - m1, k3 * m3 - m1
    focal_sq = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2]) if abs(n2[2] * n3[2]) > 1e-12 else 0.0
    if not 0.09 * max(shape) ** 2 < focal_sq < 9 * max(shape) ** 2:
        focal_sq = (0.9 * max(shape)) ** 2
    across = n2[0] ** 2 + n2[1] ** 2 + n2[2] ** 2 * focal_sq
    down = n3[0] ** 2 + n3[1] ** 2 + n3[2] ** 2 * focal_sq
    return float(np.sqrt(across / down))


class ViewGuard:
    """Remembers the area around the screen, which no screen content changes, to tell when the phone or camera moved."""

    def __init__(self, frame: np.ndarray, corners: np.ndarray):
        self._shape = frame.shape[:2]
        self._scale = min(1.0, WORK_SIZE / max(self._shape))
        gray = self._small(frame)
        quad = corners * self._scale
        self._ring = self._ring_mask(gray.shape, quad)
        reference = gray[self._ring]
        self.anchored = reference.size > 200 and float(reference.std()) >= RING_MIN_TEXTURE
        self._reference = reference - reference.mean() if self.anchored else reference

    def _small(self, frame: np.ndarray) -> np.ndarray:
        gray = _gray(frame)
        if self._scale < 1:
            gray = cv2.resize(gray, None, fx=self._scale, fy=self._scale, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (0, 0), 1.5)

    @staticmethod
    def _ring_mask(shape: tuple, quad: np.ndarray) -> np.ndarray:
        centre = quad.mean(axis=0)
        inner, outer = (np.zeros(shape, np.uint8) for _ in range(2))
        cv2.fillConvexPoly(inner, ((quad - centre) * RING[0] + centre).astype(np.int32), 1)
        cv2.fillConvexPoly(outer, ((quad - centre) * RING[1] + centre).astype(np.int32), 1)
        return (outer > 0) & (inner == 0)

    def moved(self, frame: np.ndarray) -> bool:
        """Whether the frame changed size or the area around the screen no longer looks as it did, when that area has texture."""
        if frame.shape[:2] != self._shape:
            return True
        if not self.anchored:
            return False
        current = self._small(frame)[self._ring]
        current = current - current.mean()
        norm = float(np.linalg.norm(current) * np.linalg.norm(self._reference))
        return norm == 0 or float(current @ self._reference) / norm < RING_MIN_MATCH


def screen_dark(screen: np.ndarray) -> bool:
    """Whether the deskewed screen is black with no lit pixel on it."""
    gray = cv2.GaussianBlur(_gray(screen), (5, 5), 0)
    black = float(np.percentile(gray, 5))
    return black < DARK_LEVEL and float((gray > black + LIT_ABOVE_BLACK).mean()) < MIN_LIT_FRACTION
