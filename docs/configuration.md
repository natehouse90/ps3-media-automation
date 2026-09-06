# Configuration reference

All paths and endpoints are environment variables; there are no required
private defaults. `PS3_LIBRARY_ROOT` is the common root. `PS3_WORK_ROOT` and
`PS3_STATE_ROOT` should be persistent and outside volatile temporary storage.
`PS3_MAKEPS3ISO` and `PS3_IRD_PARSER_ROOT` point to administrator-installed,
trusted tooling.

The refresh relay variables identify a protected management endpoint and a
token file. The relay, not this repository, owns PS3 state detection. Prowlarr
variables identify the API endpoint, container namespace, key, PS3-capable
indexer/category, and no-category indexer. Never place a real key in `.env`
under version control.
