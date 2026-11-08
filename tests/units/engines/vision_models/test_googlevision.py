"""GoogleVisionHelper client credentials, with the Vision client replaced by a fake."""
import pytest

pytest.importorskip("google.cloud.vision", reason="google-vision tests need the google-vision extra")

from optics_framework.common.error import OpticsError  # noqa: E402
from optics_framework.engines.vision_models.ocr_models import googlevision  # noqa: E402

pytestmark = pytest.mark.white_box


@pytest.fixture
def clients(monkeypatch):
    built = []
    monkeypatch.setattr(googlevision, "ImageAnnotatorClient", lambda **kw: built.append(kw) or object())
    return built


def test_config_api_key_reaches_the_client(clients):
    googlevision.GoogleVisionHelper({"capabilities": {"api_key": "config-key"}})
    assert clients[-1]["client_options"].api_key == "config-key"


def test_api_key_and_credentials_path_together_are_refused(clients):
    with pytest.raises(OpticsError, match="api_key or credentials_path"):
        googlevision.GoogleVisionHelper({"capabilities": {"api_key": "k", "credentials_path": "sa.json"}})


def test_without_credentials_the_client_uses_application_default_credentials(clients):
    helper = googlevision.GoogleVisionHelper({"capabilities": {}})
    assert clients == []
    helper._annotator()
    assert clients == [{}]
