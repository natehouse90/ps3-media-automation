# Architecture

```text
download staging -> importer -> PS3 preflight -> isolated work tree
                                      |             |
                                      |             v
                                      +--------> verified ISO -> PS3ISO
                                                               |
                                                               v
                                                            ps3netsrv -> NET0/webMAN
```

The library is a publication boundary: downloads and reconstruction work are
writable, while the ps3netsrv-facing library can remain read-only. Search is a
separate, read-only Prowlarr workflow. Refresh is delegated to a management
relay so the PS3 can be checked for active gameplay before XMB reload.
