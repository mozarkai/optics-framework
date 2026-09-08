# Optics project format

An optics test project is a folder of CSV files plus a `config.yaml` that
`optics execute <folder>` runs. Author one directly from the steps you already
performed this session — the MCP has no save/record tool because you don't need
one: you know the keywords you called and their args.

## Layout
    <project>/
      config.yaml                 # driver + element sources
      test_cases/test_cases.csv   # which modules each test case runs, in order
      modules/modules.csv         # the keyword steps of each module
      test_data/elements.csv      # named locators / variables

## test_cases/test_cases.csv  — columns: test_case,test_step
One row per (test case, module) in run order; `test_step` holds a MODULE name
(not a keyword).
    test_case,test_step
    Set Alarm,Open Clock
    Set Alarm,Create Alarm

## modules/modules.csv  — columns: module_name,module_step,param_1,param_2,...
One row per step. `module_step` is a keyword's display name (Title Case of the
tool/keyword name: `press_element` -> "Press Element"; full catalog at the
`optics://keywords` resource). Params are positional; an optional keyword arg is
written `name=value` (e.g. `index=2`). A locator such as `text=Save` stays one
positional value.
    module_name,module_step,param_1,param_2
    Open Clock,Launch App
    Create Alarm,Press Element,${add_alarm}
    Create Alarm,Enter Text,${hour_field},${time}

## test_data/elements.csv  — columns: Element_Name,Element_ID
Maps a name to a locator OR a value; steps reference it as `${Element_Name}`.
This is also how you parameterize a suite: put the run-specific value here and
reference it, so "set alarm to 22:00" becomes a reusable `${time}`. A whole cell
must be exactly `${Name}` — the runner substitutes whole values only, it does not
expand `${name}` inside a larger string.
    Element_Name,Element_ID
    add_alarm,//*[@content-desc="Add alarm"]
    hour_field,text=Hour
    time,22:00

## config.yaml  — driver + element sources
`elements_sources` for a driver are `{driver}_find_element`,
`{driver}_page_source`, `{driver}_screenshot`. appium is only an example here —
selenium and playwright follow the same shape.
    driver_sources:
      - appium:            # or selenium / playwright / ...
          enabled: true
          url: "<driver server url>"
          capabilities: {}   # e.g. platformName, deviceName/udid for appium
    elements_sources:
      - appium_find_element: { enabled: true }
      - appium_page_source: { enabled: true }
      - appium_screenshot: { enabled: true }

## Control flow & data (author these into modules; the runner executes them)
A module step is any keyword from the `optics://keywords` catalog — including the
control-flow/data keywords that only run under the CSV runner, not as a live
tool: `Run Loop`, `Condition`, `Execute Module`, `Evaluate`, `Date Evaluate`,
`Read Data`, `Invoke Api`. Use them when a suite needs loops, branching, computed
values, or API calls (e.g. a picker's read-current -> compute -> repeat pattern
becomes an `Evaluate` + `Run Loop`). Live, you drive that logic yourself by
calling the primitive keyword tools in the loop/branch you decide; you only need
these keywords when authoring a suite to run headless via `optics execute`.

## Escaping
CSV values containing commas are quoted by the writer; a literal newline, tab or
backslash in a value is written escaped (`\n`, `\t`, `\\`) and the runner
un-escapes it on read.

## Running
`optics execute <project>` runs every test case; JUnit XML, logs and screenshots
land in `<project>/execution_output/`.
