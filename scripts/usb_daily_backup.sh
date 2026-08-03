#!/bin/bash
# Daily cold backup to the 1 TB USB drive (blackstar-backup).
# Dossier + ANCHORUM reports + factory reports. NTFS-safe flags.
set -u

DEST="/media/kark/7A7666CA4E34EADC2/blackstar-backup"
LOG="/home/kark/blackstar/logs/usb_backup.log"

if [ ! -d "/media/kark/7A7666CA4E34EADC2" ]; then
    echo "$(date -Is) SKIP: USB drive not mounted" >> "$LOG"
    exit 0  # not an error — drive may be unplugged; next run catches up
fi

mkdir -p "$DEST"
{
    echo "$(date -Is) START"
    rsync -rL --no-perms --no-owner --no-group --chmod=ugo=rwX \
        "/mnt/blackstar/vol-hdd-b/home_reloc/Desktop/Dossier Molson" "$DEST/"
    RS1=$?
    rsync -rL --no-perms --no-owner --no-group \
        /home/kark/blackstar/report "$DEST/egregore/"
    RS2=$?
    rsync -rL --no-perms --no-owner --no-group \
        /home/kark/anchorum-workspace/reports "$DEST/"
    RS3=$?
    echo "$(date -Is) DONE rsync=$RS1/$RS2/$RS3"
    # rsync 23 = partial transfer (one known dangling .venv symlink in the
    # dossier, skipped by design). 0/1/23 are all acceptable for the dossier.
    case "$RS1" in 0|1|23) ;; *) exit 1;; esac
    [ "$RS2" -eq 0 ] && [ "$RS3" -eq 0 ]
} >> "$LOG" 2>&1
