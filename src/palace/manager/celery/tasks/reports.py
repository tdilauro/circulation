from __future__ import annotations

import functools
import tempfile
import uuid
import zipfile
from abc import ABC
from collections.abc import MutableMapping
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import IO, Any, ClassVar, TypedDict, TypeVar

from celery import shared_task
from sqlalchemy.orm import Session, sessionmaker
from typing_extensions import Unpack

from palace.manager.celery.job import Job
from palace.manager.celery.task import Task
from palace.manager.reporting.all_title import LibraryAllTitleReport, ReportTable
from palace.manager.reporting.util import (
    TTabularDataProcessor,
    row_counter_wrapper,
    write_csv,
)
from palace.manager.service.celery.celery import QueueNames
from palace.manager.service.email.email import SendEmailCallable
from palace.manager.service.storage.s3 import S3Service
from palace.manager.sqlalchemy.model.integration import (
    IntegrationConfiguration,
)
from palace.manager.sqlalchemy.model.library import Library
from palace.manager.sqlalchemy.util import get_one
from palace.manager.util.log import ExtraDataLoggerAdapter, LoggerAdapterType
from palace.manager.util.uuid import uuid_encode


class LibraryReportsJobTaskKwargs(TypedDict, total=False):
    """Keyword arguments for the library report generator task."""

    request_id: str
    email_address: str
    library_id: int


class RequestIdLoggerAdapter(ExtraDataLoggerAdapter):
    """Add request ID to logging, when present."""

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        report_id = None if self.extra is None else self.extra.get("id")
        new_msg = f"{msg}{f' (request ID: {report_id})' if report_id else ''}"
        return new_msg, kwargs


T = TypeVar("T", bound="LibraryReportJob")


# TODO: Maybe can use a protocol here to enforce class var requirements?
class LibraryReportJob(Job, ABC):
    TIMESTAMP_FORMAT_FOR_FILENAMES = "%Y-%m-%dT%H-%M-%S"
    TIMESTAMP_FORMAT_FOR_EMAILS = "%Y-%m-%dT%H:%M:%S"
    JOB_KEY: ClassVar[str]  # All subclasses must define this.
    JOB_TITLE: ClassVar[str]  # All subclasses must define this.
    # REPORTS_DEFINITIONS: ClassVar[
    #     list[ReportDefinition]
    # ]  # All subclasses must define this.

    @classmethod
    def from_task(
        cls: type[T], task: Task, **kwargs: Unpack[LibraryReportsJobTaskKwargs]
    ) -> T:
        return cls(
            session_maker=task.session_maker,
            send_email=task.services.email.send_email,
            s3_service=task.services.storage.public(),
            **kwargs,
        )

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session],
        send_email: SendEmailCallable,
        s3_service: S3Service,
        request_id: str,
        library_id: int,
        email_address: str,
    ) -> None:
        super().__init__(session_maker)
        self.send_email = send_email
        self.s3_service = s3_service
        self.request_id = request_id
        self.library_id = library_id
        self.email_address = email_address

    @property
    @functools.cache
    def log(self) -> LoggerAdapterType:
        """Return a specialized logger for this class."""
        return RequestIdLoggerAdapter(self.logger(), {"id": self.request_id})

    @property
    def job_key(self) -> str:
        return self.JOB_KEY

    @property
    def job_title(self) -> str:
        return self.JOB_TITLE

    @classmethod
    def eligible_integrations(cls, library: Library) -> list[IntegrationConfiguration]:
        """Return the IntegrationConfigurations for the given Library."""
        # TODO: This is the default behavior for subclasses that dont override
        #  it. Might want to use a mechanism like `__init_subclass__ to enforce
        #  at least calling `super()` in the subclass's method.
        return [c.integration_configuration for c in library.active_collections]

    @classmethod
    def timestamp_filename_string(cls, timestamp: datetime) -> str:
        return timestamp.strftime(cls.TIMESTAMP_FORMAT_FOR_FILENAMES)

    @classmethod
    def timestamp_email_string(cls, timestamp: datetime) -> str:
        return timestamp.strftime(cls.TIMESTAMP_FORMAT_FOR_EMAILS)

    def job_file_name(
        self, *, library: Library, timestamp: datetime, report_id: str | None = None
    ) -> str:
        if report_id is None:
            report_id = self.job_key
        date_str = self.timestamp_filename_string(timestamp)
        return f"palace-{report_id}-{library.short_name}-{date_str}"

    def library_for_report(self, _db: Session) -> Library | None:
        library = get_one(_db, Library, id=self.library_id)
        if not library:
            self.log.error(
                f"Unable to generate report '{self.job_key}' for library (id={self.library_id}): "
                "library not found."
            )
            return None
        return library

    def send_download_notification(
        self, *, download_url: str, library: Library, timestamp: datetime
    ) -> None:
        self.send_email(
            subject=f"Palace {self.job_title} {self.timestamp_email_string(timestamp)}",
            receivers=[self.email_address],
            text=(
                f"Download report here -> {download_url} \n\n"
                f"This report will be available to download for 30 days."
            ),
        )
        self.log.info(
            f'Emailed link for "{self.job_title}" for {library.name} ({library.short_name}) to {self.email_address}.'
        )


class GenerateTitleLevelReportJob(LibraryReportJob):
    JOB_KEY = "title-level-report"
    JOB_TITLE = "Title-Level Report"

    def store_to_s3(self, *, file: IO[bytes], subdir: str, file_name: str) -> str:
        # Push it to S3.
        uid = uuid_encode(uuid.uuid4())
        key = f"{S3Service.DOWNLOADS_PREFIX}/reports/{subdir}/{file_name}-{uid}.zip"

        # This returns a URL. Is it the same one that is returned by generate_url?
        self.s3_service.store_stream(
            key,
            file,
            content_type="application/zip",
        )
        return self.s3_service.generate_url(key)

    @staticmethod
    def _table_processor_for_file(file: IO[str]) -> TTabularDataProcessor[None]:
        return partial(write_csv, file, delimiter=",")

    def zip_results(
        self,
        *,
        archive: zipfile.ZipFile,
        member_name: str,
        table: ReportTable,
    ) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as temp_file:
            # Generate the report.
            processor = row_counter_wrapper(self._table_processor_for_file(temp_file))
            counted_rows, _ = table(processor)
            self.log.debug(
                f"Wrote {counted_rows.get_count()} rows to file {temp_file.name}."
            )

            # Put it in the Zip file.
            archive.write(
                filename=temp_file.name,
                arcname=member_name,
            )
            self.log.debug(
                f"Report file added to Zip archive '{archive.filename}' as '{member_name}'."
            )

    def _run_report(self, *, library: Library, session: Session | None = None) -> None:
        """Run the report for the given library."""

        # Get a session, if we weren't given one.
        if not session:
            session = Session.object_session(library)

        # We want the time of the actual run, since the content of the
        # report may change over time.
        timestamp = datetime.now()

        job_filename = self.job_file_name(library=library, timestamp=timestamp)
        report_filename_for_id = partial(
            self.job_file_name, library=library, timestamp=timestamp
        )

        self.log.info(
            f"Starting report '{self.job_key}' job for {library.name} ({library.short_name})."
        )

        tables = [LibraryAllTitleReport(library)]

        with (tempfile.NamedTemporaryFile() as temp_zip_file,):
            zip_path = Path(temp_zip_file.name)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as archive:
                for table in tables:
                    self.zip_results(
                        archive=archive,
                        member_name=f"{report_filename_for_id(report_id=table.definition.id)}.csv",
                        table=table,
                    )

            self.log.debug(f"Zip file written to '{zip_path}'.")

            # This step must be done after the Zip `archive` has been closed,
            # but before it's temporary file has been deleted. `archive` is
            # automatically closed when its context manager exits, so this step
            # should happen outside of that context manager or after `archive.close()`
            # has been called. The temporary file will be deleted when its context
            # manager exits, so this step must be performed within that context manager.
            # Store the Zip to S3.
            s3_url = self.store_to_s3(
                file=temp_zip_file, subdir=library.short_name, file_name=job_filename
            )

        # Notify the requestor.
        self.send_download_notification(
            download_url=s3_url, library=library, timestamp=timestamp
        )

    def run(self) -> None:
        """Run the main report task in job's transaction."""
        with self.transaction() as session:
            if not (library := self.library_for_report(session)):
                self.log.error(
                    f"No library found for id {self.library_id} report {self.job_key}."
                )
                return
            self._run_report(library=library, session=session)


JOB_KEY_MAPPING: dict[str, type[LibraryReportJob]] = {
    job.JOB_KEY: job
    for job in [
        GenerateTitleLevelReportJob,
    ]
}


@shared_task(queue=QueueNames.high, bind=True, name="generate-report")
def generate_report_task(
    task: Task, *, key: str, **kwargs: Unpack[LibraryReportsJobTaskKwargs]
) -> None:
    job = JOB_KEY_MAPPING[key]
    job.from_task(task, **kwargs).run()
