#!/bin/bash

# mapfile keeps each candidate one word: no splitting on spaces, no glob expansion.
_optics_words() {
  mapfile -t COMPREPLY < <(compgen -W "$1" -- "$cur")
}

# Files and folders: readline quotes the inserted names instead of letting the shell parse them.
_optics_paths() {
  compopt -o filenames 2>/dev/null
  mapfile -t COMPREPLY < <(compgen "$1" -- "$cur")
}

_optics_completions() {
  local cur prev words cword
  _init_completion || return

  local subcommands="list configure config doctor quickstart dry_run dry-run init execute live generate setup serve mcp completion"

  local template_options="calendar clock contact gmail_web mobile_web playwright youtube"
  local runner_options="test_runner pytest"
  # Only the two-space-indented lines are engine keys; category headers are not
  # indented, so this needs no per-header filter.
  local engine_options=$(optics setup --list 2>/dev/null | awk '/^  / {print $1}')

  case ${COMP_CWORD} in
    1)
      _optics_words "$subcommands --version --help -h"
      return 0
      ;;
    *)
      # Beyond the subcommand: handled per-subcommand below.
      ;;
  esac

  case ${COMP_WORDS[1]} in
    list|config|completion|quickstart)
      _optics_words "--help -h"
      ;;

    configure)
      if [[ $cur == -* ]]; then
        _optics_words "--edit -h --help"
      else
        _optics_paths -d
      fi
      ;;

    doctor)
      if [[ $cur == -* ]]; then
        _optics_words "--check -h --help"
      else
        _optics_paths -d
      fi
      ;;

    live)
      _optics_paths -d
      ;;

    mcp)
      if [[ $prev == "--transport" ]]; then
        _optics_words "stdio http"
      else
        _optics_words "--transport --host --port -h --help"
      fi
      ;;

    dry_run|dry-run|execute)
      if [[ $prev == "--runner" ]]; then
        _optics_words "$runner_options"
      else
        _optics_words "--runner --use-printer --no-use-printer --include --exclude -h --help"
      fi
      ;;

    init)
      case $prev in
        --template)
          _optics_words "$template_options"
          ;;
        *)
          _optics_words "--name --path --force --template --git-init -h --help"
          ;;
      esac
      ;;

    generate)
      _optics_words "-h --help"
      if [[ $prev == "--framework" ]]; then
        _optics_words "pytest robot"
      elif [[ $prev == "--output" ]]; then
        _optics_paths -f
      elif [[ $prev == "--project_path" ]]; then
        _optics_paths -d
      else
        : # keep the default --help completion set above
      fi
      ;;

    setup)
      case $prev in
        --install)
          _optics_words "$engine_options"
          ;;
        *)
          _optics_words "--install --list -h --help"
          ;;
      esac
      ;;

    serve)
      _optics_words "--host --port -h --help"
      ;;

    *)
      # Unknown subcommand: offer only --help.
      _optics_words "-h --help"
      ;;
  esac
}

complete -F _optics_completions optics
