from typing import Any

# Added in python 3.12
try:
    from typing import (
        override,  # pyright: ignore[reportAssignmentType]
    )

except ImportError:
    from typing import Callable

    def override(fn: Callable[..., Any]):  # pyright: ignore[reportExplicitAny]
        return fn


__all__ = ["override"]
