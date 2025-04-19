from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

import sqlalchemy as sa
from sqlalchemy import bindparam, case, func, lateral, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select

from palace.manager.report.report import ReportDefinition
from palace.manager.sqlalchemy.model.classification import Genre
from palace.manager.sqlalchemy.model.collection import Collection
from palace.manager.sqlalchemy.model.datasource import DataSource
from palace.manager.sqlalchemy.model.edition import Edition
from palace.manager.sqlalchemy.model.identifier import Equivalency, Identifier
from palace.manager.sqlalchemy.model.integration import (
    IntegrationConfiguration,
    IntegrationLibraryConfiguration,
)
from palace.manager.sqlalchemy.model.library import Library
from palace.manager.sqlalchemy.model.licensing import LicensePool
from palace.manager.sqlalchemy.model.work import Work, WorkGenre
from src.palace.manager.core.reporting.util import TTabularHeadings, TTabularRows
from src.palace.manager.util.iteration_helpers import CountingIterator


def library_all_titles_query() -> Select:
    """Select the results to report on."""

    work_genre_alias = aliased(WorkGenre)
    genre_alias = aliased(Genre)
    id_isbn = aliased(Identifier)
    equivalency_alias = aliased(Equivalency)

    isbn = lateral(
        select(id_isbn.identifier)
        .join(equivalency_alias, equivalency_alias.output_id == id_isbn.id)
        .where(
            equivalency_alias.input_id == Identifier.id,
            id_isbn.type == Identifier.ISBN,
            id_isbn.identifier.is_not(None),
            equivalency_alias.strength > 0.5,
            equivalency_alias.enabled == True,
        )
        .order_by(equivalency_alias.strength.desc())
        .limit(1)
    )

    wg_subquery = (
        select(
            work_genre_alias.work_id,
            func.string_agg(genre_alias.name, ", ").label("genres"),
        )
        .join(genre_alias, genre_alias.id == work_genre_alias.genre_id)
        .group_by(work_genre_alias.work_id)
        .subquery()
    )

    return (
        select(
            Edition.title,
            Edition.author,
            Identifier.type.label("identifier_type"),
            Identifier.identifier.label("identifier"),
            func.coalesce(
                case(
                    (Identifier.type == Identifier.ISBN, Identifier.identifier),
                    else_=sa.cast(isbn.c.identifier, sa.String),
                ),
                "",
            ).label("isbn"),
            Edition.language,
            func.coalesce(Edition.publisher, "").label("publisher"),
            Edition.medium.label("format"),
            func.coalesce(Work.audience, "").label("audience"),
            func.coalesce(wg_subquery.c.genres, "").label("genres"),
            DataSource.name.label("data_source"),
            IntegrationConfiguration.name.label("collection"),
        )
        .join(LicensePool, LicensePool.presentation_edition_id == Edition.id)
        .join(Work, LicensePool.work_id == Work.id)
        .outerjoin(wg_subquery, Work.id == wg_subquery.c.work_id)
        .join(Identifier, LicensePool.identifier_id == Identifier.id)
        .outerjoin(isbn, Identifier.type != Identifier.ISBN)
        .join(DataSource, LicensePool.data_source_id == DataSource.id)
        .join(Collection, LicensePool.collection_id == Collection.id)
        .join(
            IntegrationConfiguration,
            Collection.integration_configuration_id == IntegrationConfiguration.id,
        )
        .join(
            IntegrationLibraryConfiguration,
            Collection.integration_configuration_id
            == IntegrationLibraryConfiguration.parent_id,
        )
        .join(Library, IntegrationLibraryConfiguration.library_id == Library.id)
        .where(
            Library.id == bindparam("library_id"),
            IntegrationConfiguration.id.in_(
                bindparam("integration_ids", expanding=True)
            ),
        )
        .order_by(
            Edition.sort_title,
            Edition.author,
            DataSource.name,
            IntegrationConfiguration.name,
        )
    )


class LibraryAllTitleReport:
    """A report of all titles in the library."""

    DEFINITION = ReportDefinition(
        id="all-titles",
        title="All Titles",
        statement=library_all_titles_query(),
    )

    def __init__(
        self,
        library: Library,
        collection_ids: Sequence[int] | None = None,
    ) -> None:
        self.library = library
        self.collection_ids = (
            set(collection_ids) if collection_ids is not None else None
        )

    @staticmethod
    def eligible_collections(
        library: Library,
        *,
        collection_ids: Sequence[int] | None = None,
    ) -> list[Collection]:
        """Return the integrations that are eligible for this report.

        :param library: The library to check.
        :param collection_ids: IDs requested for inclusion. If provided, all
            requested collections must be among the given library's *associated*
            collections to considered eligible. Otherwise (i.e., not provided),
            all *active* collections for the library are considered eligible.
        :return: The list of collections that are eligible for this report.

        :raises ValueError: If any of the requested collections are not among
            the library's associated collections.
        """

        # If no collections are specified, all active collections for this library are eligible.
        if collection_ids is None:
            return list(library.active_collections)

        eligible_collections = set(library.associated_collections)
        requested_collections = set(collection_ids)
        # If collections are specified, only associated collections are eligible.
        if collection_ids not in eligible_collections:
            ineligible_collections = eligible_collections.difference(collection_ids)
            raise ValueError(
                f"Collections {', '.join(ineligible_collections)} are not eligible for library '{library.name}' reports."
            )

        # Otherwise, return the integration configurations for the requested collections.
        return [
            c for c in library.associated_collections if c.id in requested_collections
        ]

    def generate(
        self, transformer: Callable[[TTabularRows, TTabularHeadings | None], Any]
    ) -> CountingIterator:
        """Generate the report."""
        # Get the integration IDs for the collections.
        integration_ids = [
            c.integration_configuration_id
            for c in self.eligible_collections(
                self.library, collection_ids=self.collection_ids
            )
        ]

        # Generate the report.
        transformer(
            self.DEFINITION.rows(
                session=Session.object_session(self.library),
                library_id=self.library.id,
                integration_ids=integration_ids,
            ),
            self.DEFINITION.headings,
        )


def all_titles_report(
    library: Library,
    integration_ids: list[int],
    transformer: Callable[[TTabularRows, TTabularHeadings | None], Any],
) -> None:
    """Generate a report of all titles in the library."""
    report_definition = ReportDefinition(
        id="all-titles",
        title="All Titles",
        statement=library_all_titles_query(),
    )
    headings = report_definition.headings
    rows = partial(
        report_definition.rows,
        library_id=library.id,
        integration_ids=integration_ids,
    )
    transformer(rows(), headings)
