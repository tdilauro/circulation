from __future__ import annotations

import datetime
import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from jinja2 import Template
from requests import Response

from palace.manager.api.circulation import LoanInfo
from palace.manager.api.odl.api import OPDS2WithODLApi
from palace.manager.api.odl.importer import OPDS2WithODLImporter
from palace.manager.core.coverage import CoverageFailure
from palace.manager.sqlalchemy.model.collection import Collection
from palace.manager.sqlalchemy.model.edition import Edition
from palace.manager.sqlalchemy.model.library import Library
from palace.manager.sqlalchemy.model.licensing import (
    DeliveryMechanism,
    License,
    LicensePool,
    LicensePoolDeliveryMechanism,
)
from palace.manager.sqlalchemy.model.patron import Loan, Patron
from palace.manager.sqlalchemy.model.work import Work
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.files import FilesFixture, OPDS2WithODLFilesFixture
from tests.mocks.mock import MockRequestsResponse
from tests.mocks.odl import MockOPDS2WithODLApi


class OPDS2WithODLApiFixture:
    def __init__(
        self,
        db: DatabaseTransactionFixture,
        files: FilesFixture,
    ):
        self.db = db
        self.files = files

        self.library = db.default_library()
        self.collection = self.create_collection(self.library)
        self.work = self.create_work(self.collection)
        self.license = self.setup_license()
        self.api = MockOPDS2WithODLApi(self.db.session, self.collection)
        self.patron = db.patron()
        self.pool = self.license.license_pool

    def create_work(self, collection: Collection) -> Work:
        return self.db.work(with_license_pool=True, collection=collection)

    def create_collection(self, library: Library) -> Collection:
        collection, _ = Collection.by_name_and_protocol(
            self.db.session,
            f"Test {OPDS2WithODLApi.__name__} Collection",
            OPDS2WithODLApi.label(),
        )
        collection.integration_configuration.settings_dict = {
            "username": "a",
            "password": "b",
            "external_account_id": "http://odl",
            Collection.DATA_SOURCE_NAME_SETTING: "Feedbooks",
        }
        collection.libraries.append(library)
        return collection

    def setup_license(
        self,
        work: Work | None = None,
        available: int = 1,
        concurrency: int = 1,
        left: int | None = None,
        expires: datetime.datetime | None = None,
    ) -> License:
        work = work or self.work
        pool = work.license_pools[0]

        if len(pool.licenses) == 0:
            self.db.license(pool)

        license_ = pool.licenses[0]
        license_.checkout_url = "https://loan.feedbooks.net/loan/get/{?id,checkout_id,expires,patron_id,notification_url,hint,hint_url}"
        license_.checkouts_available = available
        license_.terms_concurrency = concurrency
        license_.expires = expires
        license_.checkouts_left = left
        pool.update_availability_from_licenses()
        return license_

    def checkin(
        self, patron: Patron | None = None, pool: LicensePool | None = None
    ) -> None:
        patron = patron or self.patron
        pool = pool or self.pool
        self._checkin(self.api, patron=patron, pool=pool)

    @staticmethod
    def _checkin(api: MockOPDS2WithODLApi, patron: Patron, pool: LicensePool) -> None:
        """Create a function that, when evaluated, performs a checkin."""

        lsd = json.dumps(
            {
                "status": "ready",
                "links": [
                    {
                        "rel": "return",
                        "href": "http://return",
                    }
                ],
            }
        )
        returned_lsd = json.dumps(
            {
                "status": "returned",
            }
        )

        api.queue_response(200, content=lsd)
        api.queue_response(200)
        api.queue_response(200, content=returned_lsd)
        api.checkin(patron, "pin", pool)

    def checkout(
        self,
        loan_url: str | None = None,
        patron: Patron | None = None,
        pool: LicensePool | None = None,
    ) -> tuple[LoanInfo, Any]:
        patron = patron or self.patron
        pool = pool or self.pool
        loan_url = loan_url or self.db.fresh_url()
        return self._checkout(
            self.api, patron=patron, pool=pool, db=self.db, loan_url=loan_url
        )

    @staticmethod
    def _checkout(
        api: MockOPDS2WithODLApi,
        patron: Patron,
        pool: LicensePool,
        db: DatabaseTransactionFixture,
        loan_url: str,
    ) -> tuple[LoanInfo, Any]:
        """Create a function that, when evaluated, performs a checkout."""

        lsd = json.dumps(
            {
                "status": "ready",
                "potential_rights": {"end": "3017-10-21T11:12:13Z"},
                "links": [
                    {
                        "rel": "self",
                        "href": loan_url,
                    }
                ],
            }
        )
        api.queue_response(200, content=lsd)
        loan = api.checkout(patron, "pin", pool, MagicMock())
        loan_db = (
            db.session.query(Loan)
            .filter(Loan.license_pool == pool, Loan.patron == patron)
            .one()
        )
        return loan, loan_db


@pytest.fixture(scope="function")
def opds2_with_odl_api_fixture(
    db: DatabaseTransactionFixture,
    opds2_with_odl_files_fixture: OPDS2WithODLFilesFixture,
) -> OPDS2WithODLApiFixture:
    return OPDS2WithODLApiFixture(db, opds2_with_odl_files_fixture)


class LicenseHelper:
    """Represents an ODL license."""

    def __init__(
        self,
        identifier: str | None = "",
        checkouts: int | None = None,
        concurrency: int | None = None,
        expires: datetime.datetime | str | None = None,
    ) -> None:
        """Initialize a new instance of LicenseHelper class.

        :param identifier: License's identifier
        :param checkouts: Total number of checkouts before a license expires
        :param concurrency: Number of concurrent checkouts allowed
        :param expires: Date & time when a license expires
        """
        self.identifier = identifier if identifier else f"urn:uuid:{uuid.uuid1()}"
        self.checkouts = checkouts
        self.concurrency = concurrency
        self.expires = (
            expires.isoformat() if isinstance(expires, datetime.datetime) else expires
        )


class LicenseInfoHelper:
    """Represents information about the current state of a license stored in the License Info Document."""

    def __init__(
        self,
        license: LicenseHelper,
        available: int,
        status: str = "available",
        left: int | None = None,
    ) -> None:
        """Initialize a new instance of LicenseInfoHelper class."""
        self.license: LicenseHelper = license
        self.status: str = status
        self.left: int | None = left
        self.available: int = available

    def __str__(self) -> str:
        """Return a JSON representation of the License Info Document."""
        return self.json

    @property
    def json(self) -> str:
        """Return a JSON representation of the License Info Document."""
        return json.dumps(self.dict)

    @property
    def dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the License Info Document."""
        output: dict[str, Any] = {
            "identifier": self.license.identifier,
            "status": self.status,
            "terms": {
                "concurrency": self.license.concurrency,
            },
            "checkouts": {
                "available": self.available,
            },
        }
        if self.license.expires is not None:
            output["terms"]["expires"] = self.license.expires
        if self.left is not None:
            output["checkouts"]["left"] = self.left
        return output


class OPDS2WithODLImporterFixture:
    def __init__(
        self,
        db: DatabaseTransactionFixture,
        api_fixture: OPDS2WithODLApiFixture,
    ):
        self.db = db
        self.api_fixture = api_fixture
        self.files = api_fixture.files

        self.responses: list[bytes] = []

        self.collection = api_fixture.collection

        self.importer = OPDS2WithODLImporter(
            db.session,
            collection=self.collection,
            http_get=self.get_response,
        )

    def get_response(self, *args: Any, **kwargs: Any) -> Response:
        return MockRequestsResponse(200, content=self.responses.pop(0))

    def queue_response(self, item: LicenseInfoHelper | str | bytes) -> None:
        if isinstance(item, LicenseInfoHelper):
            self.responses.append(str(item).encode("utf-8"))
        elif isinstance(item, str):
            self.responses.append(item.encode("utf-8"))
        elif isinstance(item, bytes):
            self.responses.append(item)

    def queue_fixture_file(self, filename: str) -> None:
        self.responses.append(self.files.sample_data(filename))

    def import_fixture_file(
        self,
        filename: str = "feed_template.json.jinja",
        licenses: list[LicenseInfoHelper] | None = None,
    ) -> tuple[
        list[Edition],
        list[LicensePool],
        list[Work],
        dict[str, list[CoverageFailure]],
    ]:
        feed = self.files.sample_text(filename)

        if licenses is not None:
            feed_licenses = [l.license for l in licenses]
            for _license in licenses:
                self.queue_response(_license)
            feed = Template(feed).render(licenses=feed_licenses)

        return self.importer.import_from_feed(feed)

    @staticmethod
    def get_delivery_mechanism_by_drm_scheme_and_content_type(
        delivery_mechanisms: list[LicensePoolDeliveryMechanism],
        content_type: str,
        drm_scheme: str | None,
    ) -> DeliveryMechanism | None:
        """Find a license pool in the list by its identifier.

        :param delivery_mechanisms: List of delivery mechanisms
        :param content_type: Content type
        :param drm_scheme: DRM scheme

        :return: Delivery mechanism with the specified DRM scheme and content type (if any)
        """
        for delivery_mechanism in delivery_mechanisms:
            mechanism = delivery_mechanism.delivery_mechanism

            if (
                mechanism.drm_scheme == drm_scheme
                and mechanism.content_type == content_type
            ):
                return mechanism

        return None


@pytest.fixture
def opds2_with_odl_importer_fixture(
    db: DatabaseTransactionFixture,
    opds2_with_odl_api_fixture: OPDS2WithODLApiFixture,
) -> OPDS2WithODLImporterFixture:
    return OPDS2WithODLImporterFixture(db, opds2_with_odl_api_fixture)
