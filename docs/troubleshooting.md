# Troubleshooting

Start with `ps3-ingest --check` and inspect its persistent audit. Missing IRDs,
missing required files, hash mismatches, executable changes, ambiguous
renames, and collisions are intentional fail-closed stops.

For publication issues, verify the final ISO exists under `PS3_ISO_ROOT`, the
ps3netsrv bind points to the same root, the library is readable by ps3netsrv,
and webMAN has been scanned. A pending XMB response means gameplay protection
worked; retry after returning to idle XMB.

For search issues, confirm the Prowlarr API key/container access, the PS3 leg's
advertised category, and the fallback leg's no-category configuration. Do not
add an unsupported provider category or change unrelated Arr/SAB routing.
