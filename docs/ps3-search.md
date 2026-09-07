# Search design

`ps3-search` runs two isolated Prowlarr searches and merges the returned JSON:

1. A configured indexer that advertises PS3 receives the configured PS3
   category, normally `1080`.
2. A configured indexer without a Console capability receives no category
   filter.

Indexer IDs, names, URL, API key, and container are environment-configured.
The helper does not modify Prowlarr capabilities and does not call any grab or
download endpoint. Output retains the full result object in `--json` mode and
deduplicates obvious duplicates by normalized title and size. Home's **Open in
Prowlarr** action opens the matching title/indexer/category in Prowlarr; the
user must deliberately choose any subsequent download action there.

Set `PROWLARR_CONTAINER=` for direct HTTP API access. Set
`PROWLARR_PUBLIC_URL` to the Prowlarr address reachable by the user's browser.

```sh
ps3-search "title or title ID" --limit 25
ps3-search "title or title ID" --json > results.json
```
