#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
port="/dev/ttyUSB0"

args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--port" ]] && (( i + 1 < ${#args[@]} )); then
    port="${args[i+1]}"
  fi
done

if [[ -e "$port" && ( ! -r "$port" || ! -w "$port" ) ]]; then
  if ! /usr/bin/pkexec /usr/bin/setfacl -m "u:${USER}:rw" "$port"; then
    /usr/bin/zenity --error \
      --title="RF Explorer Linux Viewer" \
      --text="Nao foi possivel liberar acesso a $port.\n\nAutorize a permissao administrativa ou adicione o usuario ao grupo dialout."
    exit 1
  fi
fi

exec /usr/bin/python3 "$script_dir/rfexplorer_linux_gui.py" "$@"
