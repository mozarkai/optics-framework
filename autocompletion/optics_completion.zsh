# Description: Zsh completion script for the Optics CLI tool
local -a _optics_subcommands=(
  'list: List available API methods'
  'config: Manage configuration'
  'dry_run: Run tests in dry-run mode'
  'init: Initialize a new project'
  'execute: Execute tests'
  'version: Show version'
  'generate: Generate framework code'
  'setup: Install drivers'
  'serve: Start a server'
  'completion: Enable shell completion'
)

# Static or dynamic values
local -a templates=("calender" "contact" "gmail_web" "youtube")
local -a runners=("test_runner" "pytest")
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
            '*:output_file:_files' \
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
