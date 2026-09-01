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


class TemplateMatchingHelper(ImageInterface):
    """Detect a reference template inside a frame via a two-stage strategy.

    ``cv2.matchTemplate`` handles exact same-scale matches with one bounded
    allocation; the SIFT + FLANN + RANSAC fallback (scale/rotation invariant)
    runs only when that finds nothing. The fallback reuses one feature-capped
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
        self.execution_output_dir = self.config.get("execution_output_path", "")

        capabilities = self.config.get("capabilities", {}) or {}
        # Uncapped SIFT on a 1080p frame yields 50k+ features -- the source of
        # the ~16-20MB/call growth seen in load testing; 2000 is plenty for UI
        # templates since the fast path handles exact matches first.
        self._sift_nfeatures = int(
            capabilities.get("sift_nfeatures", self._DEFAULT_SIFT_NFEATURES)
        )

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
        reading order.
        """
        th, tw = template.shape[:2]
        fh, fw = frame.shape[:2]
        if th == 0 or tw == 0 or th > fh or tw > fw:
            return None

        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        del frame_gray, template_gray

        if not np.any(res >= confidence_level):
            return None

        if index is None:
            # A qualifying pixel exists, so the global max clears the threshold.
            _, _, _, max_loc = cv2.minMaxLoc(res)
            x, y = int(max_loc[0]), int(max_loc[1])
        else:
            peaks = self._distinct_peaks(res, confidence_level, tw, th)
            # Negative index is rejected explicitly: peaks[-1] would otherwise
            # wrap silently to the last match.
            if index < 0 or index >= len(peaks):
                return _INDEX_MISS
            x, y = peaks[index]

        top_left = (x, y)
        bottom_right = (x + tw, y + th)
        center = (x + tw // 2, y + th // 2)
        return True, center, (top_left, bottom_right)

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
