# Added in python 3.12
try:
    from typing import (
        override,  # pyright: ignore[reportAssignmentType]
    )

except ImportError:
    from overrides import override

__all__ = ["override"]
