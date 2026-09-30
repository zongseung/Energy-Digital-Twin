#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/models.sh [plan|download|verify] [public|all|trellis2|decoder|dinov3|rmbg]
Default: plan public. plan downloads metadata only; download fetches weights.
HF_TOKEN or an existing HF login is used for gated repositories. Accept their terms first.
MODEL_ROOT defaults to <project>/var/models; revisions get separate directories.
Requires uv. Uses the pinned official huggingface-hub CLI, not a custom downloader.
EOF
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
action=${1:-plan}
scope=${2:-public}
[[ $# -le 2 ]] || { usage >&2; exit 2; }
case "$action" in plan|download|verify) ;; *) usage >&2; exit 2;; esac
case "$scope" in public|all|trellis2|decoder|dinov3|rmbg) ;; *) usage >&2; exit 2;; esac
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
model_root=${MODEL_ROOT:-$root/var/models}
command -v uvx >/dev/null || { echo 'Install uv to provide uvx.' >&2; exit 1; }
hf() { uvx --from huggingface-hub==2.0.0 hf "$@"; }

run() {
    local mode=$1 key repo revision access files directory file
    local -a selected=() verify_flags=()
    while IFS=$'\t' read -r key repo revision access files; do
        [[ $key == \#* || -z $key ]] && continue
        if [[ $scope != all && $scope != "$key" ]]; then
            [[ $scope == public && $access == public ]] || continue
        fi
        directory="$model_root/$key/$revision"
        selected=()
        verify_flags=()
        if [[ $files != '*' ]]; then
            read -r -a selected <<< "$files"
        else
            verify_flags=(--fail-on-missing-files)
        fi
        printf '%s %s @ %s\n' "$mode" "$repo" "$revision"
        case "$mode" in
            plan)
                hf download "$repo" "${selected[@]}" --revision "$revision" --local-dir "$directory" --dry-run
                ;;
            download)
                hf download "$repo" "${selected[@]}" --revision "$revision" --local-dir "$directory"
                ;;
            verify)
                [[ -d $directory ]] || { echo "Missing model: $key" >&2; return 1; }
                for file in "${selected[@]}"; do
                    [[ -s $directory/$file ]] || { echo "Missing file: $key/$file" >&2; return 1; }
                done
                hf cache verify "$repo" --revision "$revision" --local-dir "$directory" "${verify_flags[@]}"
                ;;
        esac
    done < "$root/models/manifest.tsv"
}

# Check access to every selected repository before any large download starts.
if [[ $action == download ]]; then run plan; fi
run "$action"
