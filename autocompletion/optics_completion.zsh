# Description: Zsh completion script for the Optics CLI tool
local -a _optics_subcommands=(
  'list: List available API methods'
  'configure: Edit a project config.yaml (guided prompts or --edit TUI)'
  'config: Deprecated alias for configure'
  'doctor: Diagnose a project setup (engines, config, tooling)'
  'quickstart: Guided walkthrough to create and configure a first project'
  'dry_run: Run tests in dry-run mode'
  'dry-run: Run tests in dry-run mode'
  'init: Initialize a new project'
  'execute: Execute tests'
  'live: Interactive session against a live target'
  'generate: Generate framework code'
  'setup: Install optional engine backends'
  'serve: Start the optics REST server'
  'mcp: Start the MCP server'
  'completion: Enable shell completion'
)

# Static or dynamic values
local -a templates=("calendar" "clock" "contact" "gmail_web" "mobile_web" "playwright" "youtube")
local -a runners=("test_runner" "pytest")
local -a frameworks=("pytest" "robot")
local -a transports=("stdio" "http")
# `optics setup --list` indents each installable engine key by two spaces under a
# category header; take only those indented lines so this stays correct no matter
# what the category headers are named.
local -a engines=("${(f)$(optics setup --list 2>/dev/null | awk '/^  / {print $1}')}")

_optics_completions() {
  local state

  _arguments -C \
    '(-)--version[Show the Optics Framework version]' \
    '(-)--help[-h]' \
    '1:command:->cmds' \
    '*::arg:->args'

  case $state in
    cmds)
      _describe 'command' _optics_subcommands
      ;;
    args)
      case $words[2] in
        list|config|completion|quickstart)
          _arguments '--help[-h]'
          ;;

        configure)
          _arguments \
            '*:project_folder:_files -/' \
            '--edit[Launch the full-field Textual editor]' \
            '--help[-h]'
          ;;

        doctor)
          _arguments \
            '*:project_folder:_files -/' \
            '--check[Exit non-zero if any check fails]' \
            '--help[-h]'
          ;;

        live)
          _arguments \
            '*:project_path:_files -/' \
            '--help[-h]'
          ;;

        mcp)
          _arguments \
            '--transport=[Transport]:transport:(${transports[@]})' \
            '--host=[Host address]' \
            '--port=[Port number]' \
            '--help[-h]'
          ;;

        dry_run|dry-run|execute)
          _arguments \
            '--runner=[Runner]:runner:(${runners[@]})' \
            '--use-printer[Enable printer]' \
            '--no-use-printer[Disable printer]' \
            '*--include=[Run only these test cases]:test case:' \
            '*--exclude=[Skip these test cases]:test case:' \
            '--help[-h]'
          ;;

        init)
          _arguments \
            '1:project_name:' \
            '--name=[Project name (deprecated; use the positional)]' \
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
            '--framework=[Framework]:framework:(${frameworks[@]})' \
            '--help[-h]'
          ;;

        setup)
          _arguments \
            "--install=[Engines]:engines:(${engines[@]})" \
            '--list[List all engines]' \
            '--help[-h]'
          ;;

        serve)
          _arguments \
            '--host=[Host address]' \
            '--port=[Port number]' \
            '--help[-h]'
          ;;

        *)
          # Unknown subcommand: offer only --help.
          _arguments '--help[-h]'
          ;;
      esac
      ;;

    *)
      # Unexpected state: nothing to complete.
      ;;
  esac
}

compdef _optics_completions optics
