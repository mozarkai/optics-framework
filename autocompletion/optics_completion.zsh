# Description: Zsh completion script for the Optics CLI tool
local -a _optics_subcommands=(
    'list: List available API methods'
    'config: Manage configuration'
    'dry_run: Run tests in dry-run mode'
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
local -a templates=("contact" "clock" "calendar" "youtube" "gmail_web" "playwright")
local -a runners=("test_runner" "pytest")
local -a frameworks=("pytest" "robot")
local -a transports=("stdio" "http")
local -a drivers=("${(f)$(optics setup --list 2>/dev/null | awk '{print $1}' | grep -vE '^(Action|Available|Text|LLM)$')}")

_optics_completions() {
    local state

    _arguments -C         '1:command:->cmds'         '*::arg:->args'

    case $state in
        cmds)
            _describe 'command' _optics_subcommands
            ;;
        args)
            case $words[2] in
                list|config|completion)
                    _arguments '--help[-h]'
                    ;;

                live)
                    _arguments                         '*:project_path:_files -/'                         '--help[-h]'
                    ;;

                mcp)
                    _arguments                         '--transport=[Transport]:transport:(${transports[@]})'                         '--host=[Host address]'                         '--port=[Port number]'                         '--help[-h]'
                    ;;

                dry_run|execute)
                    _arguments                         '--runner=[Runner]:runner:(${runners[@]})'                         '--use-printer[Enable printer]'                         '--no-use-printer[Disable printer]'                         '--help[-h]'
                    ;;

                init)
                    _arguments                         '--name=[Project name]'                         '--path=[Project path]'                         '--force[Override existing]'                         "--template=[Template name]:template:(${templates[@]})"                         '--git-init[Initialize Git]'                         '--help[-h]'
                    ;;

                generate)
                    _arguments                         '*:project_path:_files'                         '*:output:_files'                         '--framework=[Framework]:framework:(${frameworks[@]})'                         '--help[-h]'
                    ;;

                setup)
                    _arguments                         "--install=[Drivers]:drivers:(${drivers[@]})"                         '--list[List all drivers]'                         '--help[-h]'
                    ;;

                serve)
                    _arguments                         '--host=[Host address]'                         '--port=[Port number]'                         '--help[-h]'
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
