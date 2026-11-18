"""dual-rail: add XNO beside USDC/x402, never instead of it.

    from dual_rail import dual_rail          # the WSGI adapter (dual_rail.adapter.dual_rail)

The modules live in this package: dual_rail.adapter, dual_rail.verify, dual_rail.cli, ...
The command line is `dual-rail` (installed) or `python3 -m dual_rail.cli` (from a checkout).
"""

__version__ = "1.2.0"

from dual_rail.adapter import dual_rail  # noqa: E402,F401

__all__ = ["dual_rail", "__version__"]
