"""Tests for the dry-run REST endpoints and their helpers.

Covers the inline (``POST /v1/dry_run``) and upload (``POST /v1/dry_run/upload``)
endpoints, the device-less session path, and the upload/payload security guards.
All tests run device-less (no driver), so no real device is ever touched.
"""
import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from optics_framework.common import expose_api
from optics_framework.common import dry_run as dry_run_helpers
from optics_framework.common.config_handler import Config
from optics_framework.common.session_manager import SessionManager
from optics_framework.helper.execute import build_suite_from_inline
from optics_framework.api import ActionKeyword, AppManagement, Verifier, FlowControl

pytestmark = pytest.mark.white_box

TEST_CASES_CSV = "test_case,test_step\nSmoke,Mod A\n"
MODULES_CSV = "module_name,module_step,param_1\nMod A,Launch App,\n"


@pytest.fixture
def client():
    return TestClient(expose_api.app)


def _valid_inline():
    return {
        "test_cases": {"Smoke": ["Mod A"]},
        "modules": {"Mod A": [["Launch App"]]},
        "elements": {},
    }


def _zip_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in members.items():
            z.writestr(name, content)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Inline endpoint
# --------------------------------------------------------------------------

def test_inline_happy_path_all_pass(client):
    r = client.post("/v1/dry_run", json=_valid_inline())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PASS"
    assert len(body["test_cases"]) == 1
    assert body["test_cases"][0]["name"] == "Smoke"


def test_inline_keyword_not_found_reports_fail_not_500(client):
    payload = {
        "test_cases": {"Smoke": ["Mod A"]},
        "modules": {"Mod A": [["Launch App"], ["Totally Fake Keyword", ["x"]]]},
    }
    r = client.post("/v1/dry_run", json=payload)
    assert r.status_code == 200  # dry-run reports it; not a server error
    body = r.json()
    assert body["status"] == "FAIL"
    keywords = body["test_cases"][0]["modules"][0]["keywords"]
    by_name = {k["name"]: k for k in keywords}
    assert by_name["Launch App"]["status"] == "PASS"
    assert by_name["Totally Fake Keyword"]["status"] == "FAIL"
    assert by_name["Totally Fake Keyword"]["reason"]  # reason is populated


def test_inline_empty_suite_returns_400(client):
    r = client.post("/v1/dry_run", json={"test_cases": {}, "modules": {}})
    assert r.status_code == 400


def test_inline_variable_resolution_does_not_crash(client):
    payload = {
        "test_cases": {"Smoke": ["Mod A"]},
        "modules": {"Mod A": [["Press Element", ["${user}"]]]},
        "elements": {"user": ["xpath=//input", "text=User"]},
    }
    r = client.post("/v1/dry_run", json=payload)
    assert r.status_code == 200


def test_inline_unresolved_variable_reported_as_fail(client):
    """An undefined ${var} is reported as a keyword FAIL (200), not a crash."""
    payload = {
        "test_cases": {"Smoke": ["Mod A"]},
        "modules": {"Mod A": [["Press Element", ["${missing}"]]]},
        "elements": {},
    }
    r = client.post("/v1/dry_run", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "FAIL"
    kw = body["test_cases"][0]["modules"][0]["keywords"][0]
    assert kw["status"] == "FAIL"
    assert "missing" in kw["reason"]


def test_inline_include_filter(client):
    payload = {
        "test_cases": {"Alpha": ["Mod A"], "Beta": ["Mod A"]},
        "modules": {"Mod A": [["Launch App"]]},
        "include": ["Alpha"],
    }
    r = client.post("/v1/dry_run", json=payload)
    assert r.status_code == 200
    names = {tc["name"] for tc in r.json()["test_cases"]}
    assert names == {"Alpha"}


def test_inline_invalid_payload_returns_422(client):
    # missing required "modules"
    r = client.post("/v1/dry_run", json={"test_cases": {"Smoke": ["Mod A"]}})
    assert r.status_code == 422


def test_inline_oversized_body_returns_413(client):
    big = {"test_cases": {"a": ["m"]}, "modules": {"m": [["x", ["p" * 6_000_000]]]}}
    r = client.post("/v1/dry_run", json=big)
    assert r.status_code == 413


# --------------------------------------------------------------------------
# Upload endpoint
# --------------------------------------------------------------------------

def test_upload_zip_happy_path(client):
    data = _zip_bytes(
        {"test_cases/cases.csv": TEST_CASES_CSV, "modules/mods.csv": MODULES_CSV}
    )
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("suite.zip", data, "application/zip")},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "PASS"


def test_upload_individual_csv_files(client):
    files = [
        ("files", ("cases.csv", TEST_CASES_CSV, "text/csv")),
        ("files", ("mods.csv", MODULES_CSV, "text/csv")),
    ]
    r = client.post("/v1/dry_run/upload", files=files)
    assert r.status_code == 200
    assert r.json()["status"] == "PASS"


def test_upload_zip_slip_rejected(client):
    data = _zip_bytes({"../escape.csv": "x"})
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("evil.zip", data, "application/zip")},
    )
    assert r.status_code == 400


def test_upload_zip_without_test_cases_returns_400(client):
    # modules only, no test cases -> clean 400, not a 500 (no sys.exit)
    data = _zip_bytes({"modules/m.csv": MODULES_CSV})
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("nomod.zip", data, "application/zip")},
    )
    assert r.status_code == 400


def test_upload_oversized_file_returns_413(client):
    # A single file larger than MAX_UPLOAD_BYTES is rejected during chunked read.
    big = b"a" * (dry_run_helpers.MAX_UPLOAD_BYTES + 1024)
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("big.csv", big, "text/csv")},
    )
    assert r.status_code == 413


def test_upload_empty_zip_returns_400(client):
    data = _zip_bytes({})
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("empty.zip", data, "application/zip")},
    )
    assert r.status_code == 400


def test_upload_zip_bomb_rejected(client):
    # ~55 MiB uncompressed -> exceeds MAX_UNCOMPRESSED_BYTES; tiny compressed.
    data = _zip_bytes({"modules/huge.csv": b"0" * (55 * 1024 * 1024)})
    r = client.post(
        "/v1/dry_run/upload",
        files={"files": ("bomb.zip", data, "application/zip")},
    )
    assert r.status_code == 413


def test_upload_cleans_up_temp_dir(client, monkeypatch):
    created = []
    real_mkdtemp = expose_api.tempfile.mkdtemp

    def _spy(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "optics_dryrun_":
            created.append(path)
        return path

    monkeypatch.setattr(expose_api.tempfile, "mkdtemp", _spy)
    data = _zip_bytes(
        {"test_cases/cases.csv": TEST_CASES_CSV, "modules/mods.csv": MODULES_CSV}
    )
    client.post("/v1/dry_run/upload", files={"files": ("suite.zip", data, "application/zip")})
    assert created, "expected a dry-run temp dir to be created"
    assert all(not os.path.exists(p) for p in created), "temp dir was not cleaned up"


# --------------------------------------------------------------------------
# Helper-level unit tests (HTTP-agnostic)
# --------------------------------------------------------------------------

def test_safe_suite_filename_rejects_path_like():
    for bad in ["../x.csv", "a/b.csv", "..", ".", ""]:
        with pytest.raises(dry_run_helpers.UnsafeArchive):
            dry_run_helpers.safe_suite_filename(bad)
    assert dry_run_helpers.safe_suite_filename("My Cases!.CSV") == "My_Cases.csv"


def test_safe_extract_zip_blocks_traversal(tmp_path):
    data = _zip_bytes({"../escape.csv": "x"})
    with pytest.raises(dry_run_helpers.UnsafeArchive):
        dry_run_helpers.safe_extract_zip(data, str(tmp_path))


def test_safe_extract_zip_enforces_uncompressed_cap(tmp_path):
    data = _zip_bytes({"modules/huge.csv": b"0" * (55 * 1024 * 1024)})
    with pytest.raises(dry_run_helpers.PayloadTooLarge):
        dry_run_helpers.safe_extract_zip(data, str(tmp_path))


def test_write_uploaded_files_total_cap(tmp_path):
    over = dry_run_helpers.MAX_UPLOAD_BYTES + 1
    with pytest.raises(dry_run_helpers.PayloadTooLarge):
        dry_run_helpers.write_uploaded_files([("big.csv", b"0" * over)], str(tmp_path))


def test_find_files_validate_false_does_not_exit(tmp_path):
    """find_files(validate=False) returns empty collections instead of sys.exit."""
    from optics_framework.helper.execute import find_files

    tc, mod, el, api, cfg = find_files(str(tmp_path), validate=False)
    assert tc == [] and mod == []


def test_load_suite_from_empty_folder_has_no_queue(tmp_path):
    from optics_framework.helper.execute import load_suite_from_folder

    suite = load_suite_from_folder(str(tmp_path))
    assert suite.execution_queue is None


def test_build_suite_from_inline_builds_linked_list():
    suite = build_suite_from_inline(
        test_cases={"Smoke": ["Mod A"]},
        modules={"Mod A": [["Launch App"], ["Press Element", ["x"]]]},
        elements={"x": ["xpath=//a"]},
    )
    assert suite.execution_queue is not None
    assert suite.execution_queue.name == "Smoke"
    assert "Mod A" in suite.modules_data.modules


def test_device_less_session_builds_registry():
    """A require_driver=False session builds the full keyword registry."""
    mgr = SessionManager()
    cfg = Config()
    cfg.driver_sources = []
    cfg.elements_sources = []
    cfg.text_detection = []
    cfg.image_detection = []
    sid = mgr.create_session(cfg, None, None, None, None, require_driver=False)
    session = mgr.get_session(sid)
    try:
        assert session.driver is None
        ak = session.optics.build(ActionKeyword)
        assert ak.element_source.instances == []
        assert ak.strategy_manager.locator_strategies == []
        session.optics.build(AppManagement)
        session.optics.build(Verifier)
        FlowControl(session=session, keyword_map={})
    finally:
        mgr.terminate_session(sid)


def test_default_session_still_fails_fast_without_driver():
    """Regression: default require_driver=True still rejects a driverless config."""
    from optics_framework.common.error import OpticsError

    mgr = SessionManager()
    cfg = Config()
    cfg.driver_sources = []
    with pytest.raises(OpticsError):
        mgr.create_session(cfg, None, None, None, None)
