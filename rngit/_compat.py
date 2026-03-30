# pyright: reportUnnecessaryTypeIgnoreComment=none

from typing import (
    Any,
    Callable,
    cast,
)

try:
    # Added in python 3.12
    from typing import (
        override,  # pyright: ignore[reportUnknownVariableType,reportAttributeAccessIssue,reportUnknownType,reportUnnecessaryTypeIgnoreComment]
    )

except ImportError:
    from overrides import (  # pyright: ignore[reportMissingImports,reportUnnecessaryTypeIgnoreComment]
        override,  # pyright: ignore[reportUnknownVariableType,reportUnnecessaryTypeIgnoreComment]
    )

override = cast(Callable[[Callable[..., Any]], Callable[..., Any]], override)  # pyright: ignore[reportExplicitAny]
__all__ = ["override"]
