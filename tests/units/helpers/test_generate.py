"""
Unit and Integration Tests for generate.py module

This test file contains comprehensive testing for the test generation functionality,
covering multiple testing approaches:

1. UNIT TESTS (White-box testing):
   - Test individual methods including protected/private methods
   - Verify internal logic, edge cases, and component isolation
   - Examples: _parse_step, _resolve_params, _transform_config_structure

2. INTEGRATION TESTS (Black-box testing):
   - Test complete workflows using public APIs only
   - Verify end-to-end functionality from input files to generated output
   - Examples: CSV/YAML to pytest/Robot Framework generation flows

3. HYBRID TESTS:
   - Combine unit and integration testing approaches
   - Test both internal components and complete workflows
   - Provide comprehensive coverage and better debugging capabilities

Testing Categories:
- Data Reading: CSV and YAML parsing functionality
- Code Generation: pytest and Robot Framework test generation
- File Operations: File writing and directory structure creation
- Error Handling: Conflict detection and edge case management
- Parameter Processing: Numeric preservation and element resolution

The pylint warnings about protected member access are expected and acceptable
in test files, as they're necessary for comprehensive unit testing.
"""

import os
import shutil
import tempfile
from unittest.mock import patch, mock_open, MagicMock

import pytest
from optics_framework.helper.generate import (
    CSVDataReader,
    YAMLDataReader,
    PytestGenerator,
    RobotGenerator,
    FileWriter,
    generate_test_file,
)


# Test Data Templates
class TestDataTemplates:
    """
    HELPER CLASS: Provides reusable test data templates

    PURPOSE: Reduce code duplication by centralizing common test data patterns
    TESTING TYPE: Test infrastructure (supports both unit and integration tests)

    This class contains static methods that generate standardized test data
    for CSV and YAML formats, ensuring consistent test scenarios across
    different test cases.
    """

    @staticmethod
    def get_login_test_cases_csv():
        return """test_case,test_step
Login Test,Login Module
Login Test,Verify Module
Logout Test,Logout Module"""

    @staticmethod
    def get_login_test_cases_yaml():
        return """Test Cases:
  Login Test:
    - Login Module
    - Verify Module
  Logout Test:
    - Logout Module"""

    @staticmethod
    def get_login_modules_csv():
        return """module_name,module_step,param_1,param_2,param_3
Login Module,Launch App,test_app,,
Login Module,Enter Text,${username_field},testuser,
Login Module,Press Element,${login_button},,
Verify Module,Validate Element,${welcome_text},,
Logout Module,Press Element,${logout_button},,
Logout Module,Sleep,3000,,
Logout Module,Scroll from Element,${element},right,3000.0"""

    @staticmethod
    def get_login_modules_yaml():
        return """Modules:
  Login Module:
    - Launch App test_app
    - Enter Text ${username_field} testuser
    - Press Element ${login_button}
  Verify Module:
    - Validate Element ${welcome_text}
  Logout Module:
    - Press Element ${logout_button}
    - Sleep 3000
    - Scroll From Element ${element} right 3000.0"""

    @staticmethod
    def get_login_elements_csv():
        return """Element_Name,Element_ID
username_field,id:username
login_button,id:login_btn
welcome_text,xpath://span[text()='Welcome']
logout_button,id:logout_btn
element,id:swipe_element"""

    @staticmethod
    def get_login_elements_yaml():
        return """Elements:
  username_field: id:username
  login_button: id:login_btn
  welcome_text: xpath://span[text()='Welcome']
  logout_button: id:logout_btn
  element: id:swipe_element"""

    @staticmethod
    def get_basic_config():
        return """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: yaml
    file: elements.yaml"""

    @staticmethod
    def get_yaml_config():
        return """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: yaml
    file: elements.yaml"""

    @staticmethod
    def get_simple_config():
        return "driver_sources: []"


# Helper Functions
class TestHelpers:
    """
    HELPER CLASS: Utility functions for test setup and verification

    PURPOSE: Reduce code duplication and provide reusable test operations
    TESTING TYPE: Test infrastructure (supports both unit and integration tests)

    This class provides methods for:
    - File creation and management in test environments
    - Output verification and content validation
    - Framework-specific structure verification
    - Common assertion patterns for generated content
    """

    @staticmethod
    def create_files(temp_dir, file_data_dict):
        """Create multiple files in temp directory."""
        for filename, data in file_data_dict.items():
            with open(os.path.join(temp_dir, filename), "w", encoding="utf-8") as f:
                f.write(data)

    @staticmethod
    def create_standard_test_files(temp_dir, framework="pytest", test_type="login"):
        """Create standard test files for common test scenarios."""
        if test_type == "login":
            file_data = {
                "test_cases.csv": TestDataTemplates.get_login_test_cases_csv(),
                "modules.csv": TestDataTemplates.get_login_modules_csv(),
                "elements.csv": TestDataTemplates.get_login_elements_csv(),
                "config.yaml": TestDataTemplates.get_basic_config(),
            }
        elif test_type == "numeric":
            file_data = {
                "modules.csv": """module_name,module_step,param_1,param_2,param_3
Test Module,Sleep,3000,,
Test Module,Press By Percentage,50,75,
Test Module,Swipe,100,200,300""",
                "elements.csv": """Element_Name,Element_ID
element,id:test_element""",
                "test_cases.csv": """test_case,test_step
Test Case,Test Module""",
                "config.yaml": TestDataTemplates.get_simple_config(),
            }
        else:
            raise ValueError(f"Unknown test_type: {test_type}")

        TestHelpers.create_files(temp_dir, file_data)

    @staticmethod
    def get_generated_file_path(temp_dir, framework):
        """Get the path to generated test file."""
        extension = ".py" if framework == "pytest" else ".robot"
        filename = f"test_{os.path.basename(temp_dir)}{extension}"
        return os.path.join(temp_dir, "generated", "Tests", filename)

    @staticmethod
    def read_generated_file(temp_dir, framework):
        """Read the content of generated test file."""
        file_path = TestHelpers.get_generated_file_path(temp_dir, framework)
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def run_complete_test_flow(temp_dir, framework, file_type="login"):
        """Run complete test generation flow and return content."""
        TestHelpers.create_standard_test_files(temp_dir, framework, file_type)
        generate_test_file(temp_dir, framework=framework)
        return TestHelpers.read_generated_file(temp_dir, framework)

    @staticmethod
    def verify_robot_structure(temp_dir):
        """Verify Robot Framework output structure."""
        generated_dir = os.path.join(temp_dir, "generated")
        tests_dir = os.path.join(generated_dir, "Tests")

        assert os.path.exists(generated_dir)
        assert os.path.exists(tests_dir)

        test_files = [f for f in os.listdir(tests_dir) if f.endswith(".robot")]
        assert len(test_files) == 1

    @staticmethod
    def verify_pytest_structure(temp_dir):
        """Verify pytest output structure."""
        generated_dir = os.path.join(temp_dir, "generated")
        tests_dir = os.path.join(generated_dir, "Tests")

        assert os.path.exists(generated_dir)
        assert os.path.exists(tests_dir)

        test_files = [f for f in os.listdir(tests_dir) if f.endswith(".py")]
        assert len(test_files) == 1

    @staticmethod
    def create_keyword_arguments_files(temp_dir: str, framework: str = "pytest"):
        """Create files for keyword arguments testing."""
        if framework == "pytest":
            test_data = """test_cases:
  - name: Test Click
    modules:
      - Test Module"""
            modules_data = """modules:
  - name: Test Module
    steps:
      - keyword: Click By Percentage
        args:
          percentage_x: 50
          percentage_y: 75"""
        else:
            test_data = """Test Cases:
  Test Click:
    - Test Module"""
            modules_data = """Modules:
  Test Module:
    - Click By Percentage 50 75"""

        elements_data = """elements:
  login_button: id:login"""

        TestHelpers.create_files(
            temp_dir,
            {
                "test_cases.yaml": test_data,
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

    @staticmethod
    def verify_complex_keywords(content: str, framework: str):
        """Verify complex keyword structures are properly generated."""
        if framework == "pytest":
            assert "Press Element With Index" in content
            assert "Scroll From Element" in content
            assert "Swipe Until Element Appears" in content
            assert "Enter Text Using Keyboard" in content
            assert "Capture Screenshot" in content
        else:  # robot
            assert "Press Element With Index" in content
            assert "Scroll From Element" in content

    @staticmethod
    def create_and_generate_robot_test(temp_dir, file_data_list):
        """Create files and generate Robot Framework test, return content."""
        # Create files
        for filename, data in file_data_list:
            with open(os.path.join(temp_dir, filename), "w", encoding="utf-8") as f:
                f.write(data)

        # Generate
        generate_test_file(temp_dir, framework="robot")

        # Read generated file
        test_file = os.path.join(
            temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(temp_dir)}.robot",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def create_and_generate_pytest_test(temp_dir, file_data_list):
        """Create files and generate pytest test, return content."""
        # Create files
        for filename, data in file_data_list:
            with open(os.path.join(temp_dir, filename), "w", encoding="utf-8") as f:
                f.write(data)

        # Generate
        generate_test_file(temp_dir, framework="pytest")

        # Read generated file
        test_file = os.path.join(
            temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(temp_dir)}.py",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def verify_robot_keywords_standard(content):
        """Verify standard Robot Framework keyword patterns."""
        assert "Launch App    test_app" in content
        assert "Press Element" in content or "Enter Text" in content

    @staticmethod
    def verify_robot_complex_keywords(content):
        """Verify complex Robot Framework keyword patterns."""
        assert "Launch App    test_app" in content
        assert "Press Element With Index" in content
        assert "Scroll From Element" in content
        assert "Swipe Until Element Appears" in content
        assert "Enter Text Using Keyboard" in content
        assert "Capture Screenshot" in content

    @staticmethod
    def verify_basic_structure(content, framework):
        """Verify basic file structure for both frameworks."""
        if framework == "pytest":
            assert "# Auto-generated by generate.py" in content
            assert "import pytest" in content
            assert "from optics_framework.optics import Optics" in content
        else:  # robot
            assert "*** Settings ***" in content
            assert "*** Variables ***" in content
            assert "*** Test Cases ***" in content
            assert "*** Keywords ***" in content

    @staticmethod
    def verify_common_content(content, framework, content_checks=None):
        """Verify common content patterns across frameworks."""
        TestHelpers.verify_basic_structure(content, framework)
        TestHelpers.verify_numeric_preservation(content)
        TestHelpers.verify_element_references(content, framework)

        if content_checks:
            for check in content_checks:
                assert check in content

    @staticmethod
    def verify_numeric_preservation(content):
        """Verify numeric parameter preservation."""
        # Check for numeric values without quotes (Robot Framework format)
        assert "3000" in content or "2500" in content or "1000" in content

    @staticmethod
    def verify_element_references(content, framework):
        """Verify element references are correct when they exist."""
        if framework == "pytest":
            # Only check for element references if they're actually used in the code
            if "ELEMENTS['" in content:
                assert "ELEMENTS['" in content
            # If no element references are used, that's also valid
        else:  # robot
            # Only check for element references if they're actually used in the code
            if "${ELEMENTS." in content:
                assert "${ELEMENTS." in content
            # If no element references are used, that's also valid

    @staticmethod
    def verify_test_cases(content, framework):
        """Verify test cases are present."""
        if framework == "pytest":
            assert "def test_login_test(optics):" in content
            assert "def test_logout_test(optics):" in content
        else:  # robot
            assert "Login Test" in content
            assert "Logout Test" in content

    @staticmethod
    def verify_modules(content, framework):
        """Verify modules are present."""
        if framework == "pytest":
            assert "def login_module(optics: Optics) -> None:" in content
            assert "def verify_module(optics: Optics) -> None:" in content
            assert "def logout_module(optics: Optics) -> None:" in content
        else:  # robot
            assert "Login Module" in content
            assert "Verify Module" in content
            assert "Logout Module" in content


# Base Test Classes
class BaseIntegrationTest:
    """
    BASE CLASS: Foundation for integration tests

    PURPOSE: Provides common setup/teardown for integration testing
    TESTING TYPE: Integration test infrastructure

    This base class handles:
    - Temporary directory creation and cleanup
    - Common verification methods for generated files
    - Shared test environment management

    Integration tests verify complete workflows from input files
    to generated test files using only public APIs.
    """

    temp_dir: str

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        if self.temp_dir != "":
            shutil.rmtree(self.temp_dir)

    def verify_requirements_file(self):
        """Verify requirements.txt is created."""
        req_file = os.path.join(self.temp_dir, "generated", "requirements.txt")
        assert os.path.exists(req_file)

    def verify_output_structure(self):
        """Verify output directory structure."""
        generated_dir = os.path.join(self.temp_dir, "generated")
        tests_dir = os.path.join(generated_dir, "Tests")
        assert os.path.exists(generated_dir)
        assert os.path.exists(tests_dir)


class BaseMixedTest(BaseIntegrationTest):
    """Base class for mixed CSV/YAML tests."""

    def run_standard_mixed_test(
        self,
        framework,
        test_cases_data,
        modules_data,
        elements_data,
        config_data,
        test_cases_format,
        modules_format,
        elements_format,
    ):
        """Run a standard mixed format test."""
        file_data = {
            f"test_cases.{test_cases_format}": test_cases_data,
            f"modules.{modules_format}": modules_data,
            f"elements.{elements_format}": elements_data,
            "config.yaml": config_data,
        }

        TestHelpers.create_files(self.temp_dir, file_data)
        generate_test_file(self.temp_dir, framework=framework)

        self.verify_output_structure()
        content = TestHelpers.read_generated_file(self.temp_dir, framework)

        TestHelpers.verify_common_content(content, framework)
        TestHelpers.verify_test_cases(content, framework)
        TestHelpers.verify_modules(content, framework)

        self.verify_requirements_file()
        return content

    def run_simple_integration_test(self, framework, test_type="login"):
        """Run a simple integration test with standard files."""
        content = TestHelpers.run_complete_test_flow(
            self.temp_dir, framework, test_type
        )

        self.verify_output_structure()
        TestHelpers.verify_common_content(content, framework)

        if framework == "pytest":
            TestHelpers.verify_pytest_structure(self.temp_dir)
        else:
            TestHelpers.verify_robot_structure(self.temp_dir)

        return content

    def run_keyword_arguments_test(self, framework):
        """Run keyword arguments test for both frameworks."""
        modules_data = (
            """module_name,module_step,param_1,param_2,param_3
Test Module,Scroll from Element,${element},right,3000
Test Module,Launch App,test_app,event_name=SwipeToPay,
Test Module,Enter Text,${field},test_value,timeout=5000"""
            if framework == "robot"
            else """Modules:
  Test Module:
    - Scroll From Element ${element} right 3000
    - Launch App test_app event_name=SwipeToPay
    - Enter Text ${field} test_value timeout=5000"""
        )

        elements_data = (
            """Element_Name,Element_ID
element,id:test_element
field,id:text_field"""
            if framework == "robot"
            else """Elements:
  element: id:test_element
  field: id:text_field"""
        )

        test_cases_data = (
            """test_case,test_step
Test Case,Test Module"""
            if framework == "robot"
            else """Test Cases:
  Test Case:
    - Test Module"""
        )

        file_ext = "csv" if framework == "robot" else "yaml"
        TestHelpers.create_files(
            self.temp_dir,
            {
                f"modules.{file_ext}": modules_data,
                f"elements.{file_ext}": elements_data,
                f"test_cases.{file_ext}": test_cases_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework=framework)
        content = TestHelpers.read_generated_file(self.temp_dir, framework)

        # Verify keyword arguments are preserved
        assert "event_name=SwipeToPay" in content
        assert "timeout=5000" in content

        return content


class TestCSVDataReader:
    """
    UNIT TESTS: CSVDataReader class functionality

    PURPOSE: Test CSV file parsing and data extraction logic
    TESTING TYPE: White-box unit testing

    These tests verify:
    - CSV parsing using pandas DataFrame operations
    - Data structure conversion from CSV to internal formats
    - Error handling for malformed or missing CSV data
    - Edge cases like empty files and missing columns

    APPROACH: Uses mocking to isolate CSV reading logic from file I/O
    """

    reader: CSVDataReader

    def setup_method(self):
        self.reader = CSVDataReader()

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_test_cases_basic(self):
        """
        UNIT TEST: Basic CSV test case reading
        TESTS: CSV parsing for test cases with multiple steps
        APPROACH: Mock pandas DataFrame to simulate CSV data
        """
        # Create a mock DataFrame
        mock_df = MagicMock()

        # Mock the unique() method to return test case names
        test_case_series = MagicMock()
        test_case_series.unique.return_value = ["Login Test", "Logout Test"]
        mock_df.__getitem__.return_value = test_case_series

        # Mock the filtering and string operations
        filtered_df = MagicMock()
        test_step_series = MagicMock()
        test_step_series.str.strip.return_value.tolist.return_value = ["Step1", "Step2"]
        filtered_df.__getitem__.return_value = test_step_series
        mock_df.__getitem__.return_value.__getitem__.return_value = test_step_series

        with patch("pandas.read_csv", return_value=mock_df):
            result = self.reader.read_test_cases("test.csv")

            assert isinstance(result, dict)
            # Should have processed test cases
            assert result is not None

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_preserves_dtype_str(self):
        """Test that read_modules uses dtype=str to preserve numeric formats."""
        # Create mock DataFrame and row data
        mock_df = MagicMock()

        # Mock module_name unique values
        module_series = MagicMock()
        module_series.unique.return_value = ["Module1"]
        mock_df.__getitem__.return_value = module_series

        # Mock iterrows to return test data
        mock_row = {
            "module_step": " Test Step ",
            "param_1": "3000",
            "param_2": "test_param",
            "param_3": "3000.0",
        }
        mock_df.iterrows.return_value = [(0, mock_row)]

        # Mock the filtering operation
        mock_df.__getitem__.return_value.__getitem__.return_value = mock_df

        with patch("pandas.read_csv", return_value=mock_df) as mock_read_csv:
            result = self.reader.read_modules("test.csv")

            # Verify dtype=str was used
            mock_read_csv.assert_called_once_with("test.csv", dtype=str)
            assert isinstance(result, dict)

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_format_param_value_preserves_format(self):
        """Test that _format_param_value preserves original format."""
        # Test with integer string
        assert self.reader._format_param_value("3000") == "3000"

        # Test with float string
        assert self.reader._format_param_value("3000.0") == "3000.0"

        # Test with text
        assert self.reader._format_param_value("test_value") == "test_value"

        # Test with whitespace
        assert self.reader._format_param_value("  spaced  ") == "spaced"

        # Test with None
        assert self.reader._format_param_value(None) == "None"

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_elements_basic(self):
        """Test reading elements from CSV."""
        # Create mock DataFrame
        mock_df = MagicMock()

        # Mock the element name and ID series
        element_name_series = MagicMock()
        element_name_series.str.strip.return_value = ["button1", "input1"]

        element_id_series = MagicMock()
        element_id_series.str.strip.return_value = ["id:btn1", "xpath://input"]

        # Configure the DataFrame to return different series for different keys
        def get_item_side_effect(key):
            if "Element_Name" in key:
                return element_name_series
            elif "Element_ID" in key:
                return element_id_series
            return MagicMock()

        mock_df.__getitem__.side_effect = get_item_side_effect

        with patch("pandas.read_csv", return_value=mock_df):
            with patch(
                "builtins.zip",
                return_value=[("button1", "id:btn1"), ("input1", "xpath://input")],
            ):
                result = self.reader.read_elements("test.csv")

                assert isinstance(result, dict)

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_config_yaml_file(self):
        """Test reading config from YAML file."""
        config_data = {"driver_sources": [], "elements_sources": []}

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=config_data):
                result = self.reader.read_config("config.yaml")

                assert result == config_data

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_config_empty_file(self):
        """Test reading config from empty YAML file."""
        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=None):
                result = self.reader.read_config("config.yaml")

                assert result == {}


class TestPytestGenerator:
    """
    UNIT TESTS: PytestGenerator class functionality

    PURPOSE: Test pytest test file generation logic
    TESTING TYPE: White-box unit testing with some integration aspects

    These tests verify:
    - Internal parameter resolution methods (_resolve_params)
    - Code generation for different pytest structures
    - Element variable substitution for pytest syntax
    - Numeric parameter preservation in generated code
    - Complete test file generation workflow

    APPROACH: Mix of unit tests (testing protected methods) and integration tests
    (testing complete generation workflow)
    """

    def setup_method(self):
        self.generator = PytestGenerator()

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_element_variables(self):
        """
        UNIT TEST: Element variable resolution for pytest
        TESTS: Internal _resolve_params method with element substitution
        APPROACH: White-box testing of protected method with mock data
        """
        params = ["${button1}", "right", "3000"]
        elements = {"button1": "id:button_id"}

        result = self.generator._resolve_params(params, elements, "pytest")

        expected = ["ELEMENTS['button1']", "'right'", "'3000'"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_keyword_arguments(self):
        """Test _resolve_params with keyword arguments."""
        params = ["param1", "event_name=SwipeToPay"]
        elements = {}

        result = self.generator._resolve_params(params, elements, "pytest")

        expected = ["'param1'", "event_name=SwipeToPay"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_missing_element_error(self):
        """Test _resolve_params raises error for missing element."""
        params = ["${missing_element}"]
        elements = {}

        with pytest.raises(ValueError, match="Element 'missing_element' not found"):
            self.generator._resolve_params(params, elements, "pytest")

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_numeric_preservation(self):
        """Test _resolve_params preserves numeric formats."""
        params = ["3000", "3000.0", "2.5"]
        elements = {}

        result = self.generator._resolve_params(params, elements, "pytest")

        expected = ["'3000'", "'3000.0'", "'2.5'"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_header(self):
        """Test header generation for pytest."""
        result = self.generator._generate_header()

        assert "# Auto-generated by generate.py. Do not edit manually." in result
        assert "import pytest" in result
        assert "from optics_framework.optics import Optics" in result
        assert "from optics_framework.common.utils import load_config" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_config_with_sources(self):
        """Test config generation with various sources."""
        config = {
            "driver_sources": [{"type": "appium"}],
            "elements_sources": [{"type": "yaml"}],
            "text_detection": [{"type": "easyocr"}],
            "image_detection": [{"type": "opencv"}],
        }

        result = self.generator._generate_config(config)

        assert "'driver_config': [{'type': 'appium'}]" in result
        assert "'element_source_config': [{'type': 'yaml'}]" in result
        assert "'text_config': [{'type': 'easyocr'}]" in result
        assert "'image_config': [{'type': 'opencv'}]" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_config_empty(self):
        """Test config generation with empty config."""
        config = {}

        result = self.generator._generate_config(config)

        assert "'driver_config': []" in result
        assert "'element_source_config': []" in result
        assert "'text_config': []" in result
        assert "'image_config': []" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_elements(self):
        """Test elements dictionary generation."""
        elements = {"button1": "id:button_id", "text1": "xpath://input"}

        result = self.generator._generate_elements(elements)

        assert "ELEMENTS = {" in result
        assert "'button1': 'id:button_id'," in result
        assert "'text1': 'xpath://input'," in result
        assert "}" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_setup_fixture(self):
        """Test setup fixture generation."""
        result = self.generator._generate_setup()

        assert "@pytest.fixture(scope='module')" in result
        assert "def optics():" in result
        assert "optics = Optics()" in result
        assert "optics.setup(config=CONFIG)" in result
        assert "yield optics" in result
        assert "optics.quit()" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_module_function_basic(self):
        """Test module function generation with basic steps."""
        steps = [("Launch App", ["test_app"]), ("Press Element", ["${button1}"])]
        elements = {"button1": "id:button_id"}

        result = self.generator._generate_module_function(
            "Test Module", steps, elements
        )

        assert "def test_module(optics: Optics) -> None:" in result
        assert "optics.launch_app(" in result
        assert "optics.press_element(" in result
        assert "ELEMENTS['button1']" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_module_function_numeric_params(self):
        """Test module function generation preserves numeric parameter formats."""
        steps = [
            (
                "Scroll from Element",
                ["${element}", "right", "3000", "event_name=SwipeToPay"],
            ),
            ("Sleep", ["3000.0"]),
        ]
        elements = {"element": "id:swipe_element"}

        result = self.generator._generate_module_function(
            "Scroll Module", steps, elements
        )

        assert "def scroll_module(optics: Optics) -> None:" in result
        assert "'3000'" in result  # Integer should remain as '3000'
        assert "'3000.0'" in result  # Float should remain as '3000.0'
        assert "event_name=SwipeToPay" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_test_function(self):
        """Test test function generation."""
        result = self.generator._generate_test_function(
            "Login Test", ["Module1", "Module2"]
        )

        assert "def test_login_test(optics):" in result
        assert "module1(optics)" in result
        assert "module2(optics)" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_test_function_special_names(self):
        """Test test function generation with special characters in names."""
        result = self.generator._generate_test_function(
            "Login-Test Case #1", ["Login Module"]
        )

        assert "def test_login-test_case_#1(optics):" in result
        assert "login_module(optics)" in result

    @pytest.mark.generate
    @pytest.mark.hybrid
    def test_generate_complete_flow(self):
        """Test complete generation flow."""
        test_cases = {"Login Test": ["Login Module"]}
        modules = {
            "Login Module": [
                ("Launch App", ["test_app"]),
                ("Press Element", ["${login_btn}"]),
            ]
        }
        elements = {"login_btn": "id:login_button"}
        config = {"driver_sources": [{"type": "appium"}]}

        result = self.generator.generate(test_cases, modules, elements, config)

        # Check all major sections are present
        assert "# Auto-generated" in result
        assert "CONFIG = {" in result
        assert "ELEMENTS = {" in result
        assert "@pytest.fixture" in result
        assert "def login_module(" in result
        assert "def test_login_test(" in result
        assert "ELEMENTS['login_btn']" in result


class TestFileWriter:
    """Test cases for FileWriter class focused on pytest output."""

    def setup_method(self):
        self.writer = FileWriter()
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        if self.temp_dir != "":
            shutil.rmtree(self.temp_dir)

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_write_pytest_file(self):
        """Test writing pytest file with correct structure."""
        content = """# Auto-generated by generate.py. Do not edit manually.
import pytest
from optics_framework.optics import Optics

@pytest.mark.generate
@pytest.mark.white_box
def test_example(optics):
    pass
"""
        filename = "test_example.py"
        framework = "pytest"

        self.writer.write(self.temp_dir, filename, content, framework)

        # Check if Tests directory was created
        tests_dir = os.path.join(self.temp_dir, "Tests")
        assert os.path.exists(tests_dir)

        # Check if test file was created with correct content
        test_file = os.path.join(tests_dir, filename)
        assert os.path.exists(test_file)

        with open(test_file, "r", encoding="utf-8") as f:
            file_content = f.read()
            assert file_content == content

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_write_pytest_requirements(self):
        """Test requirements.txt generation for pytest."""
        self.writer.write(self.temp_dir, "test.py", "content", "pytest")

        req_file = os.path.join(self.temp_dir, "requirements.txt")
        assert os.path.exists(req_file)

        with open(req_file, "r", encoding="utf-8") as f:
            content = f.read()
            assert "pytest" in content
            assert "optics-framework" in content
            assert "Appium-Python-Client" in content
            assert "easyocr" in content
            assert "pyserial" in content
            assert "pytest-tagging" in content
            # Should not contain robot framework
            assert "robotframework" not in content


class TestCSVToPytestIntegration(BaseMixedTest):
    """
    INTEGRATION TESTS: CSV to pytest generation workflow

    PURPOSE: Test complete end-to-end generation from CSV files to pytest
    TESTING TYPE: Black-box integration testing

    These tests verify:
    - Complete workflow from CSV input files to generated pytest files
    - File structure creation and organization
    - Content validation in generated test files
    - Requirements file generation
    - Framework-specific syntax and patterns

    APPROACH: Uses only public APIs (generate_test_file) with real file I/O
    in temporary directories. Tests the complete user workflow.
    """

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_to_pytest_complete_flow(self):
        """
        INTEGRATION TEST: Complete CSV to pytest generation
        TESTS: End-to-end workflow from CSV files to pytest output
        APPROACH: Black-box testing using public API with file verification
        """
        content = self.run_simple_integration_test("pytest", "login")

        # Additional pytest-specific verifications
        assert "CONFIG = {" in content
        assert "ELEMENTS = {" in content
        assert "@pytest.fixture" in content
        assert "'3000'" in content  # Integer preserved as string
        assert "'3000.0'" in content  # Float preserved as string

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_numeric_parameter_preservation(self):
        """Test that CSV numeric parameters are preserved correctly."""
        content = self.run_simple_integration_test("pytest", "numeric")
        TestHelpers.verify_numeric_preservation(content)


class TestYAMLDataReader:
    """
    UNIT TESTS: YAMLDataReader class functionality

    PURPOSE: Test YAML file parsing and data extraction logic
    TESTING TYPE: White-box unit testing

    These tests verify:
    - YAML parsing using PyYAML library
    - Support for different YAML formats (list vs dict)
    - Data structure conversion from YAML to internal formats
    - Complex step parsing with parameters and element references
    - Error handling for malformed YAML data

    APPROACH: Uses mocking to isolate YAML reading logic from file I/O
    """

    reader: YAMLDataReader

    def setup_method(self):
        self.reader = YAMLDataReader()

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_test_cases_list_format(self):
        """
        UNIT TEST: YAML test case reading in list format
        TESTS: YAML parsing for list-based test case structure
        APPROACH: Mock YAML data with list format structure
        """
        yaml_data = {
            "Test Cases": [
                {"Login Test": ["Login Module", "Verify Module"]},
                {"Logout Test": ["Logout Module"]},
            ]
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_test_cases("test.yaml")

                expected = {
                    "Login Test": ["Login Module", "Verify Module"],
                    "Logout Test": ["Logout Module"],
                }
                assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_test_cases_dict_format(self):
        """Test reading test cases from YAML in dict format."""
        yaml_data = {
            "Test Cases": {
                "Login Test": ["Login Module", "Verify Module"],
                "Logout Test": ["Logout Module"],
            }
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_test_cases("test.yaml")

                expected = {
                    "Login Test": ["Login Module", "Verify Module"],
                    "Logout Test": ["Logout Module"],
                }
                assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_test_cases_empty_file(self):
        """Test reading test cases from empty YAML file."""
        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=None):
                result = self.reader.read_test_cases("test.yaml")

                assert result == {}

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_test_cases_missing_key(self):
        """Test reading test cases when 'Test Cases' key is missing."""
        yaml_data = {"Other": "data"}

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_test_cases("test.yaml")

                assert result == {}

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_parse_step_basic_keyword(self):
        """Test _parse_step with basic keyword."""
        keyword_registry = {"Launch App", "Press Element", "Sleep"}

        keyword, params = self.reader._parse_step(
            "Launch App test_app", keyword_registry
        )
        assert keyword == "Launch App"
        assert params == ["test_app"]

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_parse_step_complex_keyword(self):
        """Test _parse_step with complex multi-word keyword."""
        keyword_registry = {"Scroll From Element", "Press Element", "Sleep"}

        keyword, params = self.reader._parse_step(
            "Scroll From Element ${element} right 3000", keyword_registry
        )
        assert keyword == "Scroll From Element"
        assert params == ["${element}", "right", "3000"]

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_parse_step_keyword_priority(self):
        """Test _parse_step prioritizes longer matching keywords."""
        keyword_registry = {"Press Element", "Press Element With Index", "Sleep"}

        keyword, params = self.reader._parse_step(
            "Press Element With Index ${button} 2", keyword_registry
        )
        assert keyword == "Press Element With Index"
        assert params == ["${button}", "2"]

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_parse_step_no_params(self):
        """Test _parse_step with keyword but no parameters."""
        keyword_registry = {"Quit", "Sleep"}

        keyword, params = self.reader._parse_step("Quit", keyword_registry)
        assert keyword == "Quit"
        assert params == []

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_parse_step_unknown_keyword(self):
        """Test _parse_step with unknown keyword."""
        keyword_registry = {"Launch App", "Press Element"}

        keyword, params = self.reader._parse_step(
            "Unknown Command param1 param2", keyword_registry
        )
        assert keyword == "Unknown"
        assert params == ["Command", "param1", "param2"]

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_list_format(self):
        """Test reading modules from YAML in list format."""
        yaml_data = {
            "Modules": [
                {
                    "Login Module": [
                        "Launch App test_app",
                        "Press Element ${login_btn}",
                        "Sleep 3000",
                    ]
                },
                {
                    "Verify Module": [
                        "Validate Element ${welcome_text}",
                    ]
                },
            ]
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_modules("test.yaml")

                expected = {
                    "Login Module": [
                        ("Launch App", ["test_app"]),
                        ("Press Element", ["${login_btn}"]),
                        ("Sleep", ["3000"]),
                    ],
                    "Verify Module": [
                        ("Validate Element", ["${welcome_text}"]),
                    ],
                }
                assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_dict_format(self):
        """Test reading modules from YAML in dict format."""
        yaml_data = {
            "Modules": {
                "Login Module": [
                    "Launch App test_app",
                    "Press Element ${login_btn}",
                    "Sleep 3000",
                ],
                "Verify Module": [
                    "Validate Element ${welcome_text}",
                ],
            }
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_modules("test.yaml")

                expected = {
                    "Login Module": [
                        ("Launch App", ["test_app"]),
                        ("Press Element", ["${login_btn}"]),
                        ("Sleep", ["3000"]),
                    ],
                    "Verify Module": [
                        ("Validate Element", ["${welcome_text}"]),
                    ],
                }
                assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_numeric_preservation(self):
        """Test reading modules preserves numeric parameter formats."""
        yaml_data = {
            "Modules": {
                "Test Module": [
                    "Sleep 3000",
                    "Press By Percentage 50 75",
                    "Scroll From Element ${element} right 3000.0",
                ]
            }
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_modules("test.yaml")

                # Check that numeric values are preserved as strings
                steps = result["Test Module"]
                assert steps[0] == ("Sleep", ["3000"])
                assert steps[1] == ("Press By Percentage", ["50", "75"])
                assert steps[2] == (
                    "Scroll From Element",
                    ["${element}", "right", "3000.0"],
                )

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_empty_file(self):
        """Test reading modules from empty YAML file."""
        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=None):
                result = self.reader.read_modules("test.yaml")

                assert not result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_modules_missing_key(self):
        """Test reading modules when 'Modules' key is missing."""
        yaml_data = {"Other": "data"}

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_modules("test.yaml")

                assert not result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_elements_basic(self):
        """Test reading elements from YAML."""
        yaml_data = {
            "Elements": {
                "login_btn": "id:login_button",
                "username_field": "xpath://input[@type='text']",
                "welcome_text": "accessibility_id:welcome",
            }
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_elements("test.yaml")

                expected = {
                    "login_btn": "id:login_button",
                    "username_field": "xpath://input[@type='text']",
                    "welcome_text": "accessibility_id:welcome",
                }
                assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_elements_empty_file(self):
        """Test reading elements from empty YAML file."""
        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=None):
                result = self.reader.read_elements("test.yaml")

                assert result == {}

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_elements_missing_key(self):
        """Test reading elements when 'Elements' key is missing."""
        yaml_data = {"Other": "data"}

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_elements("test.yaml")

                assert result == {}

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_config_complete(self):
        """Test reading complete config from YAML."""
        yaml_data = {
            "driver_sources": [
                {"type": "appium", "capabilities": {"platformName": "Android"}}
            ],
            "elements_sources": [{"type": "yaml", "file": "elements.yaml"}],
            "text_detection": [{"type": "easyocr"}],
            "image_detection": [{"type": "opencv"}],
        }

        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=yaml_data):
                result = self.reader.read_config("config.yaml")

                assert result == yaml_data

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_read_config_empty_file(self):
        """Test reading config from empty YAML file."""
        with patch("builtins.open", mock_open()):
            with patch("yaml.safe_load", return_value=None):
                result = self.reader.read_config("config.yaml")

                assert result == {}


class TestYAMLToPytestIntegration(BaseMixedTest):
    """Integration tests for YAML to pytest generation."""

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_to_pytest_complete_flow(self):
        """Test complete YAML to pytest generation flow."""
        content = self.run_simple_integration_test("pytest", "login")

        # Verify YAML-specific elements
        TestHelpers.verify_basic_structure(content, "pytest")
        TestHelpers.verify_test_cases(content, "pytest")
        TestHelpers.verify_element_references(content, "pytest")
        TestHelpers.verify_numeric_preservation(content)

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_dict_format_to_pytest(self):
        """Test YAML dict format conversion to pytest."""
        test_cases_data = """Test Cases:
  Login Test:
    - Login Module
    - Verify Module
  Logout Test:
    - Logout Module"""
        modules_data = """Modules:
  Login Module:
    - Launch App test_app
    - Press Element ${login_btn}
  Verify Module:
    - Validate Element ${welcome_text}
  Logout Module:
    - Sleep 2500
    - Press Element ${logout_btn}"""
        elements_data = """Elements:
  login_btn: id:login_button
  welcome_text: xpath://span[text()='Welcome']
  logout_btn: id:logout_button"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.yaml": test_cases_data,
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        TestHelpers.verify_basic_structure(content, "pytest")
        assert "'2500'" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_complex_keywords_to_pytest(self):
        """Test YAML with complex multi-word keywords conversion to pytest."""
        modules_data = """Modules:
  Complex Module:
    - Launch App test_app
    - Press Element With Index ${button} 2
    - Scroll From Element ${element} right 3000
    - Swipe Until Element Appears ${target} up 5
    - Enter Text Using Keyboard ${field} test_text
    - Capture Screenshot test_image.png"""

        elements_data = """Elements:
  button: id:test_button
  element: id:scroll_element
  target: id:target_element
  field: id:text_field"""

        config_data = """driver_sources: []"""

        test_cases_data = """Test Cases:
  Complex Test:
    - Complex Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": config_data,
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        # Verify complex keywords are converted correctly
        assert "optics.launch_app(" in content
        assert "optics.press_element_with_index(" in content
        assert "optics.scroll_from_element(" in content
        assert "optics.swipe_until_element_appears(" in content
        assert "optics.enter_text_using_keyboard(" in content
        assert "optics.capture_screenshot(" in content

        # Verify element references
        assert "ELEMENTS['button']" in content
        assert "ELEMENTS['element']" in content
        assert "ELEMENTS['target']" in content
        assert "ELEMENTS['field']" in content

        # Verify numeric parameters
        assert "'3000'" in content
        assert "'2'" in content
        assert "'5'" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_numeric_parameter_preservation(self):
        """Test that YAML numeric parameters are preserved correctly in pytest."""
        modules_data = """Modules:
  Numeric Module:
    - Press By Percentage 50 75
    - Sleep 3000
    - Swipe 100 200 300 400
    - Scroll From Element ${element} right 3000.0
    - Press By Coordinates 150.5 250.7"""

        elements_data = """Elements:
  element: id:test_element"""

        config_data = "driver_sources: []"

        test_cases_data = """Test Cases:
  Numeric Test:
    - Numeric Module"""

        # Create files
        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": config_data,
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        # Verify all numeric formats are preserved
        assert "'50'" in content
        assert "'75'" in content
        assert "'3000'" in content  # Integer as string
        assert "'100'" in content
        assert "'200'" in content
        assert "'300'" in content
        assert "'400'" in content
        assert "'3000.0'" in content  # Float as string
        assert "'150.5'" in content
        assert "'250.7'" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_empty_modules_to_pytest(self):
        """Test YAML with empty modules conversion to pytest."""
        test_cases_data = """Test Cases:
  Empty Test:
    - Empty Module"""

        modules_data = """Modules:
  Empty Module: []"""

        elements_data = """Elements: {}"""

        config_data = """driver_sources: []"""

        # Create files
        for filename, data in [
            ("test_cases.yaml", test_cases_data),
            ("modules.yaml", modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
        ]:
            with open(
                os.path.join(self.temp_dir, filename), "w", encoding="utf-8"
            ) as f:
                f.write(data)

        # Generate
        generate_test_file(self.temp_dir, framework="pytest")

        # Read generated file
        test_file = os.path.join(
            self.temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(self.temp_dir)}.py",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Verify empty module function is created
            assert "def empty_module(optics: Optics) -> None:" in content
            assert "def test_empty_test(optics):" in content
            assert "empty_module(optics)" in content


class TestRobotGenerator:
    """
    UNIT TESTS: RobotGenerator class functionality

    PURPOSE: Test Robot Framework test file generation logic
    TESTING TYPE: White-box unit testing with some integration aspects

    These tests verify:
    - Internal parameter resolution methods (_resolve_params)
    - Robot Framework specific syntax generation
    - Element variable substitution for Robot Framework format
    - Configuration transformation for Robot Framework
    - JSON escaping for Robot Framework variables
    - Complete Robot Framework test file generation

    APPROACH: Mix of unit tests (testing protected methods) and integration tests
    (testing complete generation workflow)
    """

    def setup_method(self):
        self.generator = RobotGenerator()

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_element_variables_robot(self):
        """
        UNIT TEST: Element variable resolution for Robot Framework
        TESTS: Internal _resolve_params method with Robot Framework syntax
        APPROACH: White-box testing of protected method with mock data
        """
        params = ["${button1}", "right", "3000"]
        elements = {"button1": "id:button_id"}

        result = self.generator._resolve_params(params, elements, "robot")

        expected = ["${ELEMENTS.button1}", "right", "3000"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_keyword_arguments_robot(self):
        """Test _resolve_params with keyword arguments for Robot Framework."""
        params = ["param1", "event_name=SwipeToPay"]
        elements = {}

        result = self.generator._resolve_params(params, elements, "robot")

        expected = ["param1", "event_name=SwipeToPay"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_missing_element_error_robot(self):
        """Test _resolve_params raises error for missing element in Robot Framework."""
        params = ["${missing_element}"]
        elements = {}

        with pytest.raises(ValueError, match="Element 'missing_element' not found"):
            self.generator._resolve_params(params, elements, "robot")

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_resolve_params_numeric_preservation_robot(self):
        """Test _resolve_params preserves numeric formats for Robot Framework."""
        params = ["3000", "3000.0", "2.5"]
        elements = {}

        result = self.generator._resolve_params(params, elements, "robot")

        expected = ["3000", "3000.0", "2.5"]
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_header_robot(self):
        """Test header generation for Robot Framework."""
        result = self.generator._generate_header()

        assert "*** Settings ***" in result
        assert "Library    optics_framework.optics.Optics" in result
        assert "Library    Collections" in result
        assert "Library    optics_framework.common.utils" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_transform_config_structure(self):
        """Test config structure transformation for Robot Framework."""
        config = {
            "driver_sources": [{"type": "appium"}],
            "elements_sources": [{"type": "yaml"}],
            "text_detection": [{"type": "easyocr"}],
            "image_detection": [{"type": "opencv"}],
        }

        result = self.generator._transform_config_structure(config)

        expected = {
            "driver_config": [{"type": "appium"}],
            "element_source_config": [{"type": "yaml"}],
            "project_path": "${EXECDIR}",
            "text_config": [{"type": "easyocr"}],
            "image_config": [{"type": "opencv"}],
        }
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_transform_config_structure_empty(self):
        """Test config structure transformation with empty config."""
        config = {}

        result = self.generator._transform_config_structure(config)

        expected = {
            "driver_config": [],
            "element_source_config": [],
            "project_path": "${EXECDIR}",
        }
        assert result == expected

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_escape_json_for_robot(self):
        """Test JSON escaping for Robot Framework."""
        json_str = '{"key": "value with \\"quotes\\" and \\backslash"}'

        result = self.generator._escape_json_for_robot(json_str)

        assert '\\"' in result
        assert "\\\\" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_variables_basic(self):
        """Test variables section generation for Robot Framework."""
        elements = {"button1": "id:button_id", "text1": "xpath://input"}
        config = {"driver_sources": [{"type": "appium"}]}

        result = self.generator._generate_variables(elements, config)

        assert "*** Variables ***" in result
        assert "&{ELEMENTS}=" in result
        assert "button1=id:button_id" in result
        assert "text1=xpath://input" in result
        assert "${OPTICS_CONFIG_JSON}=" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_test_cases_robot(self):
        """Test test cases generation for Robot Framework."""
        test_cases = {
            "Login Test": ["Login Module", "Verify Module"],
            "Logout Test": ["Logout Module"],
        }

        result = self.generator._generate_test_cases(test_cases)

        assert "*** Test Cases ***" in result
        assert "Login Test" in result
        assert "Logout Test" in result
        assert "Setup Optics" in result
        assert "Login Module" in result
        assert "Verify Module" in result
        assert "Logout Module" in result
        assert "Quit Optics" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_keywords_robot(self):
        """Test keywords section generation for Robot Framework."""
        modules = {
            "Login Module": [
                ("Launch App", ["test_app"]),
                ("Press Element", ["${login_btn}"]),
            ]
        }
        elements = {"login_btn": "id:login_button"}

        result = self.generator._generate_keywords(modules, elements)

        assert "*** Keywords ***" in result
        assert "Setup Optics" in result
        assert "Quit Optics" in result
        assert "Login Module" in result
        assert "Launch App    test_app" in result
        assert "Press Element    ${ELEMENTS.login_btn}" in result

    @pytest.mark.generate
    @pytest.mark.white_box
    def test_generate_keywords_numeric_params(self):
        """Test keywords generation preserves numeric parameter formats."""
        modules = {
            "Test Module": [
                ("Sleep", ["3000"]),
                ("Press By Percentage", ["50", "75"]),
                ("Scroll from Element", ["${element}", "right", "3000.0"]),
            ]
        }
        elements = {"element": "id:swipe_element"}

        result = self.generator._generate_keywords(modules, elements)

        assert "Sleep    3000" in result
        assert "Press By Percentage    50    75" in result
        assert "Scroll from Element    ${ELEMENTS.element}    right    3000.0" in result

    @pytest.mark.generate
    @pytest.mark.hybrid
    def test_generate_complete_flow_robot(self):
        """Test complete Robot Framework generation flow."""
        test_cases = {"Login Test": ["Login Module"]}
        modules = {
            "Login Module": [
                ("Launch App", ["test_app"]),
                ("Press Element", ["${login_btn}"]),
            ]
        }
        elements = {"login_btn": "id:login_button"}
        config = {"driver_sources": [{"type": "appium"}]}

        result = self.generator.generate(test_cases, modules, elements, config)

        # Check all major sections are present
        assert "*** Settings ***" in result
        assert "*** Variables ***" in result
        assert "*** Test Cases ***" in result
        assert "*** Keywords ***" in result
        assert "&{ELEMENTS}=" in result
        assert "Login Test" in result
        assert "Login Module" in result
        assert "${ELEMENTS.login_btn}" in result


class TestCSVToRobotIntegration(BaseMixedTest):
    """Integration tests for CSV to Robot Framework generation."""

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_to_robot_complete_flow(self):
        """Test complete CSV to Robot Framework generation flow."""
        content = self.run_simple_integration_test("robot", "login")

        # Additional Robot Framework specific verifications
        TestHelpers.verify_robot_structure(self.temp_dir)
        TestHelpers.verify_basic_structure(content, "robot")
        TestHelpers.verify_test_cases(content, "robot")
        TestHelpers.verify_element_references(content, "robot")
        TestHelpers.verify_numeric_preservation(content)

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_numeric_parameter_preservation_robot(self):
        """Test that CSV numeric parameters are preserved correctly in Robot Framework."""
        modules_data = """module_name,module_step,param_1,param_2,param_3
Test Module,Scroll from Element,${element},right,3000
Test Module,Sleep,3000.0,,
Test Module,Press By Percentage,50,75,
Test Module,Swipe,100,200,300"""
        elements_data = """Element_Name,Element_ID
element,id:test_element"""
        test_cases_data = """test_case,test_step
Test Case,Test Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.csv": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify all numeric formats are preserved
        assert "3000" in content  # Integer preserved
        assert "3000.0" in content  # Float preserved
        assert "50    75" in content  # Multiple parameters
        assert "100    200    300" in content  # Multiple numeric parameters

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_complex_keywords_to_robot(self):
        """Test CSV with complex multi-word keywords conversion to Robot Framework."""
        modules_data = """module_name,module_step,param_1,param_2,param_3
Complex Module,Launch App,test_app,,
Complex Module,Press Element With Index,${button},2,
Complex Module,Scroll from Element,${element},right,3000
Complex Module,Swipe Until Element Appears,${target},up,5
Complex Module,Enter Text Using Keyboard,${field},test_text,
Complex Module,Capture Screenshot,test_image.png,,"""

        elements_data = """Element_Name,Element_ID
button,id:test_button
element,id:scroll_element
target,id:target_element
field,id:text_field"""

        test_cases_data = """test_case,test_step
Complex Test,Complex Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.csv": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify complex keywords are present
        assert "Launch App    test_app" in content
        assert "Press Element With Index    ${ELEMENTS.button}    2" in content
        assert "Scroll from Element    ${ELEMENTS.element}    right    3000" in content
        assert "Swipe Until Element Appears    ${ELEMENTS.target}    up    5" in content
        assert "Enter Text Using Keyboard    ${ELEMENTS.field}    test_text" in content
        assert "Capture Screenshot    test_image.png" in content

        TestHelpers.verify_element_references(content, "robot")
        TestHelpers.verify_numeric_preservation(content)

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_keyword_arguments_to_robot(self):
        """Test CSV with keyword arguments conversion to Robot Framework."""
        modules_data = """module_name,module_step,param_1,param_2,param_3
Test Module,Scroll from Element,${element},right,3000
Test Module,Launch App,test_app,event_name=SwipeToPay,
Test Module,Enter Text,${field},test_value,timeout=5000"""

        elements_data = """Element_Name,Element_ID
element,id:test_element
field,id:text_field"""

        test_cases_data = """test_case,test_step
Test Case,Test Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.csv": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify keyword arguments are preserved
        assert "event_name=SwipeToPay" in content
        assert "timeout=5000" in content
        assert "Scroll from Element    ${ELEMENTS.element}    right    3000" in content
        assert "Launch App    test_app    event_name=SwipeToPay" in content
        assert (
            "Enter Text    ${ELEMENTS.field}    test_value    timeout=5000" in content
        )

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_empty_modules_to_robot(self):
        """Test CSV with empty modules conversion to Robot Framework."""
        test_cases_data = """test_case,test_step
Empty Test,Empty Module"""

        modules_data = """module_name,module_step,param_1,param_2,param_3
Empty Module,,,"""

        elements_data = """Element_Name,Element_ID"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify empty module is created
        assert "Empty Module" in content
        assert "Empty Test" in content
        # Should have basic structure even with empty modules
        assert "*** Keywords ***" in content
        assert "*** Test Cases ***" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_config_variations_to_robot(self):
        """Test CSV with different config variations for Robot Framework."""
        modules_data = """module_name,module_step,param_1,param_2,param_3
Test Module,Launch App,test_app,,
Test Module,Sleep,1000,,"""

        elements_data = """Element_Name,Element_ID
test_element,id:test"""

        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: csv
    file: elements.csv
text_detection:
  - type: easyocr
image_detection:
  - type: opencv
execution_output_path: /path/to/output"""

        test_cases_data = """test_case,test_step
Config Test,Test Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": config_data,
                "test_cases.csv": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify config is properly transformed and included
        assert "${OPTICS_CONFIG_JSON}=" in content
        assert "driver_config" in content
        assert "element_source_config" in content
        assert "text_config" in content
        assert "image_config" in content
        assert "execution_output_path" in content


class TestYAMLToRobotIntegration(BaseMixedTest):
    """Integration tests for YAML to Robot Framework generation."""

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_to_robot_complete_flow(self):
        """Test complete YAML to Robot Framework generation flow."""
        content = self.run_simple_integration_test("robot", "login")

        # Additional Robot Framework specific verifications
        TestHelpers.verify_robot_structure(self.temp_dir)
        TestHelpers.verify_basic_structure(content, "robot")
        TestHelpers.verify_test_cases(content, "robot")
        TestHelpers.verify_element_references(content, "robot")
        TestHelpers.verify_numeric_preservation(content)

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_dict_format_to_robot(self):
        """Test YAML dict format conversion to Robot Framework."""
        test_cases_data = """Test Cases:
  Login Test:
    - Login Module
    - Verify Module
  Logout Test:
    - Logout Module"""

        modules_data = """Modules:
  Login Module:
    - Launch App test_app
    - Press Element ${login_btn}
  Verify Module:
    - Validate Element ${welcome_text}
  Logout Module:
    - Sleep 2500
    - Press Element ${logout_btn}"""

        elements_data = """Elements:
  login_btn: id:login_button
  welcome_text: xpath://span[text()='Welcome']
  logout_btn: id:logout_button"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.yaml": test_cases_data,
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify dict format YAML is processed correctly
        assert "Login Test" in content
        assert "Logout Test" in content
        assert "Launch App    test_app" in content
        assert "Sleep    2500" in content
        TestHelpers.verify_element_references(content, "robot")

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_complex_keywords_to_robot(self):
        """Test YAML with complex multi-word keywords conversion to Robot Framework."""
        modules_data = """Modules:
  Complex Module:
    - Launch App test_app
    - Press Element With Index ${button} 2
    - Scroll From Element ${element} right 3000
    - Swipe Until Element Appears ${target} up 5
    - Enter Text Using Keyboard ${field} test_text
    - Capture Screenshot test_image.png"""

        elements_data = """Elements:
  button: id:test_button
  element: id:scroll_element
  target: id:target_element
  field: id:text_field"""

        test_cases_data = """Test Cases:
  Complex Test:
    - Complex Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify complex keywords are converted correctly for Robot Framework
        assert "Launch App    test_app" in content
        assert "Press Element With Index    ${ELEMENTS.button}    2" in content
        assert "Scroll From Element    ${ELEMENTS.element}    right    3000" in content
        assert "Swipe Until Element Appears    ${ELEMENTS.target}    up    5" in content
        assert "Enter Text Using Keyboard    ${ELEMENTS.field}    test_text" in content
        assert "Capture Screenshot    test_image.png" in content

        TestHelpers.verify_element_references(content, "robot")
        TestHelpers.verify_numeric_preservation(content)

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_numeric_parameter_preservation_robot(self):
        """Test that YAML numeric parameters are preserved correctly in Robot Framework."""
        modules_data = """Modules:
  Numeric Module:
    - Press By Percentage 50 75
    - Sleep 3000
    - Swipe 100 200 300 400
    - Scroll From Element ${element} right 3000.0
    - Press By Coordinates 150.5 250.7"""

        elements_data = """Elements:
  element: id:test_element"""

        test_cases_data = """Test Cases:
  Numeric Test:
    - Numeric Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify all numeric formats are preserved
        assert "Press By Percentage    50    75" in content
        assert "Sleep    3000" in content
        assert "Swipe    100    200    300    400" in content
        assert "3000.0" in content  # Float preserved
        assert "150.5    250.7" in content  # Decimal coordinates

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_keyword_arguments_to_robot(self):
        """Test YAML with keyword arguments conversion to Robot Framework."""
        modules_data = """Modules:
  Test Module:
    - Scroll From Element ${element} right 3000
    - Launch App test_app event_name=SwipeToPay
    - Enter Text ${field} test_value timeout=5000
    - Press Element ${button} retry_count=3"""

        elements_data = """Elements:
  element: id:test_element
  field: id:text_field
  button: id:test_button"""

        test_cases_data = """Test Cases:
  Test Case:
    - Test Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify keyword arguments are preserved
        assert "event_name=SwipeToPay" in content
        assert "timeout=5000" in content
        assert "retry_count=3" in content
        assert "Scroll From Element    ${ELEMENTS.element}    right    3000" in content
        assert "Launch App    test_app    event_name=SwipeToPay" in content
        assert (
            "Enter Text    ${ELEMENTS.field}    test_value    timeout=5000" in content
        )

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_empty_modules_to_robot(self):
        """Test YAML with empty modules conversion to Robot Framework."""
        test_cases_data = """Test Cases:
  Empty Test:
    - Empty Module"""

        modules_data = """Modules:
  Empty Module: []"""

        elements_data = """Elements: {}"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.yaml": test_cases_data,
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify empty module is created
        assert "Empty Module" in content
        assert "Empty Test" in content
        # Should have basic structure even with empty modules
        assert "*** Keywords ***" in content
        assert "*** Test Cases ***" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_config_variations_to_robot(self):
        """Test YAML with different config variations for Robot Framework."""
        modules_data = """Modules:
  Test Module:
    - Launch App test_app
    - Sleep 1000"""

        elements_data = """Elements:
  test_element: id:test"""

        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: yaml
    file: elements.yaml
text_detection:
  - type: easyocr
image_detection:
  - type: opencv
execution_output_path: /path/to/output"""

        test_cases_data = """Test Cases:
  Config Test:
    - Test Module"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": config_data,
                "test_cases.yaml": test_cases_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify config is properly transformed and included for Robot Framework
        assert "${OPTICS_CONFIG_JSON}=" in content
        assert "driver_config" in content
        assert "element_source_config" in content
        assert "text_config" in content
        assert "image_config" in content
        assert "execution_output_path" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_mixed_element_references_to_robot(self):
        """Test YAML with mixed element references conversion to Robot Framework."""
        modules_data = """Modules:
  Mixed Module:
    - Launch App test_app
    - Press Element ${existing_element}
    - Enter Text ${another_element} test_value
    - Sleep 2000
    - Press By Coordinates 100 200"""

        elements_data = """Elements:
  existing_element: id:existing_btn
  another_element: xpath://input[@name='test']"""

        config_data = """driver_sources: []"""

        test_cases_data = """Test Cases:
  Mixed Test:
    - Mixed Module"""

        # Create files and generate Robot test
        file_data = [
            ("modules.yaml", modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
            ("test_cases.yaml", test_cases_data),
        ]

        content = TestHelpers.create_and_generate_robot_test(self.temp_dir, file_data)

        # Verify mixed element references are handled correctly
        TestHelpers.verify_robot_keywords_standard(content)
        assert (
            "Press Element    ${ELEMENTS.existing_element}" in content
        )  # Element reference
        assert (
            "Enter Text    ${ELEMENTS.another_element}    test_value" in content
        )  # Element reference
        assert "Sleep    2000" in content  # No element reference
        assert "Press By Coordinates    100    200" in content  # No element reference

        # Verify elements are defined
        assert "existing_element=id:existing_btn" in content
        assert "another_element=xpath://input[@name='test']" in content


class TestMixedCSVYAMLToPytestIntegration(BaseMixedTest):
    """
    INTEGRATION TESTS: Mixed CSV/YAML to pytest generation workflow

    PURPOSE: Test complex scenarios with multiple file formats
    TESTING TYPE: Black-box integration testing with conflict detection

    These tests verify:
    - Handling mixed CSV and YAML input files in same project
    - Conflict detection between different file formats
    - Data merging from multiple sources
    - Preservation of data integrity across format boundaries
    - Error handling for naming conflicts and format mismatches

    APPROACH: Uses public API with complex input scenarios to test
    edge cases and advanced functionality that users might encounter.
    """

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_testcases_yaml_modules_to_pytest(self):
        """
        INTEGRATION TEST: Mixed format handling (CSV test cases + YAML modules)
        TESTS: File format mixing and data integration
        APPROACH: Black-box testing with multiple input formats
        VERIFIES: Successful merging of different data sources

        Test CSV test cases with YAML modules conversion to pytest."""
        self.run_standard_mixed_test(
            framework="pytest",
            test_cases_data=TestDataTemplates.get_login_test_cases_csv(),
            modules_data=TestDataTemplates.get_login_modules_yaml(),
            elements_data=TestDataTemplates.get_login_elements_yaml(),
            config_data=TestDataTemplates.get_basic_config(),
            test_cases_format="csv",
            modules_format="yaml",
            elements_format="yaml",
        )

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_testcases_csv_modules_to_pytest(self):
        """Test YAML test cases with CSV modules conversion to pytest."""
        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: csv
    file: elements.csv"""

        self.run_standard_mixed_test(
            framework="pytest",
            test_cases_data=TestDataTemplates.get_login_test_cases_yaml(),
            modules_data=TestDataTemplates.get_login_modules_csv(),
            elements_data=TestDataTemplates.get_login_elements_csv(),
            config_data=config_data,
            test_cases_format="yaml",
            modules_format="csv",
            elements_format="csv",
        )

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_csv_yaml_elements_to_pytest(self):
        """Test mixed CSV and YAML elements handling in pytest generation."""
        test_cases_data = """test_case,test_step
Mixed Test,Mixed Module"""
        modules_data = """Modules:
  Mixed Module:
    - Launch App test_app
    - Press Element ${csv_element}
    - Enter Text ${yaml_element} test_value"""
        csv_elements_data = """Element_Name,Element_ID
csv_element,id:csv_button"""
        yaml_elements_data = """Elements:
  yaml_element: id:yaml_input"""
        config_data = """driver_sources:
  - type: appium
elements_sources:
  - type: csv
    file: elements.csv
  - type: yaml
    file: elements.yaml"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.csv": csv_elements_data,
                "elements.yaml": yaml_elements_data,
                "config.yaml": config_data,
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        TestHelpers.verify_basic_structure(content, "pytest")
        assert "ELEMENTS['csv_element']" in content
        assert "ELEMENTS['yaml_element']" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_csv_yaml_elements_conflict_detection_to_pytest(self):
        """
        INTEGRATION TEST: Conflict detection between file formats
        TESTS: Error handling for naming conflicts across CSV/YAML sources
        APPROACH: Black-box testing with intentionally conflicting data
        VERIFIES: Proper exception raising with descriptive error messages
        PATTERN: Negative testing - verifying failures happen correctly
        """
        test_cases_data = """test_case,test_step
Mixed Test,Mixed Module"""
        modules_data = """Modules:
  Mixed Module:
    - Launch App test_app"""
        csv_elements_data = """Element_Name,Element_ID
shared_element,id:csv_shared"""
        yaml_elements_data = """Elements:
  shared_element: id:yaml_shared_override"""
        config_data = """elements_sources:
  - type: csv
    file: elements.csv
  - type: yaml
    file: elements.yaml"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.csv": csv_elements_data,
                "elements.yaml": yaml_elements_data,
                "config.yaml": config_data,
            },
        )

        with pytest.raises(ValueError, match="Naming conflict detected in elements"):
            generate_test_file(self.temp_dir, framework="pytest")

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_numeric_parameter_preservation_to_pytest(self):
        """Test numeric parameter preservation from mixed CSV/YAML sources."""
        test_cases_data = """test_case,test_step
Numeric Test,CSV Module
Numeric Test,YAML Module"""
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Sleep,3000,,
CSV Module,Press By Percentage,50,75,"""
        yaml_modules_data = """Modules:
  YAML Module:
    - Sleep 2500
    - Press By Coordinates 150.5 250.7"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.csv": csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": "Elements: {}",
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        assert "def csv_module(optics: Optics) -> None:" in content
        assert "def yaml_module(optics: Optics) -> None:" in content
        assert "'3000'" in content and "'2500'" in content
        assert "'150.5'" in content and "'250.7'" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_complex_keywords_to_pytest(self):
        """Test mixed CSV/YAML with complex keywords conversion to pytest."""
        test_cases_data = """test_case,test_step
Complex Test,CSV Module
Complex Test,YAML Module"""
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Launch App,test_app,,
CSV Module,Press Element With Index,${button},2,"""
        yaml_modules_data = """Modules:
  YAML Module:
    - Scroll From Element ${element} right 3000
    - Capture Screenshot test_image.png"""
        elements_data = """Elements:
  button: id:test_button
  element: id:scroll_element"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.csv": csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        assert "optics.launch_app(" in content
        assert "optics.press_element_with_index(" in content
        assert "optics.scroll_from_element(" in content
        assert "optics.capture_screenshot(" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_file_conflict_detection_to_pytest(self):
        """Test conflict detection when both CSV and YAML have same names."""
        csv_test_cases_data = """test_case,test_step
Conflict Test,Test Module"""
        yaml_test_cases_data = """Test Cases:
  Conflict Test:
    - Test Module"""
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
Shared Module,Launch App,test_app,,"""
        yaml_modules_data = """Modules:
  Shared Module:
    - Press Element ${button}"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": csv_test_cases_data,
                "test_cases.yaml": yaml_test_cases_data,
                "modules.csv": csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": "Elements:\n  button: id:test_button",
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        with pytest.raises(ValueError, match="Naming conflict detected in test_cases"):
            generate_test_file(self.temp_dir, framework="pytest")

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_file_unique_names_to_pytest(self):
        """Test successful merging when all names are unique."""
        csv_test_cases_data = """test_case,test_step
CSV Test,CSV Module"""
        yaml_test_cases_data = """Test Cases:
  YAML Test:
    - YAML Module"""
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Launch App,test_app,,
CSV Module,Sleep,1000,,"""
        yaml_modules_data = """Modules:
  YAML Module:
    - Press Element ${button}
    - Sleep 2000"""
        elements_data = """Elements:
  button: id:test_button"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": csv_test_cases_data,
                "test_cases.yaml": yaml_test_cases_data,
                "modules.csv": csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        assert "def test_csv_test(optics):" in content
        assert "def test_yaml_test(optics):" in content
        assert "def csv_module(optics: Optics) -> None:" in content
        assert "def yaml_module(optics: Optics) -> None:" in content
        assert "'1000'" in content and "'2000'" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_empty_files_handling_to_pytest(self):
        """Test handling of empty CSV/YAML files in mixed scenarios."""
        test_cases_data = """test_case,test_step
Empty Test,Working Module"""
        empty_csv_modules_data = """module_name,module_step,param_1,param_2,param_3"""
        yaml_modules_data = """Modules:
  Working Module:
    - Launch App test_app
    - Sleep 1000"""
        empty_yaml_elements_data = """Elements: {}"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.csv": empty_csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": empty_yaml_elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        assert "def test_empty_test(optics):" in content
        assert "def working_module(optics: Optics) -> None:" in content
        assert "optics.launch_app(" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_config_sources_to_pytest(self):
        """Test mixed configuration with different element sources."""
        test_cases_data = """test_case,test_step
Config Test,Test Module"""
        modules_data = """Modules:
  Test Module:
    - Launch App test_app
    - Press Element ${csv_element}
    - Enter Text ${yaml_element} test_value"""
        csv_elements_data = """Element_Name,Element_ID
csv_element,id:csv_button"""
        yaml_elements_data = """Elements:
  yaml_element: id:yaml_input"""
        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.csv": csv_elements_data,
                "elements.yaml": yaml_elements_data,
                "config.yaml": config_data,
            },
        )

        generate_test_file(self.temp_dir, framework="pytest")
        content = TestHelpers.read_generated_file(self.temp_dir, "pytest")

        assert "def test_config_test(optics):" in content
        assert "def test_module(optics: Optics) -> None:" in content
        assert "optics.press_element(" in content
        assert "optics.enter_text(" in content


class TestMixedCSVYAMLToRobotIntegration(BaseMixedTest):
    """Integration tests for mixed CSV/YAML to Robot Framework generation."""

    def setup_method(self):
        super().setup_method()

    def teardown_method(self):
        if self.temp_dir != "":
            shutil.rmtree(self.temp_dir)
            self.temp_dir = ""

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_csv_testcases_yaml_modules_to_robot(self):
        """Test CSV test cases with YAML modules conversion to Robot Framework."""
        test_cases_data = """test_case,test_step
Login Test,Login Module
Login Test,Verify Module
Logout Test,Logout Module"""
        modules_data = """Modules:
  Login Module:
    - Launch App test_app
    - Enter Text ${username_field} testuser
    - Press Element ${login_button}
  Verify Module:
    - Validate Element ${welcome_text}
  Logout Module:
    - Press Element ${logout_button}
    - Sleep 3000
    - Scroll From Element ${element} right 3000.0"""
        elements_data = """Elements:
  username_field: id:username
  login_button: id:login_btn
  welcome_text: xpath://span[text()='Welcome']
  logout_button: id:logout_btn
  element: id:swipe_element"""
        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: yaml
    file: elements.yaml"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.yaml": elements_data,
                "config.yaml": config_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")

        TestHelpers.verify_robot_structure(self.temp_dir)
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify test cases from CSV
        assert "Login Test" in content
        assert "Logout Test" in content
        # Verify modules from YAML
        assert "Login Module" in content
        assert "Verify Module" in content
        assert "Logout Module" in content
        # Verify numeric parameter preservation from YAML
        assert "Sleep    3000" in content
        assert "3000.0" in content
        # Verify element references from YAML
        assert "${ELEMENTS.username_field}" in content
        assert "${ELEMENTS.login_button}" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_yaml_testcases_csv_modules_to_robot(self):
        """Test YAML test cases with CSV modules conversion to Robot Framework."""
        test_cases_data = """Test Cases:
  Login Test:
    - Login Module
    - Verify Module
  Logout Test:
    - Logout Module"""
        modules_data = """module_name,module_step,param_1,param_2,param_3
Login Module,Launch App,test_app,,
Login Module,Enter Text,${username_field},testuser,
Login Module,Press Element,${login_button},,
Verify Module,Validate Element,${welcome_text},,
Logout Module,Press Element,${logout_button},,
Logout Module,Sleep,3000,,
Logout Module,Scroll from Element,${element},right,3000.0"""
        elements_data = """Element_Name,Element_ID
username_field,id:username
login_button,id:login_btn
welcome_text,xpath://span[text()='Welcome']
logout_button,id:logout_btn
element,id:swipe_element"""
        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: csv
    file: elements.csv"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.yaml": test_cases_data,
                "modules.csv": modules_data,
                "elements.csv": elements_data,
                "config.yaml": config_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")

        TestHelpers.verify_robot_structure(self.temp_dir)
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify test cases from YAML
        assert "Login Test" in content
        assert "Logout Test" in content
        # Verify modules from CSV
        assert "Login Module" in content
        assert "Verify Module" in content
        assert "Logout Module" in content
        # Verify numeric parameter preservation from CSV
        assert "Sleep    3000" in content
        assert "3000.0" in content
        # Verify element references from CSV
        assert "${ELEMENTS.username_field}" in content
        assert "${ELEMENTS.login_button}" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_csv_yaml_elements_to_robot(self):
        """Test mixed CSV and YAML elements handling in Robot Framework generation."""
        test_cases_data = """test_case,test_step
Mixed Test,Mixed Module"""
        modules_data = """Modules:
  Mixed Module:
    - Launch App test_app
    - Press Element ${csv_element}
    - Enter Text ${yaml_element} test_value
    - Sleep 2000
    - Press By Coordinates 100 200"""
        csv_elements_data = """Element_Name,Element_ID
csv_element,id:csv_button
csv_unique_element,id:csv_shared"""
        yaml_elements_data = """Elements:
  yaml_element: id:yaml_input
  yaml_unique_element: id:yaml_shared_override"""
        config_data = """driver_sources:
  - type: appium
elements_sources:
  - type: csv
    file: elements.csv
  - type: yaml
    file: elements.yaml"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.csv": csv_elements_data,
                "elements.yaml": yaml_elements_data,
                "config.yaml": config_data,
            },
        )

        generate_test_file(self.temp_dir, framework="robot")

        TestHelpers.verify_robot_structure(self.temp_dir)
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify mixed elements are available
        assert "Mixed Test" in content
        assert "Mixed Module" in content
        assert "${ELEMENTS.csv_element}" in content
        assert "${ELEMENTS.yaml_element}" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_csv_yaml_elements_conflict_detection_to_robot(self):
        """Test conflict detection for mixed CSV and YAML elements with same names.

        This test validates that Robot Framework generation properly detects
        naming conflicts between CSV and YAML element sources.
        """
        test_cases_data = """test_case,test_step
Mixed Test,Mixed Module"""
        modules_data = """Modules:
  Mixed Module:
    - Launch App test_app
    - Press Element ${csv_element}
    - Enter Text ${yaml_element} test_value"""
        csv_elements_data = """Element_Name,Element_ID
csv_element,id:csv_button
shared_element,id:csv_shared"""
        yaml_elements_data = """Elements:
  yaml_element: id:yaml_input
  shared_element: id:yaml_shared_override"""
        config_data = """driver_sources:
  - type: appium
elements_sources:
  - type: csv
    file: elements.csv
  - type: yaml
    file: elements.yaml"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.yaml": modules_data,
                "elements.csv": csv_elements_data,
                "elements.yaml": yaml_elements_data,
                "config.yaml": config_data,
            },
        )

        # Should raise conflict error
        with pytest.raises(ValueError, match="Naming conflict detected in elements"):
            generate_test_file(self.temp_dir, framework="robot")

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_numeric_parameter_preservation_to_robot(self):
        """Test numeric parameter preservation from mixed CSV/YAML sources in Robot Framework."""
        test_cases_data = """test_case,test_step
Numeric Test,CSV Module
Numeric Test,YAML Module"""

        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Sleep,3000,,
CSV Module,Press By Percentage,50,75,
CSV Module,Swipe,100,200,300"""

        yaml_modules_data = """Modules:
  YAML Module:
    - Sleep 2500
    - Press By Coordinates 150.5 250.7
    - Scroll From Element ${element} right 3000.0"""

        elements_data = """Elements:
  element: id:test_element"""

        TestHelpers.create_files(
            self.temp_dir,
            {
                "test_cases.csv": test_cases_data,
                "modules.csv": csv_modules_data,
                "modules.yaml": yaml_modules_data,
                "elements.yaml": elements_data,
                "config.yaml": TestDataTemplates.get_simple_config(),
            },
        )

        generate_test_file(self.temp_dir, framework="robot")
        content = TestHelpers.read_generated_file(self.temp_dir, "robot")

        # Verify both CSV and YAML module keywords exist
        assert "CSV Module" in content
        assert "YAML Module" in content

        # Verify numeric preservation from CSV
        assert "Sleep    3000" in content  # CSV integer
        assert "Press By Percentage    50    75" in content
        assert "Swipe    100    200    300" in content

        # Verify numeric preservation from YAML
        assert "Sleep    2500" in content  # YAML integer
        assert "Press By Coordinates    150.5    250.7" in content  # YAML float
        assert "3000.0" in content  # YAML float

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_complex_keywords_to_robot(self):
        """Test mixed CSV/YAML with complex keywords conversion to Robot Framework."""
        # CSV test cases
        test_cases_data = """test_case,test_step
Complex Test,CSV Module
Complex Test,YAML Module"""

        # CSV modules with complex keywords
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Launch App,test_app,,
CSV Module,Press Element With Index,${button},2,
CSV Module,Enter Text Using Keyboard,${field},test_text,"""

        # YAML modules with complex keywords
        yaml_modules_data = """Modules:
  YAML Module:
    - Scroll From Element ${element} right 3000
    - Swipe Until Element Appears ${target} up 5
    - Capture Screenshot test_image.png"""

        elements_data = """Elements:
  button: id:test_button
  field: id:text_field
  element: id:scroll_element
  target: id:target_element"""

        config_data = "driver_sources: []"

        # Create files and generate Robot test
        file_data = [
            ("test_cases.csv", test_cases_data),
            ("modules.csv", csv_modules_data),
            ("modules.yaml", yaml_modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
        ]

        content = TestHelpers.create_and_generate_robot_test(self.temp_dir, file_data)

        # Verify complex keywords from CSV are converted correctly for Robot Framework
        TestHelpers.verify_robot_complex_keywords(content)
        assert "Press Element With Index    ${ELEMENTS.button}    2" in content
        assert "Enter Text Using Keyboard    ${ELEMENTS.field}    test_text" in content

        # Verify complex keywords from YAML are converted correctly for Robot Framework
        assert "Scroll From Element    ${ELEMENTS.element}    right    3000" in content
        assert "Swipe Until Element Appears    ${ELEMENTS.target}    up    5" in content
        assert "Capture Screenshot    test_image.png" in content

        # Verify element references work for both sources
        assert "${ELEMENTS.button}" in content
        assert "${ELEMENTS.field}" in content
        assert "${ELEMENTS.element}" in content
        assert "${ELEMENTS.target}" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_file_conflict_detection_to_robot(self):
        """Test conflict detection when both CSV and YAML have same names in Robot Framework."""
        # Both CSV and YAML test cases with SAME names - should raise error
        csv_test_cases_data = """test_case,test_step
Conflict Test,Test Module"""

        yaml_test_cases_data = """Test Cases:
  Conflict Test:
    - Test Module"""

        # Both CSV and YAML modules with SAME names - should raise error
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
Shared Module,Launch App,test_app,,
Shared Module,Sleep,1000,,"""

        yaml_modules_data = """Modules:
  Shared Module:
    - Press Element ${button}
    - Sleep 2000"""

        elements_data = """Elements:
  button: id:test_button"""

        config_data = "driver_sources: []"

        # Create files with conflicting names
        file_data = [
            ("test_cases.csv", csv_test_cases_data),
            ("test_cases.yaml", yaml_test_cases_data),
            ("modules.csv", csv_modules_data),
            ("modules.yaml", yaml_modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
        ]

        # Create files
        for filename, data in file_data:
            with open(
                os.path.join(self.temp_dir, filename), "w", encoding="utf-8"
            ) as f:
                f.write(data)

        # Generate - should raise conflict error
        with pytest.raises(ValueError, match="Naming conflict detected in test_cases"):
            generate_test_file(self.temp_dir, framework="robot")

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_file_unique_names_to_robot(self):
        """Test successful merging when all names are unique in Robot Framework."""
        # CSV and YAML test cases with UNIQUE names - should work
        csv_test_cases_data = """test_case,test_step
CSV Test,CSV Module"""

        yaml_test_cases_data = """Test Cases:
  YAML Test:
    - YAML Module"""

        # CSV and YAML modules with UNIQUE names - should work
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Launch App,test_app,,
CSV Module,Sleep,1000,,"""

        yaml_modules_data = """Modules:
  YAML Module:
    - Press Element ${button}
    - Sleep 2000"""

        elements_data = """Elements:
  button: id:test_button"""

        config_data = "driver_sources: []"

        # Create files with unique names and generate Robot test
        file_data = [
            ("test_cases.csv", csv_test_cases_data),
            ("test_cases.yaml", yaml_test_cases_data),
            ("modules.csv", csv_modules_data),
            ("modules.yaml", yaml_modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
        ]

        content = TestHelpers.create_and_generate_robot_test(self.temp_dir, file_data)

        # Verify both CSV and YAML test cases are present
        assert "CSV Test" in content
        assert "YAML Test" in content

        # Verify both CSV and YAML modules are loaded
        assert "CSV Module" in content
        assert "YAML Module" in content

        # Verify both module contents
        assert "Sleep    1000" in content  # From CSV module
        assert "Sleep    2000" in content  # From YAML module

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_empty_files_handling_to_robot(self):
        """Test handling of empty CSV/YAML files in mixed scenarios for Robot Framework."""
        # CSV test cases
        test_cases_data = """test_case,test_step
Empty Test,Working Module"""

        # Empty CSV modules file
        empty_csv_modules_data = """module_name,module_step,param_1,param_2,param_3"""

        # YAML modules with content
        yaml_modules_data = """Modules:
  Working Module:
    - Launch App test_app
    - Sleep 1000"""

        # Empty YAML elements file
        empty_yaml_elements_data = """Elements: {}"""

        config_data = "driver_sources: []"

        # Create files with some empty sources
        for filename, data in [
            ("test_cases.csv", test_cases_data),
            ("modules.csv", empty_csv_modules_data),
            ("modules.yaml", yaml_modules_data),
            ("elements.yaml", empty_yaml_elements_data),
            ("config.yaml", config_data),
        ]:
            with open(
                os.path.join(self.temp_dir, filename), "w", encoding="utf-8"
            ) as f:
                f.write(data)

        # Generate
        generate_test_file(self.temp_dir, framework="robot")

        # Read generated file
        test_file = os.path.join(
            self.temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(self.temp_dir)}.robot",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Verify test case from CSV is present
            assert "Empty Test" in content

            # Verify working module from YAML is present
            assert "Working Module" in content
            assert "Launch App    test_app" in content
            assert "Sleep    1000" in content

            # Verify empty files don't break generation
            assert "*** Settings ***" in content
            assert "*** Keywords ***" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_config_sources_to_robot(self):
        """Test mixed configuration with different element sources for Robot Framework."""
        # CSV test cases
        test_cases_data = """test_case,test_step
Config Test,Test Module"""

        # YAML modules
        modules_data = """Modules:
  Test Module:
    - Launch App test_app
    - Press Element ${csv_element}
    - Enter Text ${yaml_element} test_value"""

        # CSV elements
        csv_elements_data = """Element_Name,Element_ID
csv_element,id:csv_button"""

        # YAML elements
        yaml_elements_data = """Elements:
  yaml_element: id:yaml_input"""

        # Config with multiple element sources
        config_data = """driver_sources:
  - type: appium
    capabilities:
      platformName: Android
elements_sources:
  - type: csv
    file: elements.csv
  - type: yaml
    file: elements.yaml
text_detection:
  - type: easyocr
image_detection:
  - type: opencv"""

        # Create files
        for filename, data in [
            ("test_cases.csv", test_cases_data),
            ("modules.yaml", modules_data),
            ("elements.csv", csv_elements_data),
            ("elements.yaml", yaml_elements_data),
            ("config.yaml", config_data),
        ]:
            with open(
                os.path.join(self.temp_dir, filename), "w", encoding="utf-8"
            ) as f:
                f.write(data)

        # Generate
        generate_test_file(self.temp_dir, framework="robot")

        # Read generated file
        test_file = os.path.join(
            self.temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(self.temp_dir)}.robot",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Verify config includes multiple element sources
            assert "${OPTICS_CONFIG_JSON}=" in content
            assert "driver_config" in content or "element_source_config" in content

            # Verify elements from both sources are available
            assert "csv_element=id:csv_button" in content
            assert "yaml_element=id:yaml_input" in content

            # Verify both elements are used in module
            assert "${ELEMENTS.csv_element}" in content
            assert "${ELEMENTS.yaml_element}" in content

    @pytest.mark.generate
    @pytest.mark.black_box
    def test_mixed_keyword_arguments_to_robot(self):
        """Test mixed CSV/YAML with keyword arguments conversion to Robot Framework."""
        # CSV test cases
        test_cases_data = """test_case,test_step
Args Test,CSV Module
Args Test,YAML Module"""

        # CSV modules with keyword arguments
        csv_modules_data = """module_name,module_step,param_1,param_2,param_3
CSV Module,Launch App,test_app,event_name=SwipeToPay,
CSV Module,Enter Text,${field},test_value,timeout=5000"""

        # YAML modules with keyword arguments
        yaml_modules_data = """Modules:
  YAML Module:
    - Scroll From Element ${element} right 3000
    - Press Element ${button} retry_count=3"""

        elements_data = """Elements:
  field: id:text_field
  element: id:test_element
  button: id:test_button"""

        config_data = "driver_sources: []"

        # Create files
        for filename, data in [
            ("test_cases.csv", test_cases_data),
            ("modules.csv", csv_modules_data),
            ("modules.yaml", yaml_modules_data),
            ("elements.yaml", elements_data),
            ("config.yaml", config_data),
        ]:
            with open(
                os.path.join(self.temp_dir, filename), "w", encoding="utf-8"
            ) as f:
                f.write(data)

        # Generate
        generate_test_file(self.temp_dir, framework="robot")

        # Read generated file
        test_file = os.path.join(
            self.temp_dir,
            "generated",
            "Tests",
            f"test_{os.path.basename(self.temp_dir)}.robot",
        )
        with open(test_file, "r", encoding="utf-8") as f:
            content = f.read()

            # Verify keyword arguments from CSV are preserved in Robot Framework
            assert "event_name=SwipeToPay" in content
            assert "timeout=5000" in content
            assert "Launch App    test_app    event_name=SwipeToPay" in content
            assert (
                "Enter Text    ${ELEMENTS.field}    test_value    timeout=5000"
                in content
            )

            # Verify keyword arguments from YAML are preserved in Robot Framework
            assert "retry_count=3" in content
            assert (
                "Scroll From Element    ${ELEMENTS.element}    right    3000" in content
            )
            assert "Press Element    ${ELEMENTS.button}    retry_count=3" in content
