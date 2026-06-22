# Third-Party Notices

Foundry PDF Proc is licensed under **AGPL-3.0** (see `LICENSE`). The AGPL
obligation derives primarily from PyMuPDF/MuPDF below.

| Component | Role | License |
|---|---|---|
| **PyMuPDF / MuPDF** | core PDF engine | **AGPL-3.0** (Artifex; commercial licensing available) |
| **pdf2docx** | PDF → Word conversion | MIT |
| **pyHanko** | PDF e-signatures | MIT |
| **FastAPI** | web framework | MIT |
| **uvicorn** | ASGI server | BSD-3-Clause |
| **LibreOffice** | Office↔PDF conversion (invoked as a separate process, not linked) | MPL-2.0 / LGPL-3.0 |
| **Ghostscript** | PDF/A conversion (separate process, if used) | AGPL-3.0 |

Full dependency versions are pinned in `requirements.txt`. Each component's
license and copyright are retained by its authors. If an attribution is missing
or incorrect, please open an issue.
