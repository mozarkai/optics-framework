import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.llm_models import gemini


class TestGeminiMissingDependency:
    def test_instantiation_without_extra_raises_e0601(self, monkeypatch):
        monkeypatch.setattr(gemini, "genai", None)
        monkeypatch.setattr(gemini, "_IMPORT_ERROR", ImportError("No module named 'google'"))
        with pytest.raises(OpticsError) as exc_info:
            gemini.GeminiLLM({"capabilities": {}})
        assert exc_info.value.code == Code.E0601
        assert "optics-framework[gemini]" in exc_info.value.message
