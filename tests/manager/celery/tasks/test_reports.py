import unittest
from abc import ABC
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session, sessionmaker

from palace.manager.celery.task import Task
from palace.manager.celery.tasks.reports import (
    LibraryReportJob,
    RequestIdLoggerAdapter,
    generate_report_task,
    write_csv,
)
from palace.manager.reporting.util import TTabularHeadings, TTabularRows
from palace.manager.service.email.email import SendEmailCallable
from palace.manager.service.storage.s3 import S3Service
from palace.manager.sqlalchemy.model.library import Library
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
                mock_job.send_download_notification(
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
