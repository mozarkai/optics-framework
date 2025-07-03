import os
from pathlib import Path
from typing import Optional

ZSH_COMPLETION_CONTENT = """# Description: Zsh completion script for the Optics CLI tool
local -a _optics_subcommands=(
    'list: List available API methods'
    'config: Manage configuration'
    'dry_run: Run tests in dry-run mode'
    'init: Initialize a new project'
    'execute: Execute tests'
    'version: Show version'
    'generate: Generate framework code'
    'setup: Install drivers'
    'serve: Start the optics server'
    'completion: Enable shell completion'
)

# Static or dynamic values
local -a templates=("calender" "contact" "gmail_web" "youtube")
local -a runners=("test_runner" "pytest")
local -a frameworks=("pytest" "robot")
local -a drivers=("${(f)$(optics setup --list 2>/dev/null | awk '{print $1}' | grep -vE '^(Action|Available|Drivers:|Text)$')}")

_optics_completions() {
    local state

    _arguments -C \
        '1:command:->cmds' \
        '*::arg:->args'

    case $state in
        cmds)
            _describe 'command' _optics_subcommands
            ;;
        args)
            case $words[2] in
                list|config|version|completion)
                    _arguments '--help[-h]'
                    ;;

                dry_run|execute)
                    _arguments \
                        '--runner=[Runner]:runner:(${runners[@]})' \
                        '--use-printer[Enable printer]' \
                        '--no-use-printer[Disable printer]' \
                        '--help[-h]'
                    ;;

                init)
                    _arguments \
                        '--name=[Project name]' \
                        '--path=[Project path]' \
                        '--force[Override existing]' \
                        "--template=[Template name]:template:(${templates[@]})" \
                        '--git-init[Initialize Git]' \
                        '--help[-h]'
                    ;;

                generate)
                    _arguments \
                        '*:project_path:_files' \
                        '*:output:_files' \
                        '--framework=[Framework]:framework:(${frameworks[@]})' \
                        '--help[-h]'
                    ;;

                setup)
                    _arguments \
                        "--install=[Drivers]:drivers:(${drivers[@]})" \
                        '--list[List all drivers]' \
                        '--help[-h]'
                    ;;

                serve)
                    _arguments \
                        '--host=[Host address]' \
                        '--port=[Port number]' \
                        '--help[-h]'
                    ;;
            esac
            ;;
    esac
}

compdef _optics_completions optics
"""

BASH_COMPLETION_CONTENT = """#!/bin/bash

_optics_completions() {
  local cur prev words cword
  _init_completion || return

  local subcommands="list config dry_run init execute version generate setup serve completion"

  local template_options="calender contact gmail_web youtube"
  local runner_options="test_runner pytest"
  local driver_options=$(optics setup --list 2>/dev/null | awk '{print $1}' | grep -vE '^(Action|Available|Drivers:|Text)$')

  case ${COMP_CWORD} in
    1)
      COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )
      return 0
      ;;
  esac

  case ${COMP_WORDS[1]} in
    list|config|version|completion)
      COMPREPLY=( $(compgen -W "--help -h" -- "$cur") )
      ;;

    dry_run|execute)
      if [[ $prev == "--runner" ]]; then
        COMPREPLY=( $(compgen -W "$runner_options" -- "$cur") )
      else
        COMPREPLY=( $(compgen -W "--runner --use-printer --no-use-printer -h --help" -- "$cur") )
      fi
      ;;

    init)
      case $prev in
        --template)
          COMPREPLY=( $(compgen -W "$template_options" -- "$cur") )
          ;;
        *)
          COMPREPLY=( $(compgen -W "--name --path --force --template --git-init -h --help" -- "$cur") )
          ;;
      esac
      ;;

    generate)
      COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
        if [[ $prev == "--framework" ]]; then
            COMPREPLY=( $(compgen -W "pytest robot" -- "$cur") )
        elif [[ $prev == "--output" ]]; then
            COMPREPLY=( $(compgen -f -- "$cur") )
        elif [[ $prev == "--project_path" ]]; then
            COMPREPLY=( $(compgen -d -- "$cur") )
        fi
      ;;

    setup)
      case $prev in
        --install)
          COMPREPLY=( $(compgen -W "$driver_options" -- "$cur") )
          ;;
        *)
          COMPREPLY=( $(compgen -W "--install --list -h --help" -- "$cur") )
          ;;
      esac
      ;;

    serve)
      COMPREPLY=( $(compgen -W "--host --port -h --help" -- "$cur") )
      ;;
  esac
}

complete -F _optics_completions optics
"""

def write_completion_scripts():
    optics_dir = Path.home() / ".optics"
    optics_dir.mkdir(exist_ok=True)
    zsh_path = optics_dir / "optics_completion.zsh"
    bash_path = optics_dir / "optics_completion.sh"
    zsh_path.write_text(ZSH_COMPLETION_CONTENT)
    bash_path.write_text(BASH_COMPLETION_CONTENT)
    return zsh_path, bash_path

def update_shell_rc(shell: Optional[str] = None):
    zsh_path, bash_path = write_completion_scripts()
    home = Path.home()
    if shell is None:
        shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        rc_file = home / ".zshrc"
        completion_file = zsh_path
        source_line = f"source {completion_file}\n"
    elif "bash" in shell:
        rc_file = home / ".bashrc"
        completion_file = bash_path
        source_line = f"source {completion_file}\n"
    else:
        print("Unsupported shell. Only bash and zsh are supported.")
        return

    # Avoid duplicate source lines
    if rc_file.exists():
        with open(rc_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if any(source_line.strip() in line.strip() for line in lines):
            print(f"Autocompletion already enabled in {rc_file}")
            return

    with open(rc_file, "a", encoding="utf-8") as f:
        f.write(f"\n# Optics CLI autocompletion\n{source_line}")
    print(f"Added autocompletion to {rc_file}. Please restart your shell or run: source {rc_file}")
