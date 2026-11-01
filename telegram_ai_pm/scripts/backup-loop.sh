#!/bin/sh
set -eu
while true; do
    sh /scripts/backup.sh
    sleep 86400 &
    wait "$!"
done
