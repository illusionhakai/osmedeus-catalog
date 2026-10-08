# Source provenance

The `bugbounty_catalog` package and original catalog regression tests were extracted
from the user's Trafalgar checkout, commit `93ab977ecd3503b425cb417b13685bc9d33e2a34` (Trafalgar 0.3.0).
Original authorship belongs to the Trafalgar authors. No license file or license
declaration was present in that checkout; no new license is asserted over that code.
Review redistribution rights before publishing it.

The dashboard is built from j3ssie/osmedeus, pinned in `upstream.json`.
Osmedeus is copyright its respective contributors, under the MIT license.
The dashboard build retains the upstream LICENSE with its artifacts.

Companion changes remove the BBOT action dependency, reject structurally incomplete
provider snapshots (including individual Arkadiyt platform files), and add independent scheduling,
authentication, export, and UI integration. No Trafalgar installation is required.
