# Installation

Install on a Linux host with Python 3.10+, trusted `makeps3iso`, and a trusted
IRD cache. Clone the repository, copy `examples/.env.example` to a private
environment file, set paths, and install the scripts with `sudo install` as
shown in the README.

For a service, provide the environment through a protected systemd
`EnvironmentFile` or equivalent secret manager. Keep the token file mode 0600.
Create the work, state, audit, incoming, and ISO directories explicitly. Give
only the download/import worker write access to incoming and keep the published
library read-only for ps3netsrv.
