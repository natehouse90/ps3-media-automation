# Ingest design

`ps3_ingest.py` is a fail-closed validator and builder. It reads a source tree,
identifies the PS3 title, locates a trusted IRD, and builds a complete audit.

The classifier distinguishes exact retail matches, unique hash/size-proven
filename repairs, missing files, extras, modified files, and ambiguous cases.
It never uses name similarity as proof. A separate work tree is created below
`PS3_WORK_ROOT`; the original source is never edited. The final image is first
written under a temporary name, inspected, hashed, and then atomically renamed
into `PS3_ISO_ROOT`.

The pinned builder and IRD parser are installation inputs, not repository
content. Keep IRDs private or redistribute them only when their provenance and
license permit it. The reference builder is `ps3iso-utils` release `277db7de`
from `https://github.com/bucanero/ps3iso-utils/releases/tag/277db7de`, with
`makeps3iso` SHA-256
`c36fe8e6daf9c3ca3d617f79dc524a595aae4f178e52b314937f2fce5a9f48e4`.
`PS3_UPDATE/PS3UPDAT.PUP` may be omitted only when the validation policy
explicitly allows that optional update file.

Use `ps3-ingest --check SOURCE` for a read-only audit and `ps3-ingest SOURCE`
for a validated build. Any unresolved required file, executable mismatch,
collision, or missing trusted IRD produces a FAILED PREFLIGHT result.
