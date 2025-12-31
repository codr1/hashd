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
                    flags="--feedback -f"
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
