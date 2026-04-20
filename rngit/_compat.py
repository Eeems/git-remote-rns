# pyright: reportUnnecessaryTypeIgnoreComment=none
import sys

if sys.version_info < (3, 12):
    from typing_extensions import override  # pyright: ignore[reportUnreachable]

else:
    from typing import override  # pyright: ignore[reportUnreachable]

__all__ = ["override"]
