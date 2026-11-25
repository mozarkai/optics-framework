"""The keyword listings a user reads offer only keywords a session can run."""
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.helper.list_keyword import main as list_keywords
from tests.units.fakes import Phone, phone_keywords


def test_optics_list_and_optics_serve_name_no_keyword_a_session_cannot_run(capsys, tmp_path):
    list_keywords()
    listings = {
        "optics list": {"_".join(name.split()).lower() for name in capsys.readouterr().out.splitlines()},
        "optics serve": {k["keyword_slug"] for k in TestClient(expose_api.app).get("/v1/keywords").json()},
    }
    runnable = set(phone_keywords(Phone([]), tmp_path).keyword_map)
    for listing, slugs in listings.items():
        assert "press_element" in slugs, listing
        assert not {"log", "to_payload"} & slugs, listing
        assert slugs <= runnable, listing
