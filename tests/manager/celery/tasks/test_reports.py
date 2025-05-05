import unittest
import uuid
import zipfile
from abc import ABC
from datetime import datetime
from functools import partial
from io import BytesIO, StringIO
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from sqlalchemy.orm import Session, sessionmaker

from palace.manager.celery.task import Task
from palace.manager.celery.tasks.reports import (
    GenerateTitleLevelReportJob,
    LibraryReportJob,
    RequestIdLoggerAdapter,
    generate_report_task,
)
from palace.manager.reporting.all_title import ReportTable
from palace.manager.reporting.util import (
    TabularQueryDefinition,
    TTabularHeadings,
    TTabularRows,
    write_csv,
)
from palace.manager.service.email.email import SendEmailCallable
from palace.manager.service.storage.s3 import S3Service
from palace.manager.sqlalchemy.model.library import Library
from palace.manager.util.uuid import uuid_encode
from tests.fixtures.database import DatabaseTransactionFixture


class TestFunctions:
    @pytest.mark.parametrize(
        "rows, headings, delimiter, expected_output",
        (
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                ["header1", "header2"],
                ",",
                "header1,header2\r\nrow1,data1\r\nrow2,data2\r\n",
                id="with_headings",
            ),
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                None,
                ",",
                "row1,data1\r\nrow2,data2\r\n",
                id="without_headings",
            ),
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                ["header1", "header2"],
                "|",
                "header1|header2\r\nrow1|data1\r\nrow2|data2\r\n",
                id="different_delimiter",
            ),
            pytest.param(
                [], ["header1", "header2"], ",", "header1,header2\r\n", id="empty_rows"
            ),
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                [],
                ",",
                "\r\nrow1,data1\r\nrow2,data2\r\n",
                id="empty_headings",
            ),
        ),
    )
    def test_write_csv(
        self,
        rows: TTabularRows,
        headings: TTabularHeadings,
        delimiter: str,
        expected_output: str,
    ):
        file = StringIO()
        write_csv(file, rows, headings, delimiter=delimiter)
        assert file.getvalue() == expected_output


class TestGenerateReportTask:

    def test_generate_report_task(self):
        test_key = "test-report-key"
        test_request_id = "test-request-id"
        test_library_id = 1
        test_email_address = "test@example.com"

        mock_job_class = MagicMock()
        mock_job_instance = MagicMock()
        mock_job_class.from_task = MagicMock(return_value=mock_job_instance)
        kwargs = {
            "request_id": test_request_id,
            "library_id": test_library_id,
            "email_address": test_email_address,
        }
        with patch(
            "palace.manager.celery.tasks.reports.JOB_KEY_MAPPING",
            {test_key: mock_job_class},
        ):
            generate_report_task(key=test_key, **kwargs)

        mock_job_class.from_task.assert_called_once_with(unittest.mock.ANY, **kwargs)
        mock_job_instance.run.assert_called_once_with()

    def test_generate_report_task_exception(self):
        test_key = "test-report-key"

        mock_job_class = MagicMock()
        mock_job_instance = MagicMock()
        mock_job_class.from_task = MagicMock(return_value=mock_job_instance)
        mock_job_instance.run.side_effect = Exception("Test Exception")

        kwargs = {
            "request_id": "test_request_id",
            "library_id": 1,
            "email_address": "test@example.com",
        }

        with (
            patch(
                "palace.manager.celery.tasks.reports.JOB_KEY_MAPPING",
                {test_key: mock_job_class},
            ),
            pytest.raises(Exception, match="Test Exception"),
        ):
            generate_report_task(key=test_key, **kwargs)

        mock_job_class.from_task.assert_called_once_with(unittest.mock.ANY, **kwargs)
        mock_job_instance.run.assert_called_once()

    def test_generate_report_task_key_not_found(self):
        kwargs = {
            "request_id": "test_request_id",
            "library_id": 1,
            "email_address": "test@example.com",
        }
        invalid_key = "invalid_key"

        with pytest.raises(KeyError):
            generate_report_task(key=invalid_key, **kwargs)


class TestLibraryReportJob:
    # Define a dummy JOB_KEY and JOB_TITLE for testing purposes
    TEST_JOB_KEY = "test_report"
    TEST_JOB_TITLE = "Test Report"

    @pytest.fixture
    def mock_job(self) -> LibraryReportJob:

        class MockLibraryReportJob(LibraryReportJob, ABC):
            JOB_KEY = self.TEST_JOB_KEY
            JOB_TITLE = self.TEST_JOB_TITLE

            def run(self) -> None: ...

        return MockLibraryReportJob(
            session_maker=MagicMock(spec=sessionmaker),
            send_email=MagicMock(spec=SendEmailCallable),
            s3_service=MagicMock(spec=S3Service),
            request_id="test_request_id",
            library_id=1,
            email_address="test@example.com",
        )

    @pytest.mark.parametrize(
        "timestamp, expected_string",
        [
            (datetime(2024, 1, 1, 12, 0, 0), "2024-01-01T12-00-00"),
            (datetime(2023, 12, 31, 23, 59, 59), "2023-12-31T23-59-59"),
        ],
        ids=["first", "second"],
    )
    def test_timestamp_filename_string(self, timestamp, expected_string):
        actual_string = LibraryReportJob.timestamp_filename_string(timestamp)
        assert actual_string == expected_string

    @pytest.mark.parametrize(
        "timestamp, expected_string",
        [
            (datetime(2024, 1, 1, 12, 0, 0), "2024-01-01T12:00:00"),
            (datetime(2023, 12, 31, 23, 59, 59), "2023-12-31T23:59:59"),
        ],
        ids=["first", "second"],
    )
    def test_timestamp_email_string(self, timestamp, expected_string):
        actual_email_string = LibraryReportJob.timestamp_email_string(timestamp)
        assert actual_email_string == expected_string

    def test_job_file_name(self, mock_job):
        library = MagicMock(spec=Library, short_name="test_library")
        timestamp = datetime(2024, 1, 1, 12, 0, 0)
        file_name = mock_job.job_file_name(library=library, timestamp=timestamp)
        assert (
            file_name == f"palace-{self.TEST_JOB_KEY}-test_library-2024-01-01T12-00-00"
        )

    def test_library_for_report(self, mock_job):
        with patch(
            "palace.manager.celery.tasks.reports.get_one",
            return_value=MagicMock(spec=Library),
        ) as mock_get_one:
            mock_session = MagicMock(spec=Session)
            library = mock_job.library_for_report(mock_session)
            mock_get_one.assert_called_once_with(
                mock_session, Library, id=mock_job.library_id
            )
            assert library == mock_get_one.return_value

    def test_library_for_report_not_found(self, mock_job):
        with patch(
            "palace.manager.celery.tasks.reports.get_one", return_value=None
        ) as mock_get_one:
            with patch.object(mock_job.log, "error") as mock_log_error:
                mock_session = MagicMock(spec=Session)
                library = mock_job.library_for_report(mock_session)
                mock_get_one.assert_called_once_with(
                    mock_session, Library, id=mock_job.library_id
                )
                assert library is None
                mock_log_error.assert_called_once()

    def test_send_download_notification(self, mock_job):
        download_url = "test_download_url"
        library = MagicMock(spec=Library, name="Test Library", short_name="test_lib")
        timestamp = datetime(2024, 1, 1, 12, 0, 0)
        with patch.object(mock_job, "send_email") as mock_send_email:
            with patch.object(mock_job.log, "info") as mock_log_info:
                mock_job.send_notification(
                    download_url=download_url, library=library, timestamp=timestamp
                )
                mock_send_email.assert_called_once()
                mock_log_info.assert_called_once()

    def test_from_task(self, mock_job):
        mock_task = MagicMock(spec=Task)
        mock_task.session_maker = MagicMock(spec=sessionmaker)
        mock_task.services.email.send_email = MagicMock(spec=SendEmailCallable)
        mock_task.services.storage.public.return_value = MagicMock(spec=S3Service)

        request_id = "test_request_id"
        library_id = 1
        email_address = "test@example.com"

        job = mock_job.from_task(
            mock_task,
            request_id=request_id,
            library_id=library_id,
            email_address=email_address,
        )

        assert isinstance(job, LibraryReportJob)
        assert job.session_maker == mock_task.session_maker
        assert job.send_email == mock_task.services.email.send_email
        assert job.s3_service == mock_task.services.storage.public.return_value
        assert job.request_id == request_id
        assert job.library_id == library_id
        assert job.email_address == email_address

    def test_log_property(self, mock_job):
        with patch.object(mock_job, "logger") as mock_logger:
            log = mock_job.log
            assert isinstance(log, RequestIdLoggerAdapter)
            mock_logger.assert_called_once()

    def test_job_key_property(self, mock_job):
        assert mock_job.job_key == self.TEST_JOB_KEY

    def test_job_title_property(self, mock_job):
        assert mock_job.job_title == self.TEST_JOB_TITLE

    def test_only_active_collections_are_included(self, db: DatabaseTransactionFixture):
        library = db.default_library()
        collection1 = db.default_collection()
        collection2 = db.default_inactive_collection()

        # The library has two collections, one of which is inactive.
        assert set(library.associated_collections) == {collection1, collection2}
        assert library.active_collections == [collection1]
        assert collection1.is_active is True
        assert collection2.is_active is False

        eligible_integrations = LibraryReportJob.eligible_integrations(library)

        assert len(eligible_integrations) == 1
        assert eligible_integrations == [collection1.integration_configuration]


@pytest.fixture
def mock_job() -> GenerateTitleLevelReportJob:
    """Provides a base GenerateTitleLevelReportJob instance with mocked dependencies."""
    mock_session_maker = MagicMock(spec=sessionmaker)
    mock_send_email = MagicMock(spec=SendEmailCallable)
    mock_s3_service = MagicMock(spec=S3Service)

    return GenerateTitleLevelReportJob(
        session_maker=mock_session_maker,
        send_email=mock_send_email,
        s3_service=mock_s3_service,
        request_id="test-request-id",
        library_id=1,
        email_address="test@example.com",
    )


class TestMethods:

    @pytest.mark.parametrize(
        "file_content_bytes, name, extension, expected_extension",
        (
            pytest.param(
                b"This is the report content.",
                "test_library/test_report",
                ".zip",
                ".zip",
                id="standard_input",
            ),
            pytest.param(
                b"",
                "another/sub/dir/empty-report-content",
                ".zip",
                ".zip",
                id="empty_content",
            ),
            pytest.param(
                b"Report content",
                "dotted-report_name.v2.1",
                None,
                "",
                id="no_extension",
            ),
            pytest.param(
                b"Report content",
                "dotted-report_name.v2.1",
                "",
                "",
                id="empty_extension",
            ),
        ),
    )
    def test_store_to_s3(
        self,
        mock_job: GenerateTitleLevelReportJob,
        file_content_bytes: bytes,
        name: str,
        extension: str | None,
        expected_extension: str,
    ) -> None:
        """Verify that we interact with the S3 service as expected."""
        test_uuid = uuid.uuid4()
        encoded_uuid = uuid_encode(test_uuid)

        file_stream = BytesIO(file_content_bytes)
        expected_key = f"{S3Service.DOWNLOADS_PREFIX}/reports/{name}-{encoded_uuid}{expected_extension}"
        expected_url = f"https://s3.example.com/{expected_key}"

        # `store_stream` returns the URL for the stored object.
        mock_job.s3_service.store_stream.return_value = expected_url

        # extension is None means don't pass the argument.
        store_function = (
            partial(
                mock_job.store_to_s3, file=file_stream, name=name, extension=extension
            )
            if extension is not None
            else partial(mock_job.store_to_s3, file=file_stream, name=name)
        )

        with patch("uuid.uuid4") as mock_uuid4:
            mock_uuid4.return_value = test_uuid
            result_url = store_function()

        mock_job.s3_service.store_stream.assert_called_once_with(
            expected_key,
            file_stream,
            content_type="application/zip",
        )

        assert result_url == expected_url

    def test_store_to_s3_storage_failure(
        self, mock_job: GenerateTitleLevelReportJob
    ) -> None:
        """An exception during S3 storage is propagated."""
        test_uuid = uuid.uuid4()
        encoded_uuid = uuid_encode(test_uuid)

        file_content = b"Report data"
        file_stream = BytesIO(file_content)
        name = "failed_lib/failed_report"
        extension = ".zip"
        expected_key = (
            f"{S3Service.DOWNLOADS_PREFIX}/reports/{name}-{encoded_uuid}{extension}"
        )

        # `store_stream` raises an exception.
        mock_job.s3_service.store_stream.side_effect = Exception("S3 Upload Error")

        with patch("uuid.uuid4") as mock_uuid4:
            mock_uuid4.return_value = test_uuid
            with pytest.raises(Exception, match="S3 Upload Error"):
                mock_job.store_to_s3(file=file_stream, name=name, extension=extension)

        mock_job.s3_service.store_stream.assert_called_once_with(
            expected_key, file_stream, content_type="application/zip"
        )

    @patch("tempfile.NamedTemporaryFile")
    def test_zip_results(
        self,
        mock_named_temp_file: MagicMock,
        mock_job: GenerateTitleLevelReportJob,
    ):
        # Mock NamedTemporaryFile to return a StringIO object we can inspect
        mock_temp_file_buffer = StringIO()
        mock_temp_file_buffer.name = "/tmp/fake_temp_file.csv"  # Need a name attribute
        mock_named_temp_file.return_value.__enter__.return_value = mock_temp_file_buffer

        mock_table = MagicMock(spec=ReportTable)

        # Mock the definition attribute needed for logging/naming and headings
        simulated_rows = [("r1c1", "r1c2"), ("r2c1", "r2c2")]
        mock_definition = MagicMock(
            spec=TabularQueryDefinition, id="test_table_id", headings=["col1", "col2"]
        )
        type(mock_table).definition = PropertyMock(return_value=mock_definition)

        # Simulate report table's __call__() result.
        def report_table_call(processor):
            # The processor created by zip_results wraps write_csv and counts rows.
            # Calling it executes the wrapped function.
            counted_iterator, write_csv_result = processor(
                simulated_rows, mock_definition.headings
            )
            return counted_iterator, write_csv_result

        mock_table.side_effect = report_table_call

        zip_buffer = BytesIO()
        archive = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED)
        mock_archive_write = MagicMock()
        archive.write = mock_archive_write
        member_name = "report_part_1.csv"

        mock_job.zip_results(archive=archive, member_name=member_name, table=mock_table)
        archive.close()

        mock_named_temp_file.assert_called_once_with("w", encoding="utf-8")

        mock_table.assert_called_once()
        processor_arg = mock_table.call_args[0][0]
        assert callable(processor_arg)

        # The right content was written to the file...
        expected_csv_content = "col1,col2\r\nr1c1,r1c2\r\nr2c1,r2c2\r\n"
        assert mock_temp_file_buffer.getvalue() == expected_csv_content

        # ... and a request was made to write the file's content into the Zip archive.
        mock_archive_write.assert_called_once_with(
            filename=mock_temp_file_buffer.name, arcname=member_name
        )

    @patch("tempfile.NamedTemporaryFile")
    def test_zip_results_table_processing_error(
        self,
        mock_named_temp_file: MagicMock,
        mock_job: GenerateTitleLevelReportJob,
    ):
        """An exception during table processing is propagated."""
        mock_temp_file_buffer = StringIO()
        mock_temp_file_buffer.name = "/tmp/fake_temp_file.csv"
        mock_named_temp_file.return_value.__enter__.return_value = mock_temp_file_buffer

        mock_table = MagicMock(spec=ReportTable)
        type(mock_table).definition = PropertyMock(
            return_value=MagicMock(spec=TabularQueryDefinition, id="test_table_id")
        )
        # Make the report table's callable raise an error.
        mock_table.side_effect = ValueError("Failed to generate table data")

        zip_buffer = BytesIO()
        archive = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED)
        mock_archive_write = MagicMock()
        archive.write = mock_archive_write
        member_name = "report_part_error.csv"

        with pytest.raises(ValueError, match="Failed to generate table data"):
            mock_job.zip_results(
                archive=archive, member_name=member_name, table=mock_table
            )
        archive.close()

        # Ensure nothing was written to the temp file buffer.
        assert mock_temp_file_buffer.getvalue() == ""
        mock_archive_write.assert_not_called()
        mock_named_temp_file.assert_called_once()

    @patch("tempfile.NamedTemporaryFile")
    def test_zip_results_archive_write_error(
        self,
        mock_named_temp_file: MagicMock,
        mock_job: GenerateTitleLevelReportJob,
    ):
        """An exception during archive write is propagated."""
        mock_temp_file_buffer = StringIO()
        mock_temp_file_buffer.name = "/tmp/fake_temp_file.csv"
        mock_named_temp_file.return_value.__enter__.return_value = mock_temp_file_buffer

        mock_table = MagicMock(spec=ReportTable)
        mock_definition = MagicMock(
            spec=TabularQueryDefinition, id="test_table_id", headings=["col1"]
        )
        type(mock_table).definition = PropertyMock(return_value=mock_definition)
        simulated_rows = [("r1c1",), ("r2c1",)]

        def table_side_effect(processor):
            counted_iterator, write_csv_result = processor(
                simulated_rows, mock_definition.headings
            )
            return counted_iterator, write_csv_result

        mock_table.side_effect = table_side_effect

        zip_buffer = BytesIO()
        archive = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED)
        mock_archive_write = MagicMock(side_effect=IOError("Disk full"))
        archive.write = mock_archive_write
        member_name = "report_part_io_error.csv"

        with pytest.raises(IOError, match="Disk full"):
            mock_job.zip_results(
                archive=archive, member_name=member_name, table=mock_table
            )

        expected_csv_content = "col1\r\nr1c1\r\nr2c1\r\n"
        assert mock_temp_file_buffer.getvalue() == expected_csv_content
        mock_archive_write.assert_called_once_with(
            filename=mock_temp_file_buffer.name, arcname=member_name
        )
        archive.close()

        mock_named_temp_file.assert_called_once()

    @pytest.mark.parametrize(
        "download_url, library_name, library_short_name, timestamp, expected_timestamp_str",
        [
            pytest.param(
                "http://example.com/download/report.zip",
                "Test Library",
                "testlib",
                datetime(2024, 7, 15, 10, 30, 0),
                "2024-07-15T10:30:00",
                id="standard_case",
            ),
            pytest.param(
                "https://another-domain.org/report-link",
                "Another Library",
                "another",
                datetime(2023, 1, 1, 0, 0, 1),
                "2023-01-01T00:00:01",
                id="different_values",
            ),
        ],
    )
    def test_send_notification(
        self,
        mock_job: GenerateTitleLevelReportJob,
        download_url: str,
        library_name: str,
        library_short_name: str,
        timestamp: datetime,
        expected_timestamp_str: str,
    ):
        """
        Verify that send_notification calls the email service and logs correctly
        with various inputs.
        """
        library = MagicMock(
            spec=Library, name=library_name, short_name=library_short_name
        )
        expected_subject = f"Palace {mock_job.job_title} {expected_timestamp_str}"
        expected_text = (
            f"Download report here -> {download_url} \n\n"
            f"This report will be available to download for 30 days."
        )
        expected_log_message = (
            f'Emailed link for "{mock_job.job_title}" for {library.name} ({library.short_name}) '
            f"to {mock_job.email_address}."
        )

        # Use patch.object to spy on the logger instance associated with the job
        with patch.object(mock_job.log, "info") as mock_log_info:
            # Call the method under test
            mock_job.send_notification(
                download_url=download_url, library=library, timestamp=timestamp
            )

            # Check that the email sending function was called correctly
            mock_job.send_email.assert_called_once_with(
                subject=expected_subject,
                receivers=[mock_job.email_address],
                text=expected_text,
            )

            # Check that the logging happened as expected
            mock_log_info.assert_called_once_with(expected_log_message)
