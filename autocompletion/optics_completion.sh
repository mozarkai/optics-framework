#!/bin/bash

_optics_completions() {
  local cur prev words cword
  _init_completion || return

  local subcommands="list config dry_run init execute version generate setup"

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
    list|config|version)
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
  esac
}

complete -F _optics_completions optics
