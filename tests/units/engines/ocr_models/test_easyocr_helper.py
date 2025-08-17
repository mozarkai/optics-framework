import pytest
import cv2
from pathlib import Path
from optics_framework.engines.vision_models.ocr_models.easyocr import EasyOCRHelper


@pytest.fixture
def easyocr_instance():
    """Fixture to initialize EasyOCRHelper."""
    return EasyOCRHelper(language="en")


@pytest.fixture
def sample_image():
    """Loads a sample test image as a NumPy array."""
    test_file_path = Path(__file__).resolve()  # Get the test file's absolute path
    image_path = test_file_path.parent.parent.parent.parent / "assets" /  \
    "sample_text_image.png"  # Construct absolute path
    image = cv2.imread(str(image_path))
    assert image is not None, "Test image not found or failed to load"
    return image


def test_detect_text(easyocr_instance, sample_image):
    """Test that detect method correctly identifies text in an image."""
    reference_text = "Connected"
    result = easyocr_instance.detect_text(sample_image)
    # Filter results for reference_text
    filtered = [box for box in result if reference_text in box[1]] if result else None
    assert filtered is not None, "Text should be detected in the image"
    assert isinstance(filtered, list), "Result should be a list of bounding boxes"
    assert all(isinstance(box, tuple) and len(box) == 3 for box in filtered), "Each box should be a tuple of (bbox, text, confidence)"


def test_detect_text_not_found(easyocr_instance, sample_image):
    """Test that detect returns None if reference text is not found."""
    reference_text = "NonexistentText"
    result = easyocr_instance.detect_text(sample_image)
    # Filter results for reference_text
    filtered = [box for box in result if reference_text in box[1]] if result else None
    assert not filtered, "Should return None or empty list if text not found"
