"""
Shell completion script generation for wf CLI.
"""

BASH_COMPLETION = '''
_wf_completions() {
    local cur prev words cword
    _init_completion || return

    local commands="plan list use show log review watch refresh conflicts run close merge archive open approve reject clarify"
    local plan_cmds="new clone add edit"
    local archive_cmds="work stories delete"
    local clarify_cmds="list show answer ask"

    # Get ops dir from environment or default
    local ops_dir="${WF_OPS_DIR:-$(pwd)}"

    # Flag completion - check if current word starts with -
    if [[ "$cur" == -* ]]; then
        local cmd="${words[1]}"
        local flags=""
        case "$cmd" in
            run)      flags="--once --loop --verbose -v" ;;
            reject)   flags="--feedback -f --reset" ;;
            log)      flags="--since -s --limit -n --verbose -v --reverse -r --no-color" ;;
            use)      flags="--clear" ;;
            close)    flags="--force" ;;
            merge)    flags="--push" ;;
            open)     flags="--use --force" ;;
            plan)
                if [[ "${words[2]}" == "edit" ]]; then
                    flags="--feedback -f"
                elif [[ "${words[2]}" == "add" ]]; then
                    flags="--description -d"
                fi
                ;;
        esac
        if [[ -n "$flags" ]]; then
            COMPREPLY=($(compgen -W "$flags" -- "$cur"))
            return
        fi
    fi

    case $cword in
        1)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            ;;
        2)
            case ${words[1]} in
                plan)
                    # Subcommands or story IDs
                    local stories=""
                    if [[ -d "$ops_dir/projects" ]]; then
                        for f in "$ops_dir/projects"/*/pm/stories/STORY-*.json; do
                            [[ -f "$f" ]] && stories+=" $(basename "${f%.json}")"
                        done
                    fi
                    COMPREPLY=($(compgen -W "$plan_cmds $stories" -- "$cur"))
                    ;;
                run|show|use|close|approve)
                    # Workstream IDs and story IDs
                    local ws=""
                    if [[ -d "$ops_dir/workstreams" ]]; then
                        for d in "$ops_dir/workstreams"/*/; do
                            [[ -d "$d" && ! "$(basename "$d")" =~ ^_ ]] && ws+=" $(basename "$d")"
                        done
                    fi
                    local stories=""
                    if [[ -d "$ops_dir/projects" ]]; then
                        for f in "$ops_dir/projects"/*/pm/stories/STORY-*.json; do
                            [[ -f "$f" ]] && stories+=" $(basename "${f%.json}")"
                        done
                    fi
                    COMPREPLY=($(compgen -W "$ws $stories" -- "$cur"))
                    ;;
                merge|conflicts|refresh|log|review|watch|reject)
                    # Workstream IDs only
                    local ws=""
                    if [[ -d "$ops_dir/workstreams" ]]; then
                        for d in "$ops_dir/workstreams"/*/; do
                            [[ -d "$d" && ! "$(basename "$d")" =~ ^_ ]] && ws+=" $(basename "$d")"
                        done
                    fi
                    COMPREPLY=($(compgen -W "$ws" -- "$cur"))
                    ;;
                archive)
                    COMPREPLY=($(compgen -W "$archive_cmds" -- "$cur"))
                    ;;
                clarify)
                    COMPREPLY=($(compgen -W "$clarify_cmds" -- "$cur"))
                    ;;
                open)
                    # Archived workstream IDs
                    local archived=""
                    if [[ -d "$ops_dir/workstreams/_closed" ]]; then
                        for d in "$ops_dir/workstreams/_closed"/*/; do
                            [[ -d "$d" ]] && archived+=" $(basename "$d")"
                        done
                    fi
                    if [[ -d "$ops_dir/workstreams/_merged" ]]; then
                        for d in "$ops_dir/workstreams/_merged"/*/; do
                            [[ -d "$d" ]] && archived+=" $(basename "$d")"
                        done
                    fi
                    COMPREPLY=($(compgen -W "$archived" -- "$cur"))
                    ;;
            esac
            ;;
        3)
            case ${words[1]} in
                plan)
                    if [[ "${words[2]}" == "clone" || "${words[2]}" == "edit" ]]; then
                        # Story IDs for clone/edit
                        local stories=""
                        if [[ -d "$ops_dir/projects" ]]; then
                            for f in "$ops_dir/projects"/*/pm/stories/STORY-*.json; do
                                [[ -f "$f" ]] && stories+=" $(basename "${f%.json}")"
                            done
                        fi
                        COMPREPLY=($(compgen -W "$stories" -- "$cur"))
                    elif [[ "${words[2]}" == "add" ]]; then
                        # Workstream IDs for add
                        local ws=""
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            for d in "$ops_dir/workstreams"/*/; do
                                [[ -d "$d" && ! "$(basename "$d")" =~ ^_ ]] && ws+=" $(basename "$d")"
                            done
                        fi
                        COMPREPLY=($(compgen -W "$ws" -- "$cur"))
                    fi
                    ;;
                archive)
                    if [[ "${words[2]}" == "delete" ]]; then
                        # Archived workstream IDs
                        local archived=""
                        if [[ -d "$ops_dir/workstreams/_closed" ]]; then
                            for d in "$ops_dir/workstreams/_closed"/*/; do
                                [[ -d "$d" ]] && archived+=" $(basename "$d")"
                            done
                        fi
                        if [[ -d "$ops_dir/workstreams/_merged" ]]; then
                            for d in "$ops_dir/workstreams/_merged"/*/; do
                                [[ -d "$d" ]] && archived+=" $(basename "$d")"
                            done
                        fi
                        COMPREPLY=($(compgen -W "$archived" -- "$cur"))
                    fi
                    ;;
                clarify)
                    case ${words[2]} in
                        show|answer|ask)
                            # Workstream IDs
                            local ws=""
                            if [[ -d "$ops_dir/workstreams" ]]; then
                                for d in "$ops_dir/workstreams"/*/; do
                                    [[ -d "$d" && ! "$(basename "$d")" =~ ^_ ]] && ws+=" $(basename "$d")"
                                done
                            fi
                            COMPREPLY=($(compgen -W "$ws" -- "$cur"))
                            ;;
                    esac
                    ;;
            esac
            ;;
    esac
}

complete -F _wf_completions wf
'''

ZSH_COMPLETION = '''
#compdef wf

_wf() {
    local -a commands
    commands=(
        'plan:Plan stories from REQS.md or ad-hoc'
        'list:List stories and workstreams'
        'use:Set/show current workstream'
        'show:Show story or workstream details'
        'log:Show workstream timeline'
        'review:Final AI review before merge'
        'watch:Interactive TUI for monitoring'
        'refresh:Refresh touched files'
        'conflicts:Check file conflicts'
        'run:Run cycle'
        'close:Close story or workstream'
        'merge:Merge workstream to main'
        'archive:View archived work and stories'
        'open:Resurrect archived workstream'
        'approve:Accept story or approve workstream gate'
        'reject:Reject and iterate on current changes'
        'clarify:Manage clarification requests'
    )

    local ops_dir="${WF_OPS_DIR:-$(pwd)}"

    _arguments -C \\
        '(-p --project)'{-p,--project}'[Project name]:project:' \\
        '1: :->command' \\
        '*:: :->args'

    case $state in
        command)
            _describe -t commands 'wf commands' commands
            ;;
        args)
            case $words[1] in
                plan)
                    local -a plan_cmds stories
                    plan_cmds=('new:Create ad-hoc story' 'clone:Clone a locked story' 'edit:Edit existing story' 'add:Add micro-commit to workstream')
                    if [[ -d "$ops_dir/projects" ]]; then
                        stories=(${(@f)"$(find "$ops_dir/projects" -path '*/pm/stories/STORY-*.json' -exec basename {} .json \\; 2>/dev/null)"})
                    fi
                    _describe -t plan_cmds 'plan subcommands' plan_cmds
                    _describe -t stories 'stories' stories
                    # Flags for plan subcommands
                    if [[ "$words[2]" == "edit" ]]; then
                        _arguments '(-f --feedback)'{-f,--feedback}'[Feedback for refinement]:feedback:'
                    elif [[ "$words[2]" == "add" ]]; then
                        _arguments '(-d --description)'{-d,--description}'[Commit description]:description:'
                    fi
                    ;;
                run)
                    _arguments \\
                        '--once[Run single cycle]' \\
                        '--loop[Run continuously]' \\
                        '(-v --verbose)'{-v,--verbose}'[Verbose output]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                    fi
                    ;;
                reject)
                    _arguments \\
                        '(-f --feedback)'{-f,--feedback}'[Feedback for iteration]:feedback:' \\
                        '--reset[Discard changes and start fresh]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                    fi
                    ;;
                show)
                    _arguments \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws stories
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        if [[ -d "$ops_dir/projects" ]]; then
                            stories=(${(@f)"$(find "$ops_dir/projects" -path '*/pm/stories/STORY-*.json' -exec basename {} .json \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                        _describe -t stories 'stories' stories
                    fi
                    ;;
                log)
                    _arguments \\
                        '(-s --since)'{-s,--since}'[Show since date]:date:' \\
                        '(-n --limit)'{-n,--limit}'[Limit entries]:count:' \\
                        '(-v --verbose)'{-v,--verbose}'[Verbose output]' \\
                        '(-r --reverse)'{-r,--reverse}'[Reverse order]' \\
                        '--no-color[Disable color]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                    fi
                    ;;
                use)
                    _arguments \\
                        '--clear[Clear current workstream]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws stories
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        if [[ -d "$ops_dir/projects" ]]; then
                            stories=(${(@f)"$(find "$ops_dir/projects" -path '*/pm/stories/STORY-*.json' -exec basename {} .json \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                        _describe -t stories 'stories' stories
                    fi
                    ;;
                close)
                    _arguments \\
                        '--force[Force close]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws stories
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        if [[ -d "$ops_dir/projects" ]]; then
                            stories=(${(@f)"$(find "$ops_dir/projects" -path '*/pm/stories/STORY-*.json' -exec basename {} .json \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                        _describe -t stories 'stories' stories
                    fi
                    ;;
                merge)
                    _arguments \\
                        '--push[Push after merge]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a ws
                        if [[ -d "$ops_dir/workstreams" ]]; then
                            ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                        fi
                        _describe -t ws 'workstreams' ws
                    fi
                    ;;
                open)
                    _arguments \\
                        '--use[Set as current after opening]' \\
                        '--force[Force open]' \\
                        '*:workstream:->ws'
                    if [[ "$state" == "ws" ]]; then
                        local -a archived
                        if [[ -d "$ops_dir/workstreams/_closed" ]]; then
                            archived+=(${(@f)"$(find "$ops_dir/workstreams/_closed" -mindepth 1 -maxdepth 1 -type d -exec basename {} \\; 2>/dev/null)"})
                        fi
                        if [[ -d "$ops_dir/workstreams/_merged" ]]; then
                            archived+=(${(@f)"$(find "$ops_dir/workstreams/_merged" -mindepth 1 -maxdepth 1 -type d -exec basename {} \\; 2>/dev/null)"})
                        fi
                        _describe -t archived 'archived workstreams' archived
                    fi
                    ;;
                approve)
                    local -a ws stories
                    if [[ -d "$ops_dir/workstreams" ]]; then
                        ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                    fi
                    if [[ -d "$ops_dir/projects" ]]; then
                        stories=(${(@f)"$(find "$ops_dir/projects" -path '*/pm/stories/STORY-*.json' -exec basename {} .json \\; 2>/dev/null)"})
                    fi
                    _describe -t ws 'workstreams' ws
                    _describe -t stories 'stories' stories
                    ;;
                merge|conflicts|refresh|review|watch)
                    local -a ws
                    if [[ -d "$ops_dir/workstreams" ]]; then
                        ws=(${(@f)"$(find "$ops_dir/workstreams" -mindepth 1 -maxdepth 1 -type d ! -name '_*' -exec basename {} \\; 2>/dev/null)"})
                    fi
                    _describe -t ws 'workstreams' ws
                    ;;
            esac
            ;;
    esac
}

_wf "$@"
'''


def generate_completion(shell: str) -> str:
    """
    Generate shell completion script.

    Args:
        shell: 'bash', 'zsh', or 'fish'

    Returns:
        Completion script content
    """
    if shell == 'bash':
        return BASH_COMPLETION.strip()
    elif shell == 'zsh':
        return ZSH_COMPLETION.strip()
    elif shell == 'fish':
        # Fish completion is simpler, basic implementation
        return '''
# wf fish completion
complete -c wf -f

complete -c wf -n "__fish_use_subcommand" -a plan -d "Plan stories"
complete -c wf -n "__fish_use_subcommand" -a list -d "List stories and workstreams"
complete -c wf -n "__fish_use_subcommand" -a use -d "Set/show current workstream"
complete -c wf -n "__fish_use_subcommand" -a show -d "Show story or workstream details"
complete -c wf -n "__fish_use_subcommand" -a log -d "Show workstream timeline"
complete -c wf -n "__fish_use_subcommand" -a review -d "Final AI review before merge"
complete -c wf -n "__fish_use_subcommand" -a watch -d "Interactive TUI for monitoring"
complete -c wf -n "__fish_use_subcommand" -a refresh -d "Refresh touched files"
complete -c wf -n "__fish_use_subcommand" -a conflicts -d "Check file conflicts"
complete -c wf -n "__fish_use_subcommand" -a run -d "Run cycle"
complete -c wf -n "__fish_use_subcommand" -a close -d "Close story or workstream"
complete -c wf -n "__fish_use_subcommand" -a merge -d "Merge workstream to main"
complete -c wf -n "__fish_use_subcommand" -a archive -d "List archived workstreams"
complete -c wf -n "__fish_use_subcommand" -a open -d "Resurrect archived workstream"
complete -c wf -n "__fish_use_subcommand" -a approve -d "Accept story or approve gate"
complete -c wf -n "__fish_use_subcommand" -a reject -d "Reject and iterate"
complete -c wf -n "__fish_use_subcommand" -a clarify -d "Manage clarifications"
'''.strip()
    else:
        raise ValueError(f"Unknown shell: {shell}")
