"""The keyword listings a user reads offer only keywords a session can run."""
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from optics_framework.api.keywords import build_keywords
from optics_framework.common import expose_api
from optics_framework.common.config_handler import Config
from optics_framework.common.models import ElementData, ModuleData
from optics_framework.helper.list_keyword import main as list_keywords


def _runnable() -> set:
    builder = MagicMock()
    builder.session_config = Config(execution_output_path=tempfile.mkdtemp())
    session = SimpleNamespace(
        optics=SimpleNamespace(build=lambda cls: cls(builder)),
        modules=ModuleData(), elements=ElementData(), config_handler=MagicMock(), driver=MagicMock(),
    )
    return set(build_keywords(session).keyword_map)


def test_optics_list_and_optics_serve_name_no_keyword_a_session_cannot_run(capsys):
    list_keywords()
    listings = {
        "optics list": {"_".join(name.split()).lower() for name in capsys.readouterr().out.splitlines()},
        "optics serve": {k["keyword_slug"] for k in TestClient(expose_api.app).get("/v1/keywords").json()},
    }
    runnable = _runnable()
    for listing, slugs in listings.items():
        assert "press_element" in slugs, listing
        assert not {"log", "to_payload"} & slugs, listing
        assert slugs <= runnable, listing
