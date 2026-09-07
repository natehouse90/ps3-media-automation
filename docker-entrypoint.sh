#!/bin/sh
set -eu

# Bind mounts are often created root-owned by Docker. Restrict ownership changes
# to directories the automation must write; never chown the published tree.
for path in \
  /srv/ps3-library/incoming \
  /srv/ps3-library/.iso-build-work \
  /srv/ps3-library/.ingest/irds \
  /srv/ps3-library/.ingest/audits \
  /srv/ps3-library/PS3ISO \
  /var/lib/ps3-media-automation; do
  mkdir -p "$path"
  chown app:app "$path"
done

exec gosu app "$@"
