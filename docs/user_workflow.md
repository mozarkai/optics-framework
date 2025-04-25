# User Workflow

This guide walks you through the workflow for using the Optics Framework to create and execute automated tests. The framework relies on a CSV-based structure and a YAML configuration file to define test cases, modules, and elements.

## Initial Setup

To begin, the Optics Framework requires at least one YAML configuration file (`config.yaml`) and CSV files to define your tests. You can bootstrap a project using the `optics init` command:

```bash
optics init --name youtube --path ./youtube
```

- **--name**: Specifies the project name (e.g., `youtube`).
- **--path**: Defines where the project folder will be created.
- **--template**: (Optional) Uses a predefined sample template to populate initial files.

This command generates a project directory with a default structure, including a `config.yaml` and placeholder CSV files.

## CSV Use Cases

The framework uses three primary CSV files to organize test logic: `test_cases.csv`, `test_modules.csv`, and `elements.csv`. Below are their purposes and examples.

### test_cases.csv

This file defines test cases and links them to specific test steps (modules). It has two columns: `test_case` and `test_step`.

**Example:**

```csv
test_case,test_step
Running youtube using text,Launching App using text
Running youtube unknown,Repeat Test
```

- **test_case**: The name of the test case.
- **test_step**: The module or step to execute for that test case.

```csv
test_case,test_step
Suite Setup, Launch youtube app
Suite Teardown, Terminate youtube app
Setup, Load home page
Teardown, Return to Home Page
Running youtube using text,Launching App using text
Running youtube unknown,Repeat Test
```

**Suite Setup/Teardown and Test Case Setup/Teardown**

You can define setup and teardown steps in the test_cases.csv to ensure proper initialization and cleanup for both the entire test suite and individual test cases.

### test_modules.csv

This file lists all modules, their steps, and associated parameters. Columns include `module_name`, `module_step`, and optional `param_1` to `param_n` (as many parameters as needed).

**Example:**

```csv
module_name,module_step,param_1,param_2,param_3,param_4,param_5
Launching App using text,Launch App,,,,
Launching App using text,Press Element,//android.widget.Button[@resource-id="com.android.permissioncontroller:id/permission_allow_button"],,,
Interact using text,Assert Presence,${Subscriptions_text},,,
Interact using text,Press Element,${Subscriptions_text},,,
Interact using text,Press Element,${Home_text},,,
Interact using xpath,Assert Presence,${Subscriptions_xpath},,,
Interact using xpath,Press Element,${Subscriptions_xpath},,,
Interact using xpath,Press Element,${Home_xpath},,,
Interact using images,Press Element,${Subscriptions_image},,,
Interact using images,Press Element,${Home_image},,,
Dynamic Launch,condition,${METHOD} == 'text',Interact using text,${METHOD} == 'xpath',Interact using xpath,Interact using images
Repeat Test,Run Loop,Dynamic Launch,${METHOD},${List}
```

- **module_name**: The name of the module.
- **module_step**: A keyword or action (e.g., `Launch App`, `Press Element`). See [Keywords Reference](#) for a full list.
- **param_1, param_2, ...**: Parameters for the action, such as element IDs or conditions.

### elements.csv

This file acts as a variable store, mapping element names to their identifiers (e.g., XPath, text, or image files).

**Example:**

```csv
Element_Name,Element_ID
Subscriptions_xpath,//android.widget.TextView[@resource-id="com.google.android.youtube:id/text" and @text="Subscriptions"]
you_xpath,(//android.widget.ImageView[@resource-id="com.google.android.youtube:id/image"])[4]
Home_xpath,//android.widget.TextView[@resource-id="com.google.android.youtube:id/text" and @text="Home"]
Subscriptions_text,Subscriptions
you_text,Library
Home_text,Home
Subscriptions_image,sub.jpeg
Youtube_image,youtube.jpeg
Home_image,home.png
METHOD,None
List,"[""xpath"",""text"",""images""]"
```

- **Element_Name**: The variable name used in `test_modules.csv`.
- **Element_ID**: The actual identifier (e.g., XPath, text value, or image filename).

## Configuration File (config.yaml)

The `config.yaml` file specifies the driver and detection methods for your project. Below is an example tailored for an Android YouTube app test:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        appActivity: "com.google.android.youtube.app.honeycomb.Shell$HomeActivity"
        appPackage: "com.google.android.youtube"
        automationName: "UiAutomator2"
        deviceName: "emulator-5554"
        platformName: "Android"

elements_sources:
  - appium_find_element:
      enabled: true
      url: null
      capabilities: {}
  - appium_screenshot:
      enabled: true
      url: null
      capabilities: {}


text_detection:
  - easyocr:
      enabled: true
      url: null
      capabilities: {}

image_detection:
  - templatematch:
      enabled: true
      url: null
      capabilities: {}

log_level: INFO

include:
- "Test cases to be included"

exclude:
- "Test Cases to be excluded"

```

- **driver_sources**: Defines the automation driver (e.g., Appium for Android).
- **elements_sources**: Specifies how elements are located (e.g., Appium or screenshots).
- **text_detection**: Configures text recognition tools (e.g., EasyOCR).
- **image_detection**: Sets up image matching (e.g., Template Matching).
- **include**: Test cases to be executed, the rest of the test cases will be skipped.
- **exclude**: Specified test cases to be skipped, all other test cases will be executed.

## Project Structure

Your project folder should look like this:

```
/youtube
├── config.yaml
├── elements.csv
├── execution_output
│   └── logs.log
├── input_templates
│   ├── home.png
│   ├── sub.jpeg
│   └── youtube.jpeg
├── test_cases.csv
└── test_modules.csv
```

- **input_templates/**: Store any input images (e.g., `home.png`) referenced in `elements.csv`.
- **execution_output/**: Contains logs and other output generated during test runs.

## Using Keywords in Modules

Modules in `test_modules.csv` use a predefined set of keywords (e.g., `Launch App`, `Press Element`, `Assert Presence`). For a complete list, refer to the [Keywords Reference](usage/keyword_usage.md).

## Samples For Your Reference
Sample test scripts are provided to demonstrate the framework’s capabilities and guide users in writing their own. These examples show how texts, XPaths, and images can be used interchangeably, and how test data can be structured using CSV files.

📅 Calendar

Calendar sample showcases how our framework supports dynamic data fetching using APIs (e.g. fetching current date) and apply natural date evaluations to compute future dates on the fly.

📺 Youtube

Youtube sample showcases our framework's ability to interact with the devices using multiple element types such as texts, xpaths and images.

👥 Contact

Contact example showcases form input automation, screen validation using dynamic element assertions, and how structured test data (like ${First_Name}) can be reused across steps.

📧 Gmail Website

A Selenium-based sample that automates Gmail sign-in and account creation. It includes clicking on buttons like “Sign in” and “Create an account,” and entering user credentials, showcasing web interaction using text-based locators.

## Validating Your Setup

To check for syntactical errors in your CSV files and configuration, run a dry run:

```bash
optics dry_run ./youtube
```

For a specific test case:

```bash
optics dry_run ./youtube --test-case "Running youtube using text"
```

This command simulates the test execution without interacting with the device, helping you catch issues early.

## Executing Tests

Once validated, execute your tests with:

```bash
optics execute ./youtube
```

- **./youtube**: Path to your project directory.
- **--test-cases**: Path to the `test_cases.csv` file.

Output, including logs, will be saved in the `execution_output/` folder.
