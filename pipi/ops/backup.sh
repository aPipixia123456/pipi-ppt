#!/usr/bin/env bash
# Encrypted, consistent backup. Requires Docker Compose, age and util-linux.
set -euo pipefail
umask 077
: "${PIPI_BACKUP_DIR:?Set an absolute backup directory outside the repository}"
: "${PIPI_BACKUP_RECIPIENT:?Set the public age recipient; never the private key}"
[[ "$PIPI_BACKUP_DIR" = /* ]] || { echo 'Backup directory must be absolute' >&2; exit 1; }
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd -- "$root"
compose=(docker compose -f pipi/compose.yaml)
mkdir -p -- "$PIPI_BACKUP_DIR"
exec 9>"$PIPI_BACKUP_DIR/.backup.lock"
flock -n 9 || { echo 'Backup already running' >&2; exit 1; }
command -v age >/dev/null
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$PIPI_BACKUP_DIR/$stamp.incomplete"
mkdir -- "$target"
running=()
while IFS= read -r name; do
    case "$name" in api|web|scheduler|worker|exporter) running+=("$name");; esac
done < <("${compose[@]}" ps --services --status running)
restore_services() {
    if ((${#running[@]})); then "${compose[@]}" start "${running[@]}"; fi
}
trap restore_services EXIT
if ((${#running[@]})); then "${compose[@]}" stop --timeout 540 "${running[@]}"; fi
"${compose[@]}" exec -T postgres pg_dump -U pipi -d pipi -Fc \
    | age -r "$PIPI_BACKUP_RECIPIENT" > "$target/database.dump.age"
"${compose[@]}" run --rm --no-deps -T --entrypoint tar api \
    -C /data --exclude=./tmp --exclude='./celerybeat-schedule*' -czf - . \
    | age -r "$PIPI_BACKUP_RECIPIENT" > "$target/assets.tar.gz.age"
(cd -- "$target" && sha256sum database.dump.age assets.tar.gz.age > SHA256SUMS)
mv -- "$target" "$PIPI_BACKUP_DIR/$stamp"
echo "Encrypted backup completed: $stamp"
