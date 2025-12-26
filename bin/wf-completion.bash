# Bash completion for wf
_wf_completions() {
    local cur prev commands
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Main commands
    commands="new list use refresh status show conflicts run close merge archive open approve reject reset clarify pm"
    
    # Subcommands
    case "$prev" in
        wf)
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
            return 0
            ;;
        use|run|status|show|conflicts|close|merge|approve|reject|reset|refresh)
            # Complete with workstream IDs
            local ws_dir="${WF_OPS_DIR:-$(dirname $(dirname $(realpath "${COMP_WORDS[0]}")))}/workstreams"
            if [[ -d "$ws_dir" ]]; then
                local workstreams=$(ls -1 "$ws_dir" 2>/dev/null | tr '\n' ' ')
                COMPREPLY=($(compgen -W "$workstreams" -- "$cur"))
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
