import sys
from collections.abc import Iterable
from typing import Generic, TypeVar

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self


I = TypeVar("I")


class CountingIterator(Generic[I]):
    """An iterator that counts the number of rows yielded."""

    def __init__(self, iterable: Iterable[I]) -> None:
        self.iterator = iter(iterable)
        self.count = 0
        self._is_exhausted = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> I:
        try:
            row = next(self.iterator)
        except StopIteration:
            self._is_exhausted = True
            raise
        self.count += 1
        return row

    def get_count(self) -> int:
        return self.count

    @property
    def is_exhausted(self) -> bool:
        """Check if the iterator is exhausted."""
        return self._is_exhausted
