# Developer Guide

This section provides guidelines for developers who want to contribute to the Optics Framework.

**Note:** Before contributing, please read our [Contributing Guidelines](../contribution/contributing_guidelines.md) to understand the contribution process.

## 1. Setting Up the Development Environment

To set up the development environment for the Optics Framework, follow these steps:

### Clone the Repository

Clone the Optics Framework repository to your local machine using the following command:

```bash
git clone <repository_url>
cd optics-framework
```

### Install Dependencies

Install the required dependencies using the following command:

```bash
pipx install poetry
```

For changes related to source code:

```bash
poetry install --with dev
```

For changes related to documentation:

```bash
poetry install --with docs
```

For default installation:

```bash
poetry install
```

**NOTE:** We recommend using `poetry` to manage dependencies and virtual environments for the project.
**NOTE:** For more info about `pipx` and `poetry`, refer to the [pipx documentation](https://pipxproject.github.io/pipx/) and [poetry documentation](https://python-poetry.org/docs/).

### Install Pre-Commit Hooks

After installing dependencies, set up the pre-commit hooks to ensure code quality and commit message formatting:

```bash
poetry run pre-commit install
```

This will install hooks that:
- Run `ruff` for code linting and formatting
- Run `bandit` for security checks
- Validate commit messages using `commitizen` (Conventional Commits format)
- Check for trailing whitespace, end-of-file issues, and YAML/JSON validity
- Scan for secrets using `gitleaks`

**NOTE:** Pre-commit hooks will automatically run on `git commit`. You can also run them manually:
```bash
poetry run pre-commit run --all-files
```

## 2. Create a New Branch

Before making any changes to the codebase, create a new branch for your contribution using the following command:

```bash
git checkout -b <branch_name>
```

## 3. Make Changes

Work on your feature, bug fix, or documentation improvement in the appropriate directory:

- **Source code:** `optics_framework/`
- **Tests:** `tests/`
- **Documentation:** `docs/`

### Source Code Changes

- Make changes to the source code in the `optics_framework/` directory.

Adhere to the project’s coding standards:
Use Ruff for linting and formatting:

```bash
poetry run ruff check .
poetry run ruff format .
```

Or run both together:

```bash
poetry run ruff check --fix .
poetry run ruff format .
```

**NOTE:** Ruff is configured to automatically fix issues where possible. The pre-commit hooks will also run Ruff automatically on commit.

### Documentation Changes

- Make changes to the documentation in the `docs/` directory.
- Ensure the documentation is clear, concise, and follows the style guide.
- Use MkDocs for generating documentation.

For live changes while working on documentation:
Use MkDocs to serve the documentation locally with auto-reload:

```bash
poetry run mkdocs serve
```

This will start a local server (typically at `http://127.0.0.1:8000`) that automatically reloads when you make changes to the documentation files.

To build the documentation for production:

```bash
poetry run mkdocs build
```

### Run Tests

Run the tests to ensure that your changes do not break existing functionality:

```bash
poetry run pytest
```

### Packaging

To build the package:

```bash
poetry build
```

## 4. Commit Changes

- Adhere to the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format for your commit messages.
- The pre-commit hooks will automatically validate your commit messages using `commitizen`.
- You can also use `commitizen` to help create properly formatted commit messages:

```bash
poetry run cz commit
```

This will interactively guide you through creating a commit message that follows the Conventional Commits format.

**NOTE:** If you installed the pre-commit hooks (step 1.3), they will automatically run on each commit to check code quality and validate commit messages.

## 5. Local Static Analysis (SonarQube, Optional)

CI runs SonarQube static analysis on every push to `main` (`.github/workflows/mozarksonar.yml`). To catch the same findings before you push, run the scanner locally against a throwaway SonarQube container instead of the shared server.

Start a local SonarQube server once:

```bash
docker run -d --name sonarqube -p 9000:9000 sonarqube:community
```

Open `http://localhost:9000` (default login `admin`/`admin`), wait for it to finish starting, and generate a token under **My Account → Security**.

Then run the scanner from the repo root, pointing it at that server:

```bash
docker run --rm \
    -v "$(pwd):/usr/src" \
    --network=host \
    -e SONAR_HOST_URL="http://localhost:9000" \
    -e SONAR_SCANNER_OPTS="-Dsonar.projectKey=optics-framework" \
    -e SONAR_TOKEN="<your-token>" \
    sonarsource/sonar-scanner-cli
```

Findings are printed by the scanner and shown on the dashboard at `http://localhost:9000/dashboard?id=optics-framework`. The scanner writes a `.scannerwork/` directory into the repo; it's gitignored.
