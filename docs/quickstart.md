# :material-speedometer: Quick Start Guide

This guide will walk you through creating automated tests using Optics Framework.

!!! warning "Prerequisites"
    This guide assumes you have already installed Optics and the platform tooling
    for your target (for Android: the Appium server, an emulator/device, adb).
    If not, do the [Installation & Prerequisites](prerequisites.md) guide first.

## :material-rocket: Quick Start

### Step 1: Create Python Virtual Environment

```bash
mkdir ~/test-code
cd ~/test-code
python3 -m venv venv
source venv/bin/activate
pip install optics-framework
```

!!! danger "Important"
    Conda environments are not supported for `easyocr` and `optics-framework` together, due to conflicting requirements for `numpy` (version 1.x vs 2.x). Please use a standard Python virtual environment instead.

### Step 2: Install the Engines You Need

The core install has no drivers. Add the ones your test needs — either as pip
extras or via `optics setup` (the names match the `config.yaml` source keys):

```bash
optics setup --install appium easyocr
# equivalent to: pip install "optics-framework[appium,easyocr]"
```

!!! warning "Note"
    Intel-based Macs cannot download easyocr.

### Step 3: Create a New Test Project

```bash
optics init --name my_test_project --template contact
```

This copies the `contact` sample — a complete, runnable project — into
`./my_test_project`. Omit `--template` to scaffold an empty project you fill in
yourself. Templates: `contact`, `clock`, `calendar`, `youtube`, `gmail_web`,
`playwright`.

## :material-file-tree: Project Structure

Your test project uses four main components that work together:

```text
my_test_project/          # created in your current directory
├── config.yaml
├── modules/
|   └── modules.csv
├── test_data/
|   ├── elements.csv
|   └── input_templates/
└── test_cases/
    └── test_cases.csv
```

*`my_test_project/` - Your specific project name (you choose this)*

### Using Invoke API and API definition files

Projects that use the **Invoke Api** keyword include **API definition YAML files**, which are a separate asset from element files. The **Invoke Api** keyword does not use `elements.csv` or element YAML; it uses API definition data only. Add an `api.yaml` (or other YAML files under the project) that contains a top-level `api` or `apis` key. The runner auto-discovers and loads any such YAML files under the project directory. Example layout when using API tests:

```text
my_test_project/
├── config.yaml
├── api.yaml
├── modules/
├── test_data/
|   ├── elements.csv
|   └── input_templates/
└── test_cases/
```

The API YAML format is different from element files: it defines collections, base URLs, endpoints, request/response, and optional `extract` rules. See the **Invoke Api** and **Add Api** sections in [Keyword Usage](usage/keyword_usage.md), and sample `api.yaml` files

## :material-cog: Step 1: Configure Your Environment

The `config.yaml` file tells the framework how to connect to your device and what tools to use for finding elements.

### Driver Connection

Connects to your device/emulator:

```yaml
driver_sources:
    - appium:
        enabled: true
        url: "http://127.0.0.1:4723/wd/hub"
        capabilities:
            automationName: UiAutomator2
            deviceName: emulator-5554
            platformName: Android
```

### Key Settings to Update

- `platformVersion`: Your Android/iOS version
- `deviceName`: Your device name
- `udid`: Your device's unique identifier (find with `adb devices`)
- `url`: Your Appium server address (usually localhost)

## :material-camera: Step 2: Capture UI Element Screenshots

Before defining elements in the CSV, you need to capture screenshots of the UI elements you want to interact with.

### What is `input_templates/`?

This folder stores PNG images of buttons, icons, text fields, and other UI elements from your application. The framework uses these images to visually locate elements on the screen when other methods fail.

!!! tip "Best Practice"
    Organize `input_templates/` with subfolders for different screens and name images to match their Element_ID exactly.

## :material-format-list-bulleted: Step 3: Define Your Elements

Elements are the UI components you'll interact with – buttons, text fields, tabs, etc.

### CSV Structure

```csv
Element_Name,Element_ID_xpath,Element_ID,Element_ID_Text
```

### Column Explanations

- **Element_Name**: A descriptive name you'll use in modules (e.g., `login_button`)
- **Element_ID_xpath**: The technical XPath or accessibility ID for finding the element
- **Element_ID**: The PNG filename from `input_templates/` for visual matching
- **Element_Text**: The visible text on the element (optional, for verification)

### Example `elements.csv`

```csv
Element_Name,Element_ID_xpath,Element_ID,Element_Text
login_button,"//android.widget.Button[@resource-id=""com.app.login:id/btnLogin""]",button_login.png,Login
```

### Special Element Types

```csv
Element_Name,Element_ID_xpath,Element_ID,Element_Text
test_password,,,SecurePass123
retry_count,,,3
item_list,"[""Item1"",""Item2"",""Item3""]",,
```

### Finding XPaths

Use Appium Inspector or your device's UI Automator to find element XPaths:

1. Connect Appium Inspector to your device
2. Navigate to the screen with your element
3. Click the element in the inspector
4. Copy the XPath
5. Paste into `Element_ID_xpath` column

!!! tip "Newlines and special characters in CSV"
    XPaths and other locator strings in CSV stay one line per row. To include a newline in a value (e.g. in `@content-desc`), use `\n`; for a tab use `\t`, and for a literal backslash use `\\`. Example: `//android.widget.ImageView[@content-desc="I\nIcici Bank Limited"]` in a cell is read as an XPath whose attribute value contains a real newline between `I` and `Icici Bank Limited`.

## :material-puzzle: Step 4: Create Reusable Modules

Modules are sequences of actions that accomplish a specific task, such as building blocks.

### CSV Structure

```csv
module_name,module_step,param_1,param_2,param_3,param_4,param_5
```

### Column Explanations

- **module_name**: Name of your module (can repeat for multi-step modules)
- **module_step**: The action to perform (see common actions below)
- **param_1 to param_x**: Parameters for the action (vary by action type)

!!! warning "Parameter Count"
    If your module has fewer `param_x` columns than the action requires, those extra parameters will be ignored — leading to incomplete or failed test execution. Always check the expected number of parameters for each keyword and ensure your CSV matches it exactly.

### Common Actions

```csv
module_name,module_step,param_1,param_2,param_3,param_4,param_5
Launch Application,Launch App,,,,
Navigate To Settings,Press Element,${settings_icon},,,
Add Multiple Items,Run Loop,Add Single Item,item_name,${item_list},,
Close Application,Force Terminate App,,,,
Launch External App,Launch Other App,com.example.otherapp,,,,
```

!!! note "Variable References"
    `${element_name}` references an element from `elements.csv`

## :material-check-circle: Step 5: Build Test Cases

Test cases combine modules into complete test scenarios.

### CSV Structure

```csv
test_case,test_step
```

### Column Explanations

- **test_case**: Name of your test scenario
- **test_step**: Module to execute (references `module_name` from `modules.csv`)

### Special Test Cases

**Suite Setup** - Runs before all tests:

```csv
test_case,test_step
Suite Setup,Launch Application
```

**Suite Teardown** - Runs after all tests:

```csv
test_case,test_step
Suite Teardown,Close Application
```

**Regular Test**:

```csv
test_case,test_step
Verify User Login,User Login
```

### Why Separate Test Cases from Modules?

Test cases define **what** to test, modules define **how** to do it. This separation lets you mix and match modules for different test scenarios.

## :material-play: Running Your Tests

### Prerequisites

- Python 3.12 installed on your system
- Optics Framework installed in your virtual environment
- Appium server running: `appium`
- Android virtual device or physical device connected and verified: `adb devices`

See [Installation & Prerequisites](prerequisites.md) for how to install the
Appium server, Android SDK/adb, and a JDK.

### Always Dry Run Test Cases Before Executing

```bash
optics dry_run my_test_project
```

#### Why Are Dry Runs Needed?

- Detects missing files (like `elements.csv` or input templates) early
- Verifies CSV and YAML syntax and formatting
- Checks that all referenced elements and modules exist
- Saves time by catching setup errors before full execution

### Execute Test Cases

```bash
optics execute my_test_project
```

### What Happens During Execution

1. Framework reads your `config.yaml` and connects to device
2. Loads all elements from `elements.csv`
3. Loads all modules from `modules.csv`
4. Loads element images from `input_templates/`
5. Executes test cases in order from `test_cases.csv`
6. Generates a test report with results and logs

## :material-star: Best Practices

### 1. Naming Conventions

- Use descriptive names: `login_button_xpath`, `login_button_text`, etc.
- Be consistent: if you use `firsttest_case`, use it everywhere
- Include element type in name: `_button`, `_field`, `_icon`, `_tab`
- Match `Element_Name` with PNG filename for clarity

### 2. Screenshot Management

- Organize `input_templates/` with subfolders for different screens
- Name images to match their `Element_ID` exactly
- Update screenshots when UI changes

### 3. Module Design

- Keep modules focused on one task
- Make modules reusable – avoid hardcoded values
- Use variables (`${variable_name}`) for data that changes
- Name modules clearly to describe their purpose

### 4. Element Definition

- Prefer (text, image) over xpaths
- Test each element can be found reliably
- Update `elements.csv` when app UI changes

### 5. Test Organization

- Always include Suite Setup and Suite Teardown
- Group related tests together
- Start with a clean state (use Setup to reset app)
- Test one feature per test case
- Name tests clearly: `"Test_FeatureName_Scenario"`

### 6. File Organization

```text
my_test_project/
├── config.yaml
├── modules/
|   └── modules.csv
├── test_data/
|   ├── elements.csv
|   └── input_templates/
|       ├── button_login.png
|       └── field_username.png
└── test_cases/
    └── test_cases.csv
```

### 7. Debugging

- Set `log_level: DEBUG` in `config.yaml` for detailed logs
- Enable `file_log: true` to save logs to file
- Test modules individually before combining them
- Use meaningful module names that explain what failed
- Check `input_templates/` images load correctly
- Verify element names match exactly (case-sensitive)

## :material-format-list-checks: Quick Start Checklist

- [ ] Configure `config.yaml` with your device details
- [ ] Capture screenshots of UI elements
- [ ] Save screenshots to `test_data/input_templates/`
- [ ] Define elements in `elements.csv` (with XPaths and image filenames)
- [ ] Create modules in `modules.csv` for each action sequence
- [ ] Build test cases in `test_cases.csv` combining modules
- [ ] Start Appium server
- [ ] Connect device/emulator
- [ ] Dry run tests
- [ ] Execute tests
- [ ] Review test results and logs

## :material-alert: Common Pitfalls to Avoid

!!! failure "Avoid These Mistakes"
    - ❌ **Hardcoding values** - Use variables instead
    - ❌ **Duplicate logic** - Create reusable modules
    - ❌ **Poor element identifiers** - Elements should be unique and stable
    - ❌ **Missing screenshots** - `Element_ID` references non-existent PNG files
    - ❌ **Wrong file paths** - Ensure `input_templates/` path is correct
    - ❌ **No error handling** - Define fallback element finding methods
    - ❌ **Skipping Setup/Teardown** - Always clean up after tests
    - ❌ **Testing too much at once** - Keep test cases focused
    - ❌ **Outdated screenshots** - Update images when UI changes
    - ❌ **Generic element names** - `button1.png` is less clear than `button_login.png`

## :material-help-circle: Need Help?

### Common Issues

!!! question "Troubleshooting"
    - **Element not found**: Check XPath in Appium Inspector, verify PNG exists in `input_templates/`
    - **Wrong element clicked**: Screenshot may be outdated or too similar to other elements
    - **Test hangs**: Check if app is waiting for user input or loading
    - **Image match fails**: Recapture screenshot on same device/resolution

### Troubleshooting Steps

1. Check logs in the test output directory
2. Verify elements exist with Appium Inspector
3. Test individual modules before full test cases
4. Verify the `dry_run` works before executing to save time
5. Ensure all file paths and element names match exactly (case-sensitive)
6. Verify PNG files are in correct location and named correctly
7. Check image quality and cropping of templates
