from typing import Literal
import cv2
import numpy as np
from optics_framework.common.image_interface import ImageInterface
from optics_framework.common.logging_config import internal_logger
from optics_framework.engines.vision_models.base_methods import load_template


class TemplateMatchingHelper(ImageInterface):
    """
    Template matching helper that detects a reference image inside an input image.

    This class uses OpenCV's :func:`cv2.matchTemplate` function to locate instances
    of a template (reference image) within a larger image.
    """

    def find_element(
        self, input_data, image, index=None, confidence_level=0.85, min_inliers=10
    ):
        """
        Match a template image within a single frame image using SIFT and FLANN-based matching.
        Returns the location of a specific match by index.

        Parameters:
        - input_data (np.array): Image data of the frame.
        - image (np.array): Image data of the template.
        - index (int): The index of the match to retrieve.
        - confidence_level (float): Confidence level for the ratio test (default is 0.85).
        - min_inliers (int): Minimum number of inliers required to consider a match valid (default is 10).

        Returns:
        - Tuple[bool, Tuple[int, int], Tuple[Tuple[int, int], Tuple[int, int]]] | None
        """
        image = load_template(image)
        sift = cv2.SIFT_create()
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        flann = cv2.FlannBasedMatcher(index_params, search_params)

        if image is None or input_data is None:
            return None

        frame_gray = cv2.cvtColor(input_data, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        kp_frame, des_frame = sift.detectAndCompute(frame_gray, None)
        kp_template, des_template = sift.detectAndCompute(template_gray, None)

        if des_template is None or des_frame is None:
            return None

        try:
            matches = flann.knnMatch(des_template, des_frame, k=2)
        except cv2.error:
            return None

        good_matches = [
            m for m, n in matches if m.distance < confidence_level * n.distance
        ]

        if len(good_matches) < min_inliers:
            return None

        src_pts = np.float32(
            [kp_template[m.queryIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_frame[m.trainIdx].pt for m in good_matches]).reshape(
            -1, 1, 2
        )

        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if M is None:
            return None

        matches_mask = mask.ravel().tolist()
        inliers = np.sum(matches_mask)
        if inliers < min_inliers:
            return None

        h, w = image.shape[:2]
        centers = []
        bboxes = []
        for i in range(len(good_matches)):
            if matches_mask[i]:
                center_template = np.float32([[w / 2, h / 2]]).reshape(-1, 1, 2)
                center_frame = cv2.perspectiveTransform(center_template, M)
                center_x, center_y = (
                    int(center_frame[0][0][0]),
                    int(center_frame[0][0][1]),
                )
                centers.append((center_x, center_y))

                # Bounding box
                pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
                dst = cv2.perspectiveTransform(pts, M)
                bbox = (tuple(np.int32(dst[0][0])), tuple(np.int32(dst[2][0])))
                bboxes.append(bbox)

        if not centers or not bboxes:
            return None

        if index is not None:
            if 0 <= index < len(centers):
                return True, centers[index], bboxes[index]
            else:
                return None

        return True, centers[0], bboxes[0]

    def assert_elements(self, input_data, elements, rule="any"):
        """
        Assert that elements (templates) are present in the input frame based on the specified rule.

        :param input_data: Input source (e.g., image, video frame) for detection.
        :type input_data: np.ndarray
        :param elements: List of template paths to locate.
        :type elements: list
        :param rule: Rule to apply ("any" or "all").
        :type rule: str
        :return: None
        """
        annotated_frame = input_data.copy()
        found_status = dict.fromkeys(elements, False)

        for template_path in elements:
            if found_status[template_path]:  # Skip if already found (for 'all' rule)
                continue

            result = self.find_element(
                input_data.copy(),  # use a copy of the frame to avoid overwriting annotations across templates
                image=template_path,
            )
            if result is not None:
                success, _, annotated = result
                if success:
                    found_status[template_path] = True
                    annotated_frame = annotated  # use the latest annotated version

        match_rule = (
            any(found_status.values()) if rule == "any" else all(found_status.values())
        )

        # Rule evaluation
        if match_rule:
            return True, annotated_frame

        internal_logger.warning("SIFT assert_elements failed.")
        return False, annotated_frame


    def element_exist(
        self,
        input_data,
        reference_data,
        offset=[0, 0],
        confidence_level=0.85,
        min_inliers=10,
    ) -> tuple[Literal[False], tuple[None, None], None] | tuple[Literal[True], tuple[int, int], list[tuple[int, int]]]:
        """
        Match a template image within a single frame image using SIFT and FLANN-based matching.
        Returns the center of the template in the frame as (x, y) or None if not found.

        Parameters:
        - input_data (np.array): Image data of the frame.
        - reference_data (np.array): Image data of the template.
        - offset (list): Optional [x, y] offsets in pixels to adjust the center location.
        - confidence_level (float): Confidence level for the ratio test (default is 0.85).
        - min_inliers (int): Minimum number of inliers required to consider a match valid (default is 10).

        Returns:
        - Tuple[int, int] if found, or None if not found.
        """
        if offset is None:
            offset = [0, 0]

        # Create SIFT object
        sift = cv2.SIFT_create()

        # Create FLANN object with parameters
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        flann = cv2.FlannBasedMatcher(index_params, search_params)

        if reference_data is None or input_data is None:
            return False, (None, None), None

        frame_gray = cv2.cvtColor(input_data, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(reference_data, cv2.COLOR_BGR2GRAY)

        # Detect keypoints and descriptors for both images
        kp_frame, des_frame = sift.detectAndCompute(frame_gray, None)
        kp_template, des_template = sift.detectAndCompute(template_gray, None)

        if des_template is None or des_frame is None:
            return False, (None, None), None

        try:
            matches = flann.knnMatch(des_template, des_frame, k=2)
        except cv2.error as e:
            internal_logger.debug(f"Error in FLANN matching: {e}")
            return False, (None, None), None

        # Apply Lowe's ratio test to filter good matches
        good_matches = [
            m for m, n in matches if m.distance < confidence_level * n.distance
        ]

        if len(good_matches) < min_inliers:
            return False, (None, None), None

        # Extract matched keypoints
        src_pts = np.float32([kp_template[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_frame[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

        # Compute homography matrix
        M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if M is None:
            return False, (None, None), None

        matches_mask = mask.ravel().tolist()
        inliers = np.sum(matches_mask)

        if inliers < min_inliers:
            return False, (None, None), None

        # Find center of the template in the frame
        h, w = reference_data.shape[:2]
        center_template = np.float32([[w / 2, h / 2]]).reshape(-1, 1, 2)
        center_frame = cv2.perspectiveTransform(center_template, M)
        center_x, center_y = int(center_frame[0][0][0]), int(center_frame[0][0][1])

        # Apply the offset to the center position
        center_x += offset[0]
        center_y -= offset[1]

        # Find bounding box corners
        bbox_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        try:
            bbox_transformed = cv2.perspectiveTransform(bbox_pts, M)
            bbox_corners = [(int(pt[0][0]), int(pt[0][1])) for pt in bbox_transformed]
            top_left = bbox_corners[0]
            bottom_right = bbox_corners[2]
        except cv2.error as e:
            internal_logger.debug(f"Error in perspective transformation: {e}")
            return False, (None, None), None

        # internal_logger.debug(f"Template found at center: ({center_x}, {center_y}) with bbox: {top_left} -> {bottom_right}")

        return True, (center_x, center_y), [top_left, bottom_right]
