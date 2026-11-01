#!/bin/sh
# Restore only into a newly created scratch database, never into PGDATABASE.
set -eu
dump=${1:?Usage: verify-restore.sh /path/to/pm-TIMESTAMP.dump}
directory=$(dirname "$dump")
filename=$(basename "$dump")
(cd "$directory" && sha256sum --check "$filename.sha256")
scratch="pm_restore_check_$(date -u +%Y%m%d%H%M%S)_$$"
createdb "$scratch"
trap 'dropdb --if-exists "$scratch"' EXIT HUP INT TERM
pg_restore --exit-on-error --no-owner --no-acl --dbname="$scratch" "$dump"
psql --dbname="$scratch" --no-psqlrc --set=ON_ERROR_STOP=1 --command='SELECT count(*) AS restored_tasks FROM tasks;'
echo 'Restore verified; scratch database will be removed.'
