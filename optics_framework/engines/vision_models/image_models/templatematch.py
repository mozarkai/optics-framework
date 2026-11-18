import math
from dataclasses import dataclass
from typing import Literal
import cv2
import numpy as np
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.engines.vision_models.base_methods import load_template
from optics_framework.common import utils

_MatchResult = tuple[Literal[True], tuple[int, int], tuple[tuple[int, int], tuple[int, int]]]


class _IndexMiss:
    """Distinct matches exist but ``index`` selects none: definitive not-found.

    Callers must not fall back to SIFT, which has no ``index`` notion and
    would return its single best match -- a false positive.
    """

    __slots__ = ()


_INDEX_MISS = _IndexMiss()

_PEAK_CANDIDATE_CAP = 4096
_REFINE_CAP = 64
_COARSE_SIDE = 24
_COARSE_SLACK = 0.1
_COARSE_FIDELITY = 0.8
_SCALE_STEP = 1.05
_EQUALIZE_TILE_PX = 192
_DEFAULT_SCALE_RANGE = (0.8, 1.25)


@dataclass(frozen=True)
class _Coarse:
    score: float
    scale: float
    res: np.ndarray
    template_shape: tuple[int, int]


@dataclass(frozen=True)
class _Hit:
    x: int
    y: int
    w: int
    h: int
    score: float


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _equalize(gray: np.ndarray) -> np.ndarray:
    """CLAHE with tiles of a fixed pixel size, so a crop and its frame get the same local mapping."""
    h, w = gray.shape[:2]
    tiles = (max(1, round(w / _EQUALIZE_TILE_PX)), max(1, round(h / _EQUALIZE_TILE_PX)))
    return cv2.createCLAHE(clipLimit=1.5, tileGridSize=tiles).apply(gray)


def _resize(image: np.ndarray, factor: float) -> np.ndarray:
    if math.isclose(factor, 1.0):
        return image
    h, w = image.shape[:2]
    size = (max(1, round(w * factor)), max(1, round(h * factor)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)


def _coarse_factor(template: np.ndarray) -> float:
    """Largest reduction after which the template, enlarged back, still correlates with itself."""
    h, w = template.shape[:2]
    factor = min(1.0, _COARSE_SIDE / min(h, w))
    while factor < 1.0:
        restored = cv2.resize(_resize(template, factor), (w, h), interpolation=cv2.INTER_LINEAR)
        if cv2.matchTemplate(template, restored, cv2.TM_CCOEFF_NORMED)[0, 0] >= _COARSE_FIDELITY:
            return factor
        factor = min(1.0, factor * 2)
    return 1.0


def _fits(template: np.ndarray, frame: np.ndarray) -> bool:
    return template.shape[0] <= frame.shape[0] and template.shape[1] <= frame.shape[1]


def _scale_grid(low: float, high: float) -> tuple[float, ...]:
    step = math.log(_SCALE_STEP)
    first, last = math.ceil(math.log(low) / step - 1e-9), math.floor(math.log(high) / step + 1e-9)
    return tuple(_SCALE_STEP ** k for k in range(first, last + 1)) or (math.sqrt(low * high),)


def _parse_scale_range(value) -> tuple[float, ...]:
    low, high = (float(v) for v in (value or _DEFAULT_SCALE_RANGE))
    if not 0 < low <= high:
        raise ValueError(f"scale_range must be [low, high] with 0 < low <= high, got {value!r}.")
    return _scale_grid(low, high)


def _coarse_scan(
    frame: np.ndarray, template: np.ndarray, factor: float, scales: tuple[float, ...]
) -> _Coarse | None:
    best: _Coarse | None = None
    for scale in scales:
        sized = _resize(template, scale * factor)
        if not _fits(sized, frame):
            continue
        res = cv2.matchTemplate(frame, sized, cv2.TM_CCOEFF_NORMED)
        score = float(res.max())
        if best is None or score > best.score:
            best = _Coarse(score, scale, res, sized.shape[:2])
    return best


def _refine(frame: np.ndarray, template: np.ndarray, center: tuple[float, float], margin: int) -> _Hit | None:
    """Best full-resolution placement of ``template`` within ``margin`` px of ``center``."""
    th, tw = template.shape[:2]
    x0 = max(0, round(center[0] - tw / 2) - margin)
    y0 = max(0, round(center[1] - th / 2) - margin)
    window = frame[y0 : y0 + th + 2 * margin, x0 : x0 + tw + 2 * margin]
    if not _fits(template, window):
        return None
    _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED))
    return _Hit(x0 + x, y0 + y, tw, th, float(score))


def _refine_scale(
    frame: np.ndarray, template: np.ndarray, scale: float, center: tuple[float, float], margin: int
) -> float:
    half_step = math.sqrt(_SCALE_STEP)
    trials = ((s, _refine(frame, _resize(template, s), center, margin)) for s in (scale / half_step, scale, scale * half_step))
    scored = [(hit.score, s) for s, hit in trials if hit is not None]
    return max(scored)[1] if scored else scale


def _pick(hits: list[_Hit], index: int | None) -> _Hit | _IndexMiss | None:
    """The strongest hit, or the ``index``-th in reading order."""
    if not hits:
        return None
    if index is None:
        return max(hits, key=lambda h: h.score)
    # hits[-1] would otherwise wrap silently to the last match.
    if index < 0 or index >= len(hits):
        return _INDEX_MISS
    return sorted(hits, key=lambda h: (h.y, h.x))[index]


class TemplateMatchingHelper(ImageInterface):
    """Detect a reference template inside a frame.

    ``cv2.matchTemplate`` runs coarse-to-fine: first at the template's own
    size, then over ``scale_range`` on contrast-equalised images. The SIFT +
    FLANN + RANSAC fallback (rotation invariant) runs only when both find
    nothing. The fallback reuses one feature-capped
    SIFT and one FLANN matcher per helper and releases large intermediates
    promptly, keeping per-call memory bounded under concurrent load.
    """

    _DEFAULT_SIFT_NFEATURES = 2000

    def __init__(self, config=None):
        """Initialize from a config dict; raises ValueError when it is None."""
        self.config = config
        if config is None:
            raise ValueError("Configuration must be provided.")
        self.project_path = self.config.get("project_path", "")
        self.templates = self.config.get("templates", None)

        capabilities = self.config.get("capabilities", {}) or {}
        # Uncapped SIFT on a 1080p frame yields 50k+ features -- the source of
        # the ~16-20MB/call growth seen in load testing; 2000 is plenty for UI
        # templates since the fast path handles exact matches first.
        self._sift_nfeatures = int(
            capabilities.get("sift_nfeatures", self._DEFAULT_SIFT_NFEATURES)
        )
        self._scales = _parse_scale_range(capabilities.get("scale_range"))

        # Safe as singletons: the batch runner is sequential within a session,
        # and the concurrent serve/MCP path holds Session.keyword_lock.
        self._sift: cv2.SIFT | None = None
        self._flann: cv2.FlannBasedMatcher | None = None

    def _get_sift(self) -> cv2.SIFT:
        if self._sift is None:
            self._sift = cv2.SIFT_create(nfeatures=self._sift_nfeatures)
        return self._sift

    def _get_flann(self) -> cv2.FlannBasedMatcher:
        if self._flann is None:
            flann_index_kdtree = 1
            index_params = {"algorithm": flann_index_kdtree, "trees": 5}
            search_params = {"checks": 50}
            self._flann = cv2.FlannBasedMatcher(index_params, search_params)
        return self._flann

    def _match_template_fast(
        self, frame: np.ndarray, template: np.ndarray, confidence_level: float,
        index: int | None
    ) -> _MatchResult | _IndexMiss | None:
        """Locate ``template`` in ``frame`` via normalised cross-correlation.

        Returns the match tuple on success, ``None`` when nothing clears
        ``confidence_level`` (caller may fall back to SIFT), or ``_INDEX_MISS``
        when distinct matches exist but ``index`` selects none. ``index=None``
        takes the strongest match; an integer takes the n-th distinct match in
        reading order. The first pass matches at the template's own size; only
        when it finds nothing does a second pass sweep ``scale_range``. That
        pass runs on contrast-equalised images, which keeps the many trial
        scales from lifting look-alike icons over the bar.
        """
        th, tw = template.shape[:2]
        if th == 0 or tw == 0:
            return None
        frame_gray = _gray(frame)
        template_gray = _gray(template)
        found = self._search(frame_gray, template_gray, confidence_level, index, (1.0,))
        if found is None:
            found = self._search(
                _equalize(frame_gray), _equalize(template_gray), confidence_level, index, self._scales
            )
        return found

    def _search(
        self, frame: np.ndarray, template: np.ndarray, confidence_level: float,
        index: int | None, scales: tuple[float, ...]
    ) -> _MatchResult | _IndexMiss | None:
        """Pick the best scale on a coarse copy, then score each instance at full resolution."""
        factor = _coarse_factor(template)
        coarse = _coarse_scan(_resize(frame, factor), template, factor, scales)
        floor = confidence_level - _COARSE_SLACK
        if coarse is None or coarse.score < floor:
            return None
        ch, cw = coarse.template_shape
        peaks = self._distinct_peaks(coarse.res, floor, cw, ch)
        peaks.sort(key=lambda p: -coarse.res[p[1], p[0]])
        centers = [((x + cw / 2) / factor, (y + ch / 2) / factor) for x, y in peaks[:_REFINE_CAP]]
        margin = math.ceil(1 / factor) + 2
        scale = coarse.scale
        if len(scales) > 1:
            scale = _refine_scale(frame, template, scale, centers[0], margin)
        sized = _resize(template, scale)
        hits = [h for h in (_refine(frame, sized, c, margin) for c in centers) if h and h.score >= confidence_level]
        hit = _pick(hits, index)
        if not isinstance(hit, _Hit):
            return hit
        internal_logger.debug("Template matched at scale %.2f, score %.3f.", scale, hit.score)
        return True, (hit.x + hit.w // 2, hit.y + hit.h // 2), ((hit.x, hit.y), (hit.x + hit.w, hit.y + hit.h))

    @staticmethod
    def _distinct_peaks(
        res: np.ndarray, confidence_level: float, tw: int, th: int
    ) -> list[tuple[int, int]]:
        """Qualifying correlation peaks, one per matched instance, in reading order.

        ``matchTemplate`` correlates strongly across a neighbourhood of every
        true match, so the qualifying pixels are collapsed to one point per
        instance: a vectorised local-maximum filter first, then greedy
        suppression of anything within half a template of an already-kept peak.
        Suppression consults a boolean map and only the highest-scoring
        ``_PEAK_CANDIDATE_CAP`` local maxima enter the greedy pass, keeping the
        cost linear in candidates when degenerate frames correlate into huge
        plateaus of qualifying pixels. The radii are per-axis, so a
        wide-and-short template does not merge
        instances stacked vertically. Survivors are ordered top-to-bottom,
        left-to-right so ``index`` selects the n-th match *on screen*, the same
        way the OCR engines interpret it. Instances closer than half a
        template on an axis still merge, so ``index`` counts visual groups.
        """
        dx, dy = max(1, tw // 2), max(1, th // 2)
        neighbourhood = np.ones((2 * dy + 1, 2 * dx + 1), np.uint8)
        local_max = (res >= cv2.dilate(res, neighbourhood)) & (res >= confidence_level)
        ys, xs = np.nonzero(local_max)

        height, width = res.shape
        suppressed = np.zeros((height, width), dtype=bool)
        kept: list[tuple[int, int]] = []
        order = np.argsort(-res[ys, xs], kind="stable")[:_PEAK_CANDIDATE_CAP]
        for idx in order:
            x, y = int(xs[idx]), int(ys[idx])
            if suppressed[y, x]:
                continue
            kept.append((x, y))
            suppressed[
                max(0, y - dy + 1) : min(height, y + dy),
                max(0, x - dx + 1) : min(width, x + dx),
            ] = True
        return sorted(kept, key=lambda p: (p[1], p[0]))

    def _sift_match(
        self, frame: np.ndarray, template: np.ndarray,
        confidence_level: float, min_inliers: int
    ) -> tuple[int, int, tuple[int, int], tuple[int, int]]:
        """SIFT + FLANN + RANSAC homography.

        Returns ``(center_x, center_y, top_left, bottom_right)``; raises
        ``RuntimeError`` on a miss (legacy contract).
        """
        sift = self._get_sift()
        flann = self._get_flann()

        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        kp_frame, des_frame = sift.detectAndCompute(frame_gray, None)
        kp_template, des_template = sift.detectAndCompute(template_gray, None)
        del frame_gray, template_gray

        if des_template is None or des_frame is None:
            raise RuntimeError("SIFT feature detection failed.")

        try:
            matches = flann.knnMatch(des_template, des_frame, k=2)
        except cv2.error as e:
            internal_logger.debug(f"Error in FLANN matching: {e}")
            raise RuntimeError(f"FLANN matching failed: {e}") from e

        good_matches = [
            m for m, n in matches if m.distance < confidence_level * n.distance
        ]

        # Descriptor matrices are the large allocations (tens of MB at 1080p);
        # drop them before the homography work to bound peak memory.
        del des_frame, des_template, matches

        if len(good_matches) < min_inliers:
            raise RuntimeError("Not enough good matches found.")

        src_pts = np.float32(
            [kp_template[m.queryIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)
        dst_pts = np.float32(
            [kp_frame[m.trainIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)

        m, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if m is None:
            raise RuntimeError("Homography computation failed.")

        matches_mask = mask.ravel().tolist()
        inliers = int(np.sum(matches_mask))
        if inliers < min_inliers:
            raise RuntimeError("Not enough inliers found.")

        h, w = template.shape[:2]
        center_template = np.float32([[w / 2, h / 2]]).reshape(-1, 1, 2)
        try:
            center_frame = cv2.perspectiveTransform(center_template, m)
        except cv2.error as e:
            raise RuntimeError(f"Perspective transformation failed: {e}") from e
        center_x, center_y = (
            int(center_frame[0][0][0]),
            int(center_frame[0][0][1]),
        )

        bbox_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        try:
            bbox_transformed = cv2.perspectiveTransform(bbox_pts, m)
        except cv2.error as e:
            raise RuntimeError(f"Perspective transformation failed: {e}") from e
        top_left = (
            int(bbox_transformed[0][0][0]),
            int(bbox_transformed[0][0][1]),
        )
        bottom_right = (
            int(bbox_transformed[2][0][0]),
            int(bbox_transformed[2][0][1]),
        )

        return center_x, center_y, top_left, bottom_right

    def find_element(
        self, input_data, image, index=None, confidence_level=0.85, min_inliers=10
    ):
        """Match a template in a frame: matchTemplate fast path, SIFT fallback.

        ``index`` selects the n-th distinct match top-to-bottom, left-to-right;
        ``None`` selects the strongest. Returns
        ``(True, center, (top_left, bottom_right))``, or ``None`` when distinct
        matches exist but ``index`` selects none of them -- not-found, never a
        SIFT false positive. ``confidence_level`` is a correlation threshold
        here (higher = stricter) but a Lowe ratio factor in ``_sift_match``
        (higher = more lenient).
        """
        if input_data is None:
            raise ValueError("Input data is None.")
        template = load_template(image, self.templates)

        fast = self._match_template_fast(input_data, template, confidence_level, index)
        if fast is _INDEX_MISS:
            # SIFT has no ``index`` notion; consulting it would return its
            # single best match -- a false positive at the wrong location.
            internal_logger.debug(
                "Template matches exist but index %s selects none; reporting not found.",
                index,
            )
            return None
        if fast is not None:
            return fast

        center_x, center_y, top_left, bottom_right = self._sift_match(
            input_data, template, confidence_level, min_inliers
        )
        return True, (center_x, center_y), (top_left, bottom_right)

    def assert_elements(self, input_data, elements, rule="any"):
        """Locate each template in the frame per ``rule`` ("any"/"all").

        Returns ``(True, annotated_frame)`` on success; raises ``RuntimeError``
        when the rule is not satisfied.
        """
        annotated_frame = input_data.copy()
        found_status = dict.fromkeys(elements, False)

        for template_path in elements:
            if found_status[template_path]:
                continue

            # copy so annotations from earlier templates don't compound
            result = self.find_element(
                input_data.copy(),
                image=template_path,
            )
            if result is not None:
                success, _, bbox = result
                if success:
                    found_status[template_path] = True
                    annotated_frame = utils.annotate(annotated_frame, [bbox])

        match_rule = (
            any(found_status.values()) if rule == "any" else all(found_status.values())
        )

        if match_rule:
            return True, annotated_frame

        internal_logger.warning("assert_elements failed.")
        raise RuntimeError("assert_elements failed.")

    @staticmethod
    def _apply_offset(
        center: tuple[int, int], top_left: tuple[int, int],
        bottom_right: tuple[int, int], offset: list[int],
    ) -> tuple[Literal[True], tuple[int, int], list[tuple[int, int]]]:
        """Apply a pixel ``offset`` (x right, y up) and build the success tuple."""
        center_x, center_y = center
        center_x += offset[0]
        center_y -= offset[1]
        return True, (center_x, center_y), [top_left, bottom_right]

    def element_exist(
        self,
        input_data,
        reference_data,
        offset=[0, 0],
        confidence_level=0.85,
        min_inliers=10,
    ) -> tuple[Literal[True], tuple[int, int], list[tuple[int, int]]]:
        """Match ``reference_data`` in ``input_data``; apply pixel ``offset`` to the center.

        Returns ``(True, (x, y), [top_left, bottom_right])``; raises
        ``RuntimeError`` when no match is found.
        """
        if offset is None:
            offset = [0, 0]

        if reference_data is None or input_data is None:
            raise ValueError("Input image and reference image must be provided.")

        fast = self._match_template_fast(input_data, reference_data, confidence_level, None)
        if fast is not None:
            _, center, (top_left, bottom_right) = fast
            return self._apply_offset(center, top_left, bottom_right, offset)

        center_x, center_y, top_left, bottom_right = self._sift_match(
            input_data, reference_data, confidence_level, min_inliers
        )
        return self._apply_offset((center_x, center_y), top_left, bottom_right, offset)
