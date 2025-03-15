from __future__ import annotations

import csv
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import IO, Any, TypeVar

from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from palace.manager.util.iteration_helpers import CountingIterator

TTabularRows = Iterable[Sequence[Any]]
TTabularHeadings = Sequence[str]

TTabularDataProcessorReturn = TypeVar("TTabularDataProcessorReturn")
TTabularDataProcessor = Callable[
    [TTabularRows, TTabularHeadings | None], TTabularDataProcessorReturn
]
TCounterWrapperReturn = tuple[CountingIterator, TTabularDataProcessorReturn]
TCounterWrapper = Callable[
    [TTabularRows, TTabularHeadings | None], TCounterWrapperReturn
]


def row_counter_wrapper(
    func: TTabularDataProcessor,
) -> TCounterWrapper:
    """
    Wraps the 'rows' argument with CountingIterator, calls the original
    function, and returns a tuple containing the CountingIterator
    instance and the original function's return value (which might be None).

    :param func: The function to decorate. Must accept (rows: TTabularRows, headings: TTabularHeadings | None).
    :return: A new function that returns (CountingIterator, original_result).
    :raises TypeError: If the 'rows' argument is not iterable.
    """

    @wraps(func)
    def wrapper(
        rows: TTabularRows, headings: TTabularHeadings | None
    ) -> tuple[CountingIterator, TTabularDataProcessorReturn]:
        if not isinstance(rows, Iterable):
            raise TypeError(
                f"The 'rows' argument for {func.__name__} must be an Iterable."
            )
        counted_rows = CountingIterator(rows)
        original_result: TTabularDataProcessorReturn = func(counted_rows, headings)
        return counted_rows, original_result

    return wrapper


def write_csv(
    file: IO[str],
    rows: TTabularRows,
    headings: TTabularHeadings | None,
    delimiter: str = ",",
) -> None:
    """Write tabular data to a CSV file.

    Writes tabular data to a CSV file, optionally including a header row.

    :param file: The file-like object to write to.
    :param rows: The rows of data to write.
    :param headings: The optional header row.
    :param delimiter: The delimiter to use.

    :raises TypeError: If the 'rows' argument is not iterable.
    """
    writer = csv.writer(file, delimiter=delimiter)
    if headings is not None:
        writer.writerow(headings)
    writer.writerows(rows)
    file.flush()


@dataclass(kw_only=True)
class TabularQueryDefinition:
    """A query for generating a report table."""

    id: str
    title: str
    statement: Select

    def __post_init__(self) -> None:
        self.headings: list[str] = [c.name for c in self.statement.c]
        if not self.headings:
            raise ValueError(f"No columns in '{self.title}' query (id='{self.id}').")

    def rows(
        self, *, session: Session, **query_params
    ) -> Generator[tuple[str | int | float | bool, ...]]:
        """Generate a report for the given library."""
        for row in session.execute(self.statement.params(**query_params)):
            yield tuple(row)
