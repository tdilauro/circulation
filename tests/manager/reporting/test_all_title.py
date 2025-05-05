from unittest.mock import patch

import pytest

from palace.manager.reporting.all_title import LibraryAllTitleReport
from tests.fixtures.database import DatabaseTransactionFixture


class TestAllTitleReport:

    def test_included_collections(self, db: DatabaseTransactionFixture):
        library = db.default_library()
        active_collection = db.default_collection()
        inactive_collection = db.default_inactive_collection()

        # If no collection ids are specified, the library's active collections are included.
        assert LibraryAllTitleReport.included_collections(library) == [
            active_collection
        ]

        report = LibraryAllTitleReport(library)
        assert report.included_collections(library) == [active_collection]

        with patch.object(
            LibraryAllTitleReport.DEFINITION, "rows"
        ) as mock_definition_rows:
            _ = report.rows
            mock_definition_rows.assert_called_once_with(
                session=report.session,
                library_id=library.id,
                integration_ids=[active_collection.integration_configuration.id],
            )

        # Inactive collections can be included by explicitly specifying them.
        test_collection_ids = [inactive_collection.id]

        assert LibraryAllTitleReport.included_collections(
            library, collection_ids=test_collection_ids
        ) == [inactive_collection]

        report = LibraryAllTitleReport(library, collection_ids=test_collection_ids)
        assert report.included_collections(
            library, collection_ids=test_collection_ids
        ) == [inactive_collection]

        with patch.object(
            LibraryAllTitleReport.DEFINITION, "rows"
        ) as mock_definition_rows:
            _ = report.rows
            mock_definition_rows.assert_called_once_with(
                session=report.session,
                library_id=library.id,
                integration_ids=[inactive_collection.integration_configuration.id],
            )

        # Of course, an active collection may also be specified explicitly.
        test_collection_ids = [inactive_collection.id, active_collection.id]

        assert set(
            LibraryAllTitleReport.included_collections(
                library, collection_ids=test_collection_ids
            )
        ) == {active_collection, inactive_collection}

        report = LibraryAllTitleReport(library, collection_ids=test_collection_ids)
        assert set(
            report.included_collections(library, collection_ids=test_collection_ids)
        ) == {active_collection, inactive_collection}

        with patch.object(
            LibraryAllTitleReport.DEFINITION, "rows"
        ) as mock_definition_rows:
            _ = report.rows
            mock_definition_rows.assert_called_once()
            _, call_kwargs = mock_definition_rows.call_args
            assert call_kwargs.get("library_id") == library.id
            assert set(call_kwargs.get("integration_ids")) == {
                active_collection.integration_configuration.id,
                inactive_collection.integration_configuration.id,
            }

        # If called with a collection not associated with the library, we get a ValueError.
        # Conjure an id that shouldn't exist.
        invalid_collection_id = active_collection.id + inactive_collection.id
        test_collection_ids = [invalid_collection_id]

        with pytest.raises(
            ValueError,
            match=rf"Ineligible collection\(s\) for library '{library.name}' reports:",
        ):
            LibraryAllTitleReport.included_collections(
                library, collection_ids=test_collection_ids
            )

        with pytest.raises(
            ValueError,
            match=rf"Ineligible collection\(s\) for library '{library.name}' reports:",
        ):
            LibraryAllTitleReport(library, collection_ids=test_collection_ids)


# class TestAllTitleReport:
#
#     def test_included_collections(self, db: DatabaseTransactionFixture):
#         library = db.default_library()
#         active_collection = db.default_collection()
#         active_integration_id = active_collection.integration_configuration_id
#         inactive_collection = db.default_inactive_collection()
#         inactive_integration_id = inactive_collection.integration_configuration_id
#
#         # --- Scenario 1: No collections specified (defaults to active) ---
#         report_default = LibraryAllTitleReport(library)
#         # Spy on the DEFINITION.rows method
#         with patch.object(LibraryAllTitleReport.DEFINITION, "rows") as mock_rows_default:
#             # Access the rows property to trigger the call
#             _ = report_default.rows
#
#             # Verify included_collections returns only the active one
#             assert report_default.included_collections(library) == [active_collection]
#
#             # Verify DEFINITION.rows was called with the active integration ID
#             mock_rows_default.assert_called_once()
#             call_args, call_kwargs = mock_rows_default.call_args
#             assert call_kwargs.get("library_id") == library.id
#             assert call_kwargs.get("integration_ids") == [active_integration_id]
#
#
#         # --- Scenario 2: Inactive collection explicitly specified ---
#         report_inactive = LibraryAllTitleReport(library, collection_ids=[inactive_collection.id])
#         # Spy on the DEFINITION.rows method again for this instance
#         with patch.object(LibraryAllTitleReport.DEFINITION, "rows") as mock_rows_inactive:
#             # Access the rows property
#             _ = report_inactive.rows
#
#             # Verify included_collections returns the inactive one when requested
#             assert report_inactive.included_collections(library, collection_ids=[inactive_collection.id]) == [inactive_collection]
#
#             # Verify DEFINITION.rows was called with the inactive integration ID
#             mock_rows_inactive.assert_called_once()
#             call_args_inactive, call_kwargs_inactive = mock_rows_inactive.call_args
#             assert call_kwargs_inactive.get("library_id") == library.id
#             assert call_kwargs_inactive.get("integration_ids") == [inactive_integration_id]
#
#         # --- Scenario 3: Requesting an ineligible collection (Optional, based on included_collections logic) ---
#         # This part depends on how included_collections handles errors,
#         # but if it raises an error before rows is called, you might test that separately.
#         # If it filters silently, you might test that rows is called with an empty list.
