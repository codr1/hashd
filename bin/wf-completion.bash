# Bash completion for wf
_wf_completions() {
    local cur prev commands
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Main commands
    commands="plan new list use refresh status show log watch conflicts run close merge archive open approve reject reset clarify pm review"
    
    # Subcommands
    case "$prev" in
        wf)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            return 0
            ;;
        use|run|status|show|log|watch|conflicts|close|merge|approve|reject|reset|refresh|review)
            # Complete with workstream IDs and story IDs
            local ops_dir="${WF_OPS_DIR:-$(dirname $(dirname $(realpath "${COMP_WORDS[0]}")))}"
            local ws_dir="$ops_dir/workstreams"
            local ids=""
            # Workstreams (exclude _closed)
            if [[ -d "$ws_dir" ]]; then
                ids=$(ls -1 "$ws_dir" 2>/dev/null | grep -v '^_' | tr '\n' ' ')
            fi
            # Stories from all projects
            for story_dir in "$ops_dir"/projects/*/pm/stories; do
                if [[ -d "$story_dir" ]]; then
                    ids="$ids $(ls -1 "$story_dir" 2>/dev/null | grep '\.json$' | sed 's/\.json$//' | tr '\n' ' ')"
                fi
            done
            COMPREPLY=($(compgen -W "$ids" -- "$cur"))
            return 0
            ;;
        plan)
            COMPREPLY=($(compgen -W "new edit clone" -- "$cur"))
            return 0
            ;;
        edit|clone)
            # Complete with story IDs for wf plan edit/clone
            if [[ "${COMP_WORDS[1]}" == "plan" ]]; then
                local ops_dir="${WF_OPS_DIR:-$(dirname $(dirname $(realpath "${COMP_WORDS[0]}")))}"
                local ids=""
                for story_dir in "$ops_dir"/projects/*/pm/stories; do
                    if [[ -d "$story_dir" ]]; then
                        ids="$ids $(ls -1 "$story_dir" 2>/dev/null | grep '\.json$' | sed 's/\.json$//' | tr '\n' ' ')"
                    fi
                done
                COMPREPLY=($(compgen -W "$ids" -- "$cur"))
            fi
            return 0
            ;;
        clarify)
            COMPREPLY=($(compgen -W "list show answer ask" -- "$cur"))
            return 0
            ;;
        pm)
            COMPREPLY=($(compgen -W "plan refine spec status list show" -- "$cur"))
            return 0
            ;;
        archive)
            COMPREPLY=($(compgen -W "delete" -- "$cur"))
            return 0
            ;;
        open)
            # Complete with archived workstream IDs
            local closed_dir="${WF_OPS_DIR:-$(dirname $(dirname $(realpath "${COMP_WORDS[0]}")))}/workstreams/_closed"
            if [[ -d "$closed_dir" ]]; then
                local workstreams=$(ls -1 "$closed_dir" 2>/dev/null | tr '\n' ' ')
                COMPREPLY=($(compgen -W "$workstreams" -- "$cur"))
            fi
            return 0
            ;;
    esac
    
    # Flags
    case "$cur" in
        -*)
            case "${COMP_WORDS[1]}" in
                use)
                    COMPREPLY=($(compgen -W "--clear" -- "$cur"))
                    ;;
                run)
                    COMPREPLY=($(compgen -W "--once --loop --verbose -v" -- "$cur"))
                    ;;
                reject|reset)
                    COMPREPLY=($(compgen -W "--feedback -f" -- "$cur"))
                    ;;
                show)
                    COMPREPLY=($(compgen -W "--brief -b" -- "$cur"))
                    ;;
                log)
                    COMPREPLY=($(compgen -W "--since -s --limit -n --verbose -v --reverse -r --no-color" -- "$cur"))
                    ;;
                close)
                    COMPREPLY=($(compgen -W "--force" -- "$cur"))
                    ;;
                merge)
                    COMPREPLY=($(compgen -W "--push" -- "$cur"))
                    ;;
                open)
                    COMPREPLY=($(compgen -W "--use --force" -- "$cur"))
                    ;;
            esac
            return 0
            ;;
    esac
}

complete -F _wf_completions wf
