import csv
from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass
from typing import IO, Any

from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from palace.manager.util.iteration_helpers import CountingIterator

TTabularRows = Iterable[Sequence[Any]]
TTabularHeadings = Sequence[str] | None


def write_csv(
    file: IO[str],
    rows: TTabularRows,
    headings: TTabularHeadings,
    delimiter: str = ",",
) -> None:
    writer = csv.writer(file, delimiter=delimiter)
    if headings is not None:
        writer.writerow(headings)
    rows = CountingIterator(rows)
    writer.writerows(rows)
    file.flush()


@dataclass(kw_only=True)
class ReportDefinition:
    """A query for generating a report."""

    id: str
    title: str
    statement: Select

    def __post_init__(self) -> None:
        self.headings: list[str] = [c.name for c in self.statement.c]

    def rows(
        self, *, session: Session, library_id: int, integration_ids: Sequence[int]
    ) -> Generator[tuple[str | int | float | bool, ...]]:
        """Generate a report for the given library."""
        statement = self.statement.params(
            library_id=library_id, integration_ids=integration_ids
        )
        for row in session.execute(statement):
            yield tuple(row)
