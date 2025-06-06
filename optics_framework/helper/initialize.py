import os
import shutil
import subprocess # nosec
import yaml
import pathlib
from optics_framework.common.config_handler import ConfigHandler


def _check_and_prepare_directory(project_path: str, force: bool) -> bool:
    """Check if project directory exists and prepare it based on force flag."""
    if os.path.exists(project_path):
        if force:
            shutil.rmtree(project_path)
            print(
                f"Existing project folder removed due to --force: {project_path}")
        else:
            print(
                f"Project '{project_path}' already exists. Use --force to override.")
            return False
    os.makedirs(project_path)
    print(f"Created project directory: {project_path}")
    return True


def _create_csv_files(project_path: str) -> None:
    """Create CSV files with predefined headers in the project directory."""
    csv_files = {
        "test_cases.csv": "test_case,test_step\n",
        "test_modules.csv": "module_name,module_step,param_1,param_2\n",
        "elements.csv": "Element_Name,Element_ID\n",
    }
    for filename, content in csv_files.items():
        file_path = os.path.join(project_path, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    print("CSV files created.")


def _create_config_file(project_path: str) -> None:
    """Create config.yaml with default values from ConfigHandler."""
    config_path = os.path.join(project_path, "config.yaml")
    try:
        with open(ConfigHandler.DEFAULT_GLOBAL_CONFIG_PATH,'r') as f:
            global_yaml_data = yaml.safe_load(f)
    except Exception:
            global_yaml_data = ""
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(global_yaml_data,
                  f, default_flow_style=False)
    print("Created config.yaml with default values.")


def _copy_template(project_path: str, template: str) -> None:
    """Copy files from the specified template directory if it exists."""
    package_root = pathlib.Path(__file__).parent.parent
    template_path = package_root / "samples" / template
    if not os.path.exists(template_path):
        print(
            f"Template '{template}' not found in {os.path.abspath(template_path)}")
        return

    for item in os.listdir(template_path):
        src_item = os.path.join(template_path, item)
        dest_item = os.path.join(project_path, item)
        if os.path.isdir(src_item):
            shutil.copytree(src_item, dest_item)
        else:
            shutil.copy2(src_item, dest_item)
    print(f"Copied template '{template}' files into the project.")

def create_project(args):
    """
    Creates a new project structure for the Optics Framework.

    Parameters
    ----------
    args : argparse.Namespace
        The command-line arguments containing:
        - name (str): The name of the project (required).
        - path (str, optional): The directory where the project should be created.
        - force (bool, optional): If True, overrides an existing project directory.
        - template (str, optional): Name of a template to copy from `optics_framework/samples/`.
        - git_init (bool, optional): If True, initializes a Git repository in the project.

    Returns
    -------
    None
    """
    project_name = args.name
    base_path = args.path if args.path else os.getcwd()
    project_path = os.path.join(base_path, project_name)

    if not _check_and_prepare_directory(project_path, args.force):
        return

    _create_csv_files(project_path)
    _create_config_file(project_path)
    if args.template:
        _copy_template(project_path, args.template)
    if args.git_init:  # Check if git_init flag is set
        try:

            git_path = shutil.which("git")  # Get the absolute path of Git
            if git_path:
                subprocess.run([git_path, "init"], cwd=project_path,               #nosec B603
                            check=True, shell=False)
        except FileNotFoundError:
            print("Error: Git not found!")
        except subprocess.CalledProcessError as e:
            print(f"Error initializing git repository: {e}")
