# Changelog

Notable changes to Optics Framework. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each release on GitHub also lists every merged pull request.

## [Unreleased]

### Added

- `optics execute` and `optics dry_run` accept `--include NAME ...` or `--exclude NAME ...` to change which test cases run, for one run, without editing `config.yaml`. Either flag replaces both `include` and `exclude` from `config.yaml` for that run. Names match whole test case names, ignoring case and extra spaces. Several names can follow one flag, or the flag can be repeated.
- A name passed to `--include` or `--exclude` that matches no test case, or a selection that leaves nothing to run, stops before any session starts with exit code `3` and a message starting with `Error:`. The message suggests the closest test case name and lists the project's test cases. A blank name or both flags at once exit with `2`.
- `--list` on `optics execute` and `optics dry_run` prints the test cases the command would run, one per line, and exits without starting a session, running the preflight or needing an enabled driver.

### Changed

- An `include` or `exclude` name in `config.yaml` that matches no test case now prints a warning instead of being ignored silently. The run continues.
- Test case names in `include` and `exclude` now also ignore runs of whitespace (and use Unicode case folding), so a name with doubled inner spaces can be selected by its visible name.
