# **How to Start a New No-Code Test Script**
**This guide will walk you through creating automated tests using optics framework.**

## 📦 Installation & Setup

### Install Optics Framework

```bash
pip install optics-framework
```

## 🚀 Quick Start

### Step 1: Create Python Virtual Environment
Note: Ensure Appium server is running and a virtual Android device is enabled before proceeding.
```bash
mkdir ~/test-code
cd ~/test-code
python3 -m venv venv
source venv/bin/activate
pip install optics-framework
```

> **⚠️ Important:** Conda environments are not supported for `easyocr` and `optics-framework` together, due to conflicting requirements for `numpy` (version 1.x vs 2.x). Please use a standard Python virtual environment instead.

### Step 2: Create a New Test Project
```bash
optics setup --install Appium EasyOCR
optics init --name my_test_project --path . --template contact
```
This creates a project structure with sample templates to help you get started.

> **⚠️ Note: Intel based Macs cannot download easyocr.
## Your test project uses four main components that work together:
```
optics_framework/
└── samples/
    └── my_test_project/
        ├── config.yaml
        ├── modules/
        |   └── modules.csv
        ├── test_data/
        |   ├── elements.csv
        |   └── input_templates/
        └── test_cases/
            └── test_cases.csv
```

#### *my_test_project/ - Your specific project name (you choose this)*

## 📌 Step 1: Configure Your Environment (config.yaml)
The config.yaml file tells the framework how to connect to your device and what tools to use for finding elements.
### What to configure:
Driver Connection - Connects to your device/emulator:
```bash
driver_sources:
    - appium:
        enabled: true
        url: "http://127.0.0.1:4723/wd/hub"
        capabilities:
            {
            automationName: UiAutomator2
            deviceName: emulator-5554
            platformName: Android
            }
```

### Key settings to update:

- platformVersion: Your Android/iOS version
- deviceName: Your device name
- udid: Your device's unique identifier (find with adb devices)
- url: Your Appium server address (usually localhost)

## 📌 Step 2: Capture UI Element Screenshots (input_templates/)
Before defining elements in the CSV, you need to capture screenshots of the UI elements you want to interact with.

### What is input_templates/?
This folder stores PNG images of buttons, icons, text fields, and other UI elements from your application. The framework uses these images to visually locate elements on the screen when other methods fail.

## 📌 Step 3: Define Your Elements (elements.csv)
Elements are the UI components you'll interact with – buttons, text fields, tabs, etc.

### CSV Structure:
Element_Name,Element_ID_xpath,Element_ID,Element_ID_Text

### Column Explanations:

- Element_Name: A descriptive name you'll use in modules (e.g., login_button)
- Element_ID_xpath: The technical XPath or accessibility ID for finding the element
- Element_ID: The PNG filename from input_templates/ for visual matching
- Element_Text: The visible text on the element (optional, for verification)

### Example elements.csv:
- Element_Name,Element_ID_xpath,Element_ID,Element_Text
- login_button,"//android.widget.Button[@resource-id=""com.app.login:id/btnLogin""]",button_login.png,Login

### Special element types:
- Element_Name,Element_ID_xpath,Element_ID,Element_Text
- test_password,,,SecurePass123
- retry_count,,,3
- item_list,"[""Item1"",""Item2"",""Item3""]",,

### Finding XPaths:
Use Appium Inspector or your device's UI Automator to find element XPaths:

- Connect Appium Inspector to your device
- Navigate to the screen with your element
- Click the element in the inspector
- Copy the XPath
- Paste into Element_ID_xpath column

## 📌 Step 4: Create Reusable Modules (modules.csv)
Modules are sequences of actions that accomplish a specific task, such as building blocks.

### CSV Structure:
module_name,module_step,param_1,param_2,param_3,param_4,param_5

### Column Explanations:

- module_name: Name of your module (can repeat for multi-step modules)
- module_step: The action to perform (see common actions below)
- param_1 to param_x: Parameters for the action (vary by action type)

*💡 Note: ❌ If your module has fewer param_x columns than the action requires, those extra parameters will be ignored — leading to incomplete or failed test execution. Always check the expected number of parameters for each keyword and ensure your CSV matches it exactly.*

### Common Actions:
- Launch Application,Launch App,,,,
- Navigate To Settings,Press Element,${settings_icon},,,
- Add Multiple Items,Run Loop,Add Single Item,item_name,${item_list},,
- Close Application,Force Terminate App,,,,
- Launch External App,Launch Other App,com.example.otherapp,,,,

#### *Note: ${element_name} references an element from elements.csv*

## 📌 Step 5: Build Test Cases (test_cases.csv)
Test cases combine modules into complete test scenarios.

### CSV Structure:
test_case,test_step

### Column Explanations:

- test_case: Name of your test scenario
- test_step: Module to execute (references module_name from modules.csv)

### Special Test Cases:
- Suite Setup - Runs before all tests:
test_case,test_step
Suite Setup,Launch Application
- Suite Teardown - Runs after all tests:
Suite Teardown,Close Application
- Regular Test:
Verify User Login,User Login

### Why separate test cases from modules?
Test cases define what to test, modules define how to do it. This separation lets you mix and match modules for different test scenarios.

## 🛠️ Running Your Tests

### Prerequisites:
- Python 3.12 installed on your system
- Optics Framework installed in your virtual environment
- Appium server running: appium
- Android virtual device or physical device connected and verified: adb devices

### Always dry run test cases before executing:

```bash
optics dry_run my_test_project
```
#### Why are dry runs needed?
- Detects missing files (like elements.csv or input templates) early
- Verifies CSV and yaml syntax and formatting
- Checks that all referenced elements and modules exist
- Saves time by catching setup errors before full execution

### Execute Test Cases:

```bash
optics execute my_test_project
```

### What happens during execution:

- Framework reads your config.yaml and connects to device
- Loads all elements from elements.csv
- Loads all modules from modules.csv
- Loads element images from input_templates/
- Executes test cases in order from test_cases.csv
- Generates a test report with results and logs

## Best Practices ✅
1. **Naming Conventions**

- Use descriptive names: login_button_xpath, login_button_text etc
- Be consistent: if you use firsttest_case, use it everywhere
- Include element type in name: _button, _field, _icon, _tab
- Match Element_Name with PNG filename for clarity

2. **Screenshot Management**

- Organize input_templates/ with subfolders for different screens
- Name images to match their Element_ID exactly
- Update screenshots when UI changes

3. **Module Design**

- Keep modules focused on one task
- Make modules reusable – avoid hardcoded values
- Use variables (${variable_name}) for data that changes
- Name modules clearly to describe their purpose

4. **Element Definition**

- Prefer (text,image) over xpaths
- Test each element can be found reliably
- Update elements.csv when app UI changes

5. **Test Organization**

- Always include Suite Setup and Suite Teardown
- Group related tests together
- Start with a clean state (use Setup to reset app)
- Test one feature per test case
- Name tests clearly: "Test_FeatureName_Scenario"

6. **File Organization**
```
optics_framework/
    └── samples/
        └── my_test_project/
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
7. **Debugging**

- Set log_level: DEBUG in config.yaml for detailed logs
- Enable file_log: true to save logs to file
- Test modules individually before combining them
- Use meaningful module names that explain what failed
- Check input_templates/ images load correctly
- Verify element names match exactly (case-sensitive)

## Quick Start Checklist

1. Configure config.yaml with your device details
2. Capture screenshots of UI elements
3. Save screenshots to test_data/input_templates/
4. Define elements in elements.csv (with XPaths and image filenames)
5. Create modules in modules.csv for each action sequence
6. Build test cases in test_cases.csv combining modules
7. Start Appium server
8. Connect device/emulator
9. Dry run tests
10. Execute tests
11. Review test results and logs

## Common Pitfalls to Avoid
- ❌ Hardcoding values - Use variables instead
- ❌ Duplicate logic - Create reusable modules
- ❌ Poor element identifiers - Elements should be unique and stable
- ❌ Missing screenshots - Element_ID references non-existent PNG files
- ❌ Wrong file paths - Ensure input_templates/ path is correct
- ❌ No error handling - Define fallback element finding methods
- ❌ Skipping Setup/Teardown - Always clean up after tests
- ❌ Testing too much at once - Keep test cases focused
- ❌ Outdated screenshots - Update images when UI changes
- ❌ Generic element names - button1.png is less clear than button_login.png

## Need Help?
### Common Issues:

- Element not found: Check XPath in Appium Inspector, verify PNG exists in input_templates/
- Wrong element clicked: Screenshot may be outdated or too similar to other elements
- Test hangs: Check if app is waiting for user input or loading
- Image match fails: Recapture screenshot on same device/resolution

### Troubleshooting Steps:

- Check logs in the test output directory
- Verify elements exist with Appium Inspector
- Test individual modules before full test cases
- Verify the dry_run works before executing to save time
- Ensure all file paths and element names match exactly (case-sensitive)
- Verify PNG files are in correct location and named correctly
- Check image quality and cropping of templates
