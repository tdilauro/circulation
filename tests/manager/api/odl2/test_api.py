from __future__ import annotations

import datetime
import json
import urllib
import uuid
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import dateutil
import pytest

from palace.manager.api.circulation import HoldInfo, LoanInfo
from palace.manager.api.circulation_exceptions import (
    AlreadyCheckedOut,
    AlreadyOnHold,
    CannotFulfill,
    CannotLoan,
    CurrentlyAvailable,
    HoldsNotPermitted,
    NoAvailableCopies,
    NoLicenses,
    NotCheckedOut,
    NotOnHold,
    PatronHoldLimitReached,
    PatronLoanLimitReached,
)
from palace.manager.api.odl2.constants import FEEDBOOKS_AUDIO
from palace.manager.sqlalchemy.constants import MediaTypes
from palace.manager.sqlalchemy.model.collection import Collection
from palace.manager.sqlalchemy.model.licensing import (
    DeliveryMechanism,
    LicensePool,
    LicensePoolDeliveryMechanism,
)
from palace.manager.sqlalchemy.model.patron import Hold, Loan
from palace.manager.sqlalchemy.model.resource import Representation
from palace.manager.sqlalchemy.model.work import Work
from palace.manager.sqlalchemy.util import create
from palace.manager.util.datetime_helpers import datetime_utc, utc_now
from palace.manager.util.http import BadResponseException
from tests.fixtures.database import DatabaseTransactionFixture
from tests.fixtures.odl2 import ODL2APIFixture


class TestODL2API:
    def test_loan_limit(self, odl2_api_fixture: ODL2APIFixture):
        """Test the loan limit collection setting"""
        # Set the loan limit
        odl2_api_fixture.api.loan_limit = 1

        response = odl2_api_fixture.checkout(
            patron=odl2_api_fixture.patron,
            pool=odl2_api_fixture.work.active_license_pool(),
        )
        # Did the loan take place correctly?
        assert (
            response[0].identifier
            == odl2_api_fixture.work.presentation_edition.primary_identifier.identifier
        )

        # Second loan for the patron should fail due to the loan limit
        work2: Work = odl2_api_fixture.create_work(odl2_api_fixture.collection)
        with pytest.raises(PatronLoanLimitReached) as exc:
            odl2_api_fixture.checkout(
                patron=odl2_api_fixture.patron, pool=work2.active_license_pool()
            )
        assert exc.value.limit == 1

    def test_hold_limit(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ):
        """Test the hold limit collection setting"""
        patron1 = db.patron()

        # First checkout with patron1, then place a hold with the test patron
        pool = odl2_api_fixture.work.active_license_pool()
        assert pool is not None
        response = odl2_api_fixture.checkout(patron=patron1, pool=pool)
        assert (
            response[0].identifier
            == odl2_api_fixture.work.presentation_edition.primary_identifier.identifier
        )

        # Set the hold limit to zero (holds disallowed) and ensure hold fails.
        odl2_api_fixture.api.hold_limit = 0
        with pytest.raises(HoldsNotPermitted) as exc:
            odl2_api_fixture.api.place_hold(odl2_api_fixture.patron, "pin", pool, "")
        assert exc.value.problem_detail.title is not None
        assert exc.value.problem_detail.detail is not None
        assert "Holds not permitted" in exc.value.problem_detail.title
        assert "Holds are not permitted" in exc.value.problem_detail.detail

        # Set the hold limit to 1.
        odl2_api_fixture.api.hold_limit = 1

        hold_response = odl2_api_fixture.api.place_hold(
            odl2_api_fixture.patron, "pin", pool, ""
        )
        # Hold was successful
        assert hold_response.hold_position == 1
        create(
            db.session, Hold, patron_id=odl2_api_fixture.patron.id, license_pool=pool
        )

        # Second work should fail for the test patron due to the hold limit
        work2: Work = odl2_api_fixture.create_work(odl2_api_fixture.collection)
        # Generate a license
        odl2_api_fixture.setup_license(work2)

        # Do the same, patron1 checkout and test patron hold
        pool = work2.active_license_pool()
        assert pool is not None
        response = odl2_api_fixture.checkout(patron=patron1, pool=pool)
        assert (
            response[0].identifier
            == work2.presentation_edition.primary_identifier.identifier
        )

        # Hold should fail
        with pytest.raises(PatronHoldLimitReached) as exc2:
            odl2_api_fixture.api.place_hold(odl2_api_fixture.patron, "pin", pool, "")
        assert exc2.value.limit == 1

        # Set the hold limit to None (unlimited) and ensure hold succeeds.
        odl2_api_fixture.api.hold_limit = None
        hold_response = odl2_api_fixture.api.place_hold(
            odl2_api_fixture.patron, "pin", pool, ""
        )
        assert hold_response.hold_position == 1
        create(
            db.session, Hold, patron_id=odl2_api_fixture.patron.id, license_pool=pool
        )
        # Verify that there are now two holds that  our test patron has both of them.
        assert 2 == db.session.query(Hold).count()
        assert (
            2
            == db.session.query(Hold)
            .filter(Hold.patron_id == odl2_api_fixture.patron.id)
            .count()
        )

    def test_get_license_status_document_success(
        self, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # With a new loan. New loan returns a 201 status.
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        odl2_api_fixture.api.queue_response(
            201, content=json.dumps(dict(status="ready"))
        )
        odl2_api_fixture.api.get_license_status_document(loan)
        requested_url = odl2_api_fixture.api.requests[0][0]

        parsed = urlparse(requested_url)
        assert "https" == parsed.scheme
        assert "loan.feedbooks.net" == parsed.netloc
        params = parse_qs(parsed.query)

        assert odl2_api_fixture.api.settings.passphrase_hint == params["hint"][0]
        assert (
            odl2_api_fixture.api.settings.passphrase_hint_url == params["hint_url"][0]
        )

        assert odl2_api_fixture.license.identifier == params["id"][0]

        # The checkout id and patron id are random UUIDs.
        checkout_id = params["checkout_id"][0]
        assert uuid.UUID(checkout_id)
        patron_id = params["patron_id"][0]
        assert uuid.UUID(patron_id)

        # Loans expire in 21 days by default.
        now = utc_now()
        after_expiration = now + datetime.timedelta(days=23)
        expires = urllib.parse.unquote(params["expires"][0])

        # The expiration time passed to the server is associated with
        # the UTC time zone.
        assert expires.endswith("+00:00")
        expires_t = dateutil.parser.parse(expires)
        assert expires_t.tzinfo == dateutil.tz.tz.tzutc()

        # It's a time in the future, but not _too far_ in the future.
        assert expires_t > now
        assert expires_t < after_expiration

        notification_url = urllib.parse.unquote_plus(params["notification_url"][0])
        assert (
            "http://odl_notify?library_short_name=%s&loan_id=%s"
            % (odl2_api_fixture.library.short_name, loan.id)
            == notification_url
        )

        # With an existing loan. Existing loan returns a 200 status.
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = odl2_api_fixture.db.fresh_str()

        odl2_api_fixture.api.queue_response(
            200, content=json.dumps(dict(status="active"))
        )
        odl2_api_fixture.api.get_license_status_document(loan)
        requested_url = odl2_api_fixture.api.requests[1][0]
        assert loan.external_identifier == requested_url

    def test_get_license_status_document_errors(
        self, odl2_api_fixture: ODL2APIFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)

        odl2_api_fixture.api.queue_response(200, content="not json")
        pytest.raises(
            BadResponseException,
            odl2_api_fixture.api.get_license_status_document,
            loan,
        )

        odl2_api_fixture.api.queue_response(
            200, content=json.dumps(dict(status="unknown"))
        )
        pytest.raises(
            BadResponseException,
            odl2_api_fixture.api.get_license_status_document,
            loan,
        )

        odl2_api_fixture.api.queue_response(403, content="just junk " * 100)
        pytest.raises(
            BadResponseException,
            odl2_api_fixture.api.get_license_status_document,
            loan,
        )
        assert "returned status code 403. Expected 2XX." in caplog.text
        assert "just junk ..." in caplog.text

    def test_checkin_success(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # A patron has a copy of this book checked out.
        odl2_api_fixture.setup_license(concurrency=7, available=6)

        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = "http://loan/" + db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        # The patron returns the book successfully.
        odl2_api_fixture.checkin()
        assert 3 == len(odl2_api_fixture.api.requests)
        assert "http://loan" in odl2_api_fixture.api.requests[0][0]
        assert "http://return" == odl2_api_fixture.api.requests[1][0]
        assert "http://loan" in odl2_api_fixture.api.requests[2][0]

        # The pool's availability has increased, and the local loan has
        # been deleted.
        assert 7 == odl2_api_fixture.pool.licenses_available
        assert 0 == db.session.query(Loan).count()

        # The license on the pool has also been updated
        assert 7 == odl2_api_fixture.license.checkouts_available

    def test_checkin_success_with_holds_queue(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # A patron has the only copy of this book checked out.
        odl2_api_fixture.setup_license(concurrency=1, available=0)
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = "http://loan/" + db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        # Another patron has the book on hold.
        patron_with_hold = db.patron()
        odl2_api_fixture.pool.patrons_in_hold_queue = 1
        hold, ignore = odl2_api_fixture.pool.on_hold_to(
            patron_with_hold, start=utc_now(), end=None, position=1
        )

        # The first patron returns the book successfully.
        odl2_api_fixture.checkin()
        assert 3 == len(odl2_api_fixture.api.requests)
        assert "http://loan" in odl2_api_fixture.api.requests[0][0]
        assert "http://return" == odl2_api_fixture.api.requests[1][0]
        assert "http://loan" in odl2_api_fixture.api.requests[2][0]

        # Now the license is reserved for the next patron.
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 1 == odl2_api_fixture.pool.licenses_reserved
        assert 1 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == db.session.query(Loan).count()
        assert 0 == hold.position

    def test_checkin_already_fulfilled(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # The loan is already fulfilled.
        odl2_api_fixture.setup_license(concurrency=7, available=6)
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        lsd = json.dumps(
            {
                "status": "active",
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        # Checking in the book silently does nothing.
        odl2_api_fixture.api.checkin(
            odl2_api_fixture.patron, "pinn", odl2_api_fixture.pool
        )
        assert 1 == len(odl2_api_fixture.api.requests)
        assert 6 == odl2_api_fixture.pool.licenses_available
        assert 1 == db.session.query(Loan).count()

    def test_checkin_not_checked_out(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # Not checked out locally.
        pytest.raises(
            NotCheckedOut,
            odl2_api_fixture.api.checkin,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
        )

        # Not checked out according to the distributor.
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        lsd = json.dumps(
            {
                "status": "revoked",
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        pytest.raises(
            NotCheckedOut,
            odl2_api_fixture.api.checkin,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
        )

    def test_checkin_cannot_return(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # Not fulfilled yet, but no return link from the distributor.
        loan, ignore = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = db.fresh_str()
        loan.end = utc_now() + datetime.timedelta(days=3)

        lsd = json.dumps(
            {
                "status": "ready",
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        # Checking in silently does nothing.
        odl2_api_fixture.api.checkin(
            odl2_api_fixture.patron, "pin", odl2_api_fixture.pool
        )

        # If the return link doesn't change the status, it still
        # silently ignores the problem.
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

        odl2_api_fixture.api.queue_response(200, content=lsd)
        odl2_api_fixture.api.queue_response(200, content="Deleted")
        odl2_api_fixture.api.queue_response(200, content=lsd)
        odl2_api_fixture.api.checkin(
            odl2_api_fixture.patron, "pin", odl2_api_fixture.pool
        )

    def test_checkin_open_access(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # Checking in an open-access book doesn't need to call out to the distributor API.
        oa_work = db.work(
            with_open_access_download=True, collection=odl2_api_fixture.collection
        )
        pool = oa_work.license_pools[0]
        loan, ignore = pool.loan_to(odl2_api_fixture.patron)

        # make sure that _checkin isn't called since it is not needed for an open access work
        odl2_api_fixture.api._checkin = MagicMock(
            side_effect=Exception("Should not be called")
        )

        odl2_api_fixture.api.checkin(odl2_api_fixture.patron, "pin", pool)

    def test_checkout_success(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # This book is available to check out.
        odl2_api_fixture.setup_license(concurrency=6, available=6, left=30)

        # A patron checks out the book successfully.
        loan_url = db.fresh_str()
        loan, _ = odl2_api_fixture.checkout(loan_url=loan_url)

        assert odl2_api_fixture.collection == loan.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == loan.data_source_name
        assert odl2_api_fixture.pool.identifier.type == loan.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == loan.identifier
        assert loan.start_date is not None
        assert loan.start_date > utc_now() - datetime.timedelta(minutes=1)
        assert loan.start_date < utc_now() + datetime.timedelta(minutes=1)
        assert datetime_utc(3017, 10, 21, 11, 12, 13) == loan.end_date
        assert loan_url == loan.external_identifier
        assert 1 == db.session.query(Loan).count()

        # Now the patron has a loan in the database that matches the LoanInfo
        # returned by the API.
        db_loan = db.session.query(Loan).one()
        assert odl2_api_fixture.pool == db_loan.license_pool
        assert odl2_api_fixture.license == db_loan.license
        assert loan.start_date == db_loan.start
        assert loan.end_date == db_loan.end

        # The pool's availability and the license's remaining checkouts have decreased.
        assert 5 == odl2_api_fixture.pool.licenses_available
        assert 29 == odl2_api_fixture.license.checkouts_left

    def test_checkout_open_access(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # This book is available to check out.
        oa_work = db.work(
            with_open_access_download=True, collection=odl2_api_fixture.collection
        )
        loan = odl2_api_fixture.api.checkout(
            odl2_api_fixture.patron, "pin", oa_work.license_pools[0], MagicMock()
        )

        assert loan.collection(db.session) == odl2_api_fixture.collection
        assert loan.identifier == oa_work.license_pools[0].identifier.identifier
        assert loan.identifier_type == oa_work.license_pools[0].identifier.type
        assert loan.start_date is None
        assert loan.end_date is None
        assert loan.external_identifier is None

    def test_checkout_success_with_hold(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # A patron has this book on hold, and the book just became available to check out.
        odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron,
            start=utc_now() - datetime.timedelta(days=1),
            position=0,
        )
        odl2_api_fixture.setup_license(concurrency=1, available=1, left=5)

        assert odl2_api_fixture.pool.licenses_available == 0
        assert odl2_api_fixture.pool.licenses_reserved == 1
        assert odl2_api_fixture.pool.patrons_in_hold_queue == 1

        # The patron checks out the book.
        loan_url = db.fresh_str()
        loan, _ = odl2_api_fixture.checkout(loan_url=loan_url)

        # The patron gets a loan successfully.
        assert odl2_api_fixture.collection == loan.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == loan.data_source_name
        assert odl2_api_fixture.pool.identifier.type == loan.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == loan.identifier
        assert loan.start_date is not None
        assert loan.start_date > utc_now() - datetime.timedelta(minutes=1)
        assert loan.start_date < utc_now() + datetime.timedelta(minutes=1)
        assert datetime_utc(3017, 10, 21, 11, 12, 13) == loan.end_date
        assert loan_url == loan.external_identifier
        assert 1 == db.session.query(Loan).count()

        db_loan = db.session.query(Loan).one()
        assert odl2_api_fixture.pool == db_loan.license_pool
        assert odl2_api_fixture.license == db_loan.license
        assert 4 == odl2_api_fixture.license.checkouts_left

        # The book is no longer reserved for the patron, and the hold has been deleted.
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == db.session.query(Hold).count()

    def test_checkout_already_checked_out(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        odl2_api_fixture.setup_license(concurrency=2, available=1)

        # Checkout succeeds the first time
        odl2_api_fixture.checkout()

        # But raises an exception the second time
        pytest.raises(AlreadyCheckedOut, odl2_api_fixture.checkout)

        assert 1 == db.session.query(Loan).count()

    def test_checkout_expired_hold(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # The patron was at the beginning of the hold queue, but the hold already expired.
        yesterday = utc_now() - datetime.timedelta(days=1)
        hold, _ = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron, start=yesterday, end=yesterday, position=0
        )
        other_hold, _ = odl2_api_fixture.pool.on_hold_to(db.patron(), start=utc_now())
        odl2_api_fixture.setup_license(concurrency=2, available=1)

        pytest.raises(
            NoAvailableCopies,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

    def test_checkout_no_available_copies(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # A different patron has the only copy checked out.
        odl2_api_fixture.setup_license(concurrency=1, available=0)
        existing_loan, _ = odl2_api_fixture.license.loan_to(db.patron())

        pytest.raises(
            NoAvailableCopies,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 1 == db.session.query(Loan).count()

        db.session.delete(existing_loan)

        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        last_week = now - datetime.timedelta(weeks=1)

        # A different patron has the only copy reserved.
        other_patron_hold, _ = odl2_api_fixture.pool.on_hold_to(
            db.patron(), position=0, start=last_week
        )
        odl2_api_fixture.pool.update_availability_from_licenses()

        pytest.raises(
            NoAvailableCopies,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

        # The patron has a hold, but another patron is ahead in the holds queue.
        hold, _ = odl2_api_fixture.pool.on_hold_to(
            db.patron(), position=1, start=yesterday
        )
        odl2_api_fixture.pool.update_availability_from_licenses()

        pytest.raises(
            NoAvailableCopies,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

        # The patron has the first hold, but it's expired.
        hold.start = last_week - datetime.timedelta(days=1)
        hold.end = yesterday
        odl2_api_fixture.pool.update_availability_from_licenses()

        pytest.raises(
            NoAvailableCopies,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

    def test_checkout_no_licenses(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        odl2_api_fixture.setup_license(concurrency=1, available=1, left=0)

        pytest.raises(
            NoLicenses,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

    def test_checkout_when_all_licenses_expired(
        self, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # license expired by expiration date
        odl2_api_fixture.setup_license(
            concurrency=1,
            available=2,
            left=1,
            expires=utc_now() - datetime.timedelta(weeks=1),
        )

        pytest.raises(
            NoLicenses,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        # license expired by no remaining checkouts
        odl2_api_fixture.setup_license(
            concurrency=1,
            available=2,
            left=0,
            expires=utc_now() + datetime.timedelta(weeks=1),
        )

        pytest.raises(
            NoLicenses,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

    def test_checkout_cannot_loan(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        lsd = json.dumps(
            {
                "status": "revoked",
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        pytest.raises(
            CannotLoan,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

        # No external identifier.
        lsd = json.dumps(
            {
                "status": "ready",
                "potential_rights": {"end": "2017-10-21T11:12:13Z"},
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        pytest.raises(
            CannotLoan,
            odl2_api_fixture.api.checkout,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        assert 0 == db.session.query(Loan).count()

    @pytest.mark.parametrize(
        "delivery_mechanism, correct_type, correct_link, links",
        [
            (
                DeliveryMechanism.ADOBE_DRM,
                DeliveryMechanism.ADOBE_DRM,
                "http://acsm",
                [
                    {
                        "rel": "license",
                        "href": "http://acsm",
                        "type": DeliveryMechanism.ADOBE_DRM,
                    }
                ],
            ),
            (
                MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
                MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
                "http://manifest",
                [
                    {
                        "rel": "manifest",
                        "href": "http://manifest",
                        "type": MediaTypes.AUDIOBOOK_MANIFEST_MEDIA_TYPE,
                    }
                ],
            ),
            (
                DeliveryMechanism.FEEDBOOKS_AUDIOBOOK_DRM,
                FEEDBOOKS_AUDIO,
                "http://correct",
                [
                    {
                        "rel": "license",
                        "href": "http://acsm",
                        "type": DeliveryMechanism.ADOBE_DRM,
                    },
                    {
                        "rel": "manifest",
                        "href": "http://correct",
                        "type": FEEDBOOKS_AUDIO,
                    },
                ],
            ),
        ],
    )
    def test_fulfill_success(
        self,
        odl2_api_fixture: ODL2APIFixture,
        db: DatabaseTransactionFixture,
        delivery_mechanism: str,
        correct_type: str,
        correct_link: str,
        links: dict[str, Any],
    ) -> None:
        # Fulfill a loan in a way that gives access to a license file.
        odl2_api_fixture.setup_license(concurrency=1, available=1)
        odl2_api_fixture.checkout()

        lpdm = MagicMock(spec=LicensePoolDeliveryMechanism)
        lpdm.delivery_mechanism = MagicMock(spec=DeliveryMechanism)
        lpdm.delivery_mechanism.content_type = "ignored/format"
        lpdm.delivery_mechanism.drm_scheme = delivery_mechanism

        lsd = json.dumps(
            {
                "status": "ready",
                "potential_rights": {"end": "2017-10-21T11:12:13Z"},
                "links": links,
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        fulfillment = odl2_api_fixture.api.fulfill(
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            lpdm,
        )

        assert odl2_api_fixture.collection == fulfillment.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == fulfillment.data_source_name
        assert odl2_api_fixture.pool.identifier.type == fulfillment.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == fulfillment.identifier
        assert datetime_utc(2017, 10, 21, 11, 12, 13) == fulfillment.content_expires
        assert correct_link == fulfillment.content_link
        assert correct_type == fulfillment.content_type

    def test_fulfill_open_access(
        self,
        odl2_api_fixture: ODL2APIFixture,
        db: DatabaseTransactionFixture,
    ) -> None:
        oa_work = db.work(
            with_open_access_download=True, collection=odl2_api_fixture.collection
        )
        pool = oa_work.license_pools[0]
        loan, ignore = pool.loan_to(odl2_api_fixture.patron)

        # If we can't find a delivery mechanism, we can't fulfill the loan.
        pytest.raises(
            CannotFulfill,
            odl2_api_fixture.api.fulfill,
            odl2_api_fixture.patron,
            "pin",
            pool,
            MagicMock(spec=LicensePoolDeliveryMechanism),
        )

        lpdm = pool.delivery_mechanisms[0]
        fulfillment = odl2_api_fixture.api.fulfill(
            odl2_api_fixture.patron, "pin", pool, lpdm
        )

        assert odl2_api_fixture.collection == fulfillment.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == fulfillment.data_source_name
        assert fulfillment.identifier_type == pool.identifier.type
        assert fulfillment.identifier == pool.identifier.identifier
        assert fulfillment.content_expires is None
        assert fulfillment.content_link == pool.open_access_download_url
        assert fulfillment.content_type == lpdm.delivery_mechanism.content_type

    def test_fulfill_cannot_fulfill(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        odl2_api_fixture.setup_license(concurrency=7, available=7)
        odl2_api_fixture.checkout()

        assert 1 == db.session.query(Loan).count()
        assert 6 == odl2_api_fixture.pool.licenses_available

        lsd = json.dumps(
            {
                "status": "revoked",
            }
        )

        odl2_api_fixture.api.queue_response(200, content=lsd)
        pytest.raises(
            CannotFulfill,
            odl2_api_fixture.api.fulfill,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            Representation.EPUB_MEDIA_TYPE,
        )

        # The pool's availability has been updated and the local
        # loan has been deleted, since we found out the loan is
        # no longer active.
        assert 7 == odl2_api_fixture.pool.licenses_available
        assert 0 == db.session.query(Loan).count()

    def _holdinfo_from_hold(self, hold: Hold) -> HoldInfo:
        pool: LicensePool = hold.license_pool
        return HoldInfo(
            pool.collection,
            pool.data_source.name,
            pool.identifier.type,
            pool.identifier.identifier,
            hold.start,
            hold.end,
            hold.position,
        )

    def test_count_holds_before(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        tomorrow = now + datetime.timedelta(days=1)
        last_week = now - datetime.timedelta(weeks=1)

        hold, ignore = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron, start=now
        )

        info = self._holdinfo_from_hold(hold)
        assert 0 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

        # A previous hold.
        odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        assert 1 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

        # Expired holds don't count.
        odl2_api_fixture.pool.on_hold_to(
            db.patron(), start=last_week, end=yesterday, position=0
        )
        assert 1 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

        # Later holds don't count.
        odl2_api_fixture.pool.on_hold_to(db.patron(), start=tomorrow)
        assert 1 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

        # Holds on another pool don't count.
        other_pool = db.licensepool(None)
        other_pool.on_hold_to(odl2_api_fixture.patron, start=yesterday)
        assert 1 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

        for i in range(3):
            odl2_api_fixture.pool.on_hold_to(
                db.patron(), start=yesterday, end=tomorrow, position=1
            )
        assert 4 == odl2_api_fixture.api._count_holds_before(info, hold.license_pool)

    def test_update_hold_end_date(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        now = utc_now()
        tomorrow = now + datetime.timedelta(days=1)
        yesterday = now - datetime.timedelta(days=1)
        next_week = now + datetime.timedelta(days=7)
        last_week = now - datetime.timedelta(days=7)

        odl2_api_fixture.pool.licenses_owned = 1
        odl2_api_fixture.pool.licenses_reserved = 1

        hold, ignore = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron, start=now, position=0
        )
        info = self._holdinfo_from_hold(hold)
        library = hold.patron.library

        # Set the reservation period and loan period.
        config = odl2_api_fixture.collection.integration_configuration.for_library(
            library.id
        )
        assert config is not None
        DatabaseTransactionFixture.set_settings(
            config,
            **{
                Collection.DEFAULT_RESERVATION_PERIOD_KEY: 3,
                Collection.EBOOK_LOAN_DURATION_KEY: 6,
            },
        )
        odl2_api_fixture.db.session.commit()

        # A hold that's already reserved and has an end date doesn't change.
        info.end_date = tomorrow
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date
        info.end_date = yesterday
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert yesterday == info.end_date

        # Updating a hold that's reserved but doesn't have an end date starts the
        # reservation period.
        info.end_date = None
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert info.end_date is not None
        assert info.end_date < next_week  # type: ignore[unreachable]
        assert info.end_date > now

        # Updating a hold that has an end date but just became reserved starts
        # the reservation period.
        info.end_date = yesterday
        info.hold_position = 1
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert info.end_date < next_week
        assert info.end_date > now

        # When there's a holds queue, the end date is the maximum time it could take for
        # a license to become available.

        # One copy, one loan, hold position 1.
        # The hold will be available as soon as the loan expires.
        odl2_api_fixture.pool.licenses_available = 0
        odl2_api_fixture.pool.licenses_reserved = 0
        odl2_api_fixture.pool.licenses_owned = 1
        loan, ignore = odl2_api_fixture.license.loan_to(db.patron(), end=tomorrow)
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date

        # One copy, one loan, hold position 2.
        # The hold will be available after the loan expires + 1 cycle.
        first_hold, ignore = odl2_api_fixture.pool.on_hold_to(
            db.patron(), start=last_week
        )
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow + datetime.timedelta(days=9) == info.end_date

        # Two copies, one loan, one reserved hold, hold position 2.
        # The hold will be available after the loan expires.
        odl2_api_fixture.pool.licenses_reserved = 1
        odl2_api_fixture.pool.licenses_owned = 2
        odl2_api_fixture.license.checkouts_available = 2
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert tomorrow == info.end_date

        # Two copies, one loan, one reserved hold, hold position 3.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires.
        second_hold, ignore = odl2_api_fixture.pool.on_hold_to(
            db.patron(), start=yesterday
        )
        first_hold.end = next_week
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=6) == info.end_date

        # One copy, no loans, one reserved hold, hold position 3.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires + 1 cycle.
        db.session.delete(loan)
        odl2_api_fixture.pool.licenses_owned = 1
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=15) == info.end_date

        # One copy, no loans, one reserved hold, hold position 2.
        # The hold will be available after the reserved hold is checked out
        # at the latest possible time and that loan expires.
        db.session.delete(second_hold)
        odl2_api_fixture.pool.licenses_owned = 1
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=6) == info.end_date

        db.session.delete(first_hold)

        # Ten copies, seven loans, three reserved holds, hold position 9.
        # The hold will be available after the sixth loan expires.
        odl2_api_fixture.pool.licenses_owned = 10
        for i in range(5):
            odl2_api_fixture.pool.loan_to(db.patron(), end=next_week)
        odl2_api_fixture.pool.loan_to(
            db.patron(), end=next_week + datetime.timedelta(days=1)
        )
        odl2_api_fixture.pool.loan_to(
            db.patron(), end=next_week + datetime.timedelta(days=2)
        )
        odl2_api_fixture.pool.licenses_reserved = 3
        for i in range(3):
            odl2_api_fixture.pool.on_hold_to(
                db.patron(),
                start=last_week + datetime.timedelta(days=i),
                end=next_week + datetime.timedelta(days=i),
                position=0,
            )
        for i in range(5):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=1) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 12.
        # The hold will be available after the second reserved hold is checked
        # out and that loan expires.
        for i in range(3):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=7) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 29.
        # The hold will be available after the sixth loan expires + 2 cycles.
        for i in range(17):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=19) == info.end_date

        # Ten copies, seven loans, three reserved holds, hold position 32.
        # The hold will be available after the second reserved hold is checked
        # out and that loan expires + 2 cycles.
        for i in range(3):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_end_date(
            info, hold.license_pool, library=library
        )
        assert next_week + datetime.timedelta(days=25) == info.end_date

    def test_update_hold_position(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        now = utc_now()
        yesterday = now - datetime.timedelta(days=1)
        tomorrow = now + datetime.timedelta(days=1)

        hold, ignore = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron, start=now
        )
        info = self._holdinfo_from_hold(hold)

        odl2_api_fixture.pool.licenses_owned = 1

        # When there are no other holds and no licenses reserved, hold position is 1.
        loan, _ = odl2_api_fixture.license.loan_to(db.patron())
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 1 == info.hold_position

        # When a license is reserved, position is 0.
        db.session.delete(loan)
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # If another hold has the reserved licenses, position is 2.
        odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 2 == info.hold_position

        # If another license is reserved, position goes back to 0.
        odl2_api_fixture.pool.licenses_owned = 2
        odl2_api_fixture.license.checkouts_available = 2
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # If there's an earlier hold but it expired, it doesn't
        # affect the position.
        odl2_api_fixture.pool.on_hold_to(
            db.patron(), start=yesterday, end=yesterday, position=0
        )
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 0 == info.hold_position

        # Hold position is after all earlier non-expired holds...
        for i in range(3):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=yesterday)
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 5 == info.hold_position

        # and before any later holds.
        for i in range(2):
            odl2_api_fixture.pool.on_hold_to(db.patron(), start=tomorrow)
        odl2_api_fixture.api._update_hold_position(info, hold.license_pool)
        assert 5 == info.hold_position

    def test_update_hold_data(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        hold, is_new = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron,
            utc_now(),
            utc_now() + datetime.timedelta(days=100),
            9,
        )
        odl2_api_fixture.api._update_hold_data(hold)
        assert hold.position == 0
        assert hold.end.date() == (hold.start + datetime.timedelta(days=3)).date()

    def test_update_hold_queue(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        licenses = [odl2_api_fixture.license]

        DatabaseTransactionFixture.set_settings(
            odl2_api_fixture.collection.integration_configuration,
            **{Collection.DEFAULT_RESERVATION_PERIOD_KEY: 3},
        )

        # If there's no holds queue when we try to update the queue, it
        # will remove a reserved license and make it available instead.
        odl2_api_fixture.pool.licenses_owned = 1
        odl2_api_fixture.pool.licenses_available = 0
        odl2_api_fixture.pool.licenses_reserved = 1
        odl2_api_fixture.pool.patrons_in_hold_queue = 0
        last_update = utc_now() - datetime.timedelta(minutes=5)
        odl2_api_fixture.work.last_update_time = last_update
        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)
        assert 1 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue
        # The work's last update time is changed so it will be moved up in the crawlable OPDS feed.
        assert odl2_api_fixture.work.last_update_time > last_update

        # If there are holds, a license will get reserved for the next hold
        # and its end date will be set.
        hold, _ = odl2_api_fixture.pool.on_hold_to(
            odl2_api_fixture.patron, start=utc_now(), position=1
        )
        later_hold, _ = odl2_api_fixture.pool.on_hold_to(
            db.patron(), start=utc_now() + datetime.timedelta(days=1), position=2
        )
        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)

        # The pool's licenses were updated.
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 1 == odl2_api_fixture.pool.licenses_reserved
        assert 2 == odl2_api_fixture.pool.patrons_in_hold_queue

        # And the first hold changed.
        assert 0 == hold.position
        assert hold.end - utc_now() - datetime.timedelta(days=3) < datetime.timedelta(
            hours=1
        )

        # The later hold is the same.
        assert 2 == later_hold.position

        # Now there's a reserved hold. If we add another license, it's reserved and,
        # the later hold is also updated.
        l = db.license(
            odl2_api_fixture.pool, terms_concurrency=1, checkouts_available=1
        )
        licenses.append(l)
        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)

        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 2 == odl2_api_fixture.pool.licenses_reserved
        assert 2 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == later_hold.position
        assert later_hold.end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)

        # Now there are no more holds. If we add another license,
        # it ends up being available.
        l = db.license(
            odl2_api_fixture.pool, terms_concurrency=1, checkouts_available=1
        )
        licenses.append(l)
        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)
        assert 1 == odl2_api_fixture.pool.licenses_available
        assert 2 == odl2_api_fixture.pool.licenses_reserved
        assert 2 == odl2_api_fixture.pool.patrons_in_hold_queue

        # License pool is updated when the holds are removed.
        db.session.delete(hold)
        db.session.delete(later_hold)
        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)
        assert 3 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue

        # We can also make multiple licenses reserved at once.
        loans = []
        holds = []
        for i in range(3):
            p = db.patron()
            loan, _ = odl2_api_fixture.checkout(patron=p)
            loans.append((loan, p))
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue

        l = db.license(
            odl2_api_fixture.pool, terms_concurrency=2, checkouts_available=2
        )
        licenses.append(l)
        for i in range(3):
            hold, ignore = odl2_api_fixture.pool.on_hold_to(
                db.patron(),
                start=utc_now() - datetime.timedelta(days=3 - i),
                position=i + 1,
            )
            holds.append(hold)

        odl2_api_fixture.api.update_licensepool(odl2_api_fixture.pool)
        assert 2 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 3 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == holds[0].position
        assert 0 == holds[1].position
        assert 3 == holds[2].position
        assert holds[0].end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)
        assert holds[1].end - utc_now() - datetime.timedelta(
            days=3
        ) < datetime.timedelta(hours=1)

        # If there are more licenses that change than holds, some of them become available.
        for i in range(2):
            _, p = loans[i]
            odl2_api_fixture.checkin(patron=p)
        assert 3 == odl2_api_fixture.pool.licenses_reserved
        assert 1 == odl2_api_fixture.pool.licenses_available
        assert 3 == odl2_api_fixture.pool.patrons_in_hold_queue
        for hold in holds:
            assert 0 == hold.position
            assert hold.end - utc_now() - datetime.timedelta(
                days=3
            ) < datetime.timedelta(hours=1)

    def test_place_hold_success(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        loan, _ = odl2_api_fixture.checkout(patron=db.patron())

        hold = odl2_api_fixture.api.place_hold(
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            "notifications@librarysimplified.org",
        )

        assert 1 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert odl2_api_fixture.collection == hold.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == hold.data_source_name
        assert odl2_api_fixture.pool.identifier.type == hold.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == hold.identifier
        assert hold.start_date is not None
        assert hold.start_date > utc_now() - datetime.timedelta(minutes=1)
        assert hold.start_date < utc_now() + datetime.timedelta(minutes=1)
        assert loan.end_date == hold.end_date
        assert 1 == hold.hold_position

    def test_place_hold_already_on_hold(self, odl2_api_fixture: ODL2APIFixture) -> None:
        odl2_api_fixture.setup_license(concurrency=1, available=0)
        odl2_api_fixture.pool.on_hold_to(odl2_api_fixture.patron)
        pytest.raises(
            AlreadyOnHold,
            odl2_api_fixture.api.place_hold,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            "notifications@librarysimplified.org",
        )

    def test_place_hold_currently_available(
        self, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        pytest.raises(
            CurrentlyAvailable,
            odl2_api_fixture.api.place_hold,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
            "notifications@librarysimplified.org",
        )

    def test_release_hold_success(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        loan_patron = db.patron()
        odl2_api_fixture.checkout(patron=loan_patron)
        odl2_api_fixture.pool.on_hold_to(odl2_api_fixture.patron, position=1)

        odl2_api_fixture.api.release_hold(
            odl2_api_fixture.patron, "pin", odl2_api_fixture.pool
        )
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == db.session.query(Hold).count()

        odl2_api_fixture.pool.on_hold_to(odl2_api_fixture.patron, position=0)
        odl2_api_fixture.checkin(patron=loan_patron)

        odl2_api_fixture.api.release_hold(
            odl2_api_fixture.patron, "pin", odl2_api_fixture.pool
        )
        assert 1 == odl2_api_fixture.pool.licenses_available
        assert 0 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 0 == db.session.query(Hold).count()

        odl2_api_fixture.pool.on_hold_to(odl2_api_fixture.patron, position=0)
        other_hold, ignore = odl2_api_fixture.pool.on_hold_to(db.patron(), position=2)

        odl2_api_fixture.api.release_hold(
            odl2_api_fixture.patron, "pin", odl2_api_fixture.pool
        )
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 1 == odl2_api_fixture.pool.licenses_reserved
        assert 1 == odl2_api_fixture.pool.patrons_in_hold_queue
        assert 1 == db.session.query(Hold).count()
        assert 0 == other_hold.position

    def test_release_hold_not_on_hold(self, odl2_api_fixture: ODL2APIFixture) -> None:
        pytest.raises(
            NotOnHold,
            odl2_api_fixture.api.release_hold,
            odl2_api_fixture.patron,
            "pin",
            odl2_api_fixture.pool,
        )

    def test_patron_activity_loan(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        # No loans yet.
        assert [] == odl2_api_fixture.api.patron_activity(
            odl2_api_fixture.patron, "pin"
        )

        # One loan.
        _, loan = odl2_api_fixture.checkout()

        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 1 == len(activity)
        assert odl2_api_fixture.collection == activity[0].collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == activity[0].data_source_name
        assert odl2_api_fixture.pool.identifier.type == activity[0].identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == activity[0].identifier
        assert loan.start == activity[0].start_date
        assert loan.end == activity[0].end_date
        assert loan.external_identifier == activity[0].external_identifier

        # Two loans.
        pool2 = db.licensepool(None, collection=odl2_api_fixture.collection)
        license2 = db.license(pool2, terms_concurrency=1, checkouts_available=1)
        _, loan2 = odl2_api_fixture.checkout(pool=pool2)

        def activity_sort_key(activity: LoanInfo | HoldInfo) -> datetime.datetime:
            if activity.start_date is None:
                raise TypeError("start_date is None")
            return activity.start_date

        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 2 == len(activity)
        [l1, l2] = sorted(activity, key=activity_sort_key)

        assert odl2_api_fixture.collection == l1.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == l1.data_source_name
        assert odl2_api_fixture.pool.identifier.type == l1.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == l1.identifier
        assert loan.start == l1.start_date
        assert loan.end == l1.end_date
        assert loan.external_identifier == l1.external_identifier

        assert odl2_api_fixture.collection == l2.collection(db.session)
        assert pool2.data_source.name == l2.data_source_name
        assert pool2.identifier.type == l2.identifier_type
        assert pool2.identifier.identifier == l2.identifier
        assert loan2.start == l2.start_date
        assert loan2.end == l2.end_date
        assert loan2.external_identifier == l2.external_identifier

        # If a loan is expired already, it's left out.
        loan2.end = utc_now() - datetime.timedelta(days=2)
        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 1 == len(activity)
        assert odl2_api_fixture.pool.identifier.identifier == activity[0].identifier
        odl2_api_fixture.checkin(pool=pool2)

        # Open access loans are included.
        oa_work = db.work(
            with_open_access_download=True, collection=odl2_api_fixture.collection
        )
        pool3 = oa_work.license_pools[0]
        loan3, ignore = pool3.loan_to(odl2_api_fixture.patron)

        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 2 == len(activity)
        [l1, l2] = sorted(activity, key=activity_sort_key)

        assert odl2_api_fixture.collection == l1.collection(db.session)
        assert odl2_api_fixture.pool.data_source.name == l1.data_source_name
        assert odl2_api_fixture.pool.identifier.type == l1.identifier_type
        assert odl2_api_fixture.pool.identifier.identifier == l1.identifier
        assert loan.start == l1.start_date
        assert loan.end == l1.end_date
        assert loan.external_identifier == l1.external_identifier

        assert odl2_api_fixture.collection == l2.collection(db.session)
        assert pool3.data_source.name == l2.data_source_name
        assert pool3.identifier.type == l2.identifier_type
        assert pool3.identifier.identifier == l2.identifier
        assert loan3.start == l2.start_date
        assert loan3.end == l2.end_date
        assert loan3.external_identifier == l2.external_identifier

        # remove the open access loan
        db.session.delete(loan3)

        # One hold.
        other_patron = db.patron()
        odl2_api_fixture.checkout(patron=other_patron, pool=pool2)
        hold, _ = pool2.on_hold_to(odl2_api_fixture.patron)
        hold.start = utc_now() - datetime.timedelta(days=2)
        hold.end = hold.start + datetime.timedelta(days=3)
        hold.position = 3
        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 2 == len(activity)
        [h1, l1] = sorted(activity, key=activity_sort_key)

        assert isinstance(h1, HoldInfo)

        assert odl2_api_fixture.collection == h1.collection(db.session)
        assert pool2.data_source.name == h1.data_source_name
        assert pool2.identifier.type == h1.identifier_type
        assert pool2.identifier.identifier == h1.identifier
        assert hold.start == h1.start_date
        assert hold.end == h1.end_date
        # Hold position was updated.
        assert 1 == h1.hold_position
        assert 1 == hold.position

        # If the hold is expired, it's deleted right away and the license
        # is made available again.
        odl2_api_fixture.checkin(patron=other_patron, pool=pool2)
        hold.end = utc_now() - datetime.timedelta(days=1)
        hold.position = 0
        activity = odl2_api_fixture.api.patron_activity(odl2_api_fixture.patron, "pin")
        assert 1 == len(activity)
        assert 0 == db.session.query(Hold).count()
        assert 1 == pool2.licenses_available
        assert 0 == pool2.licenses_reserved

    def test_update_loan_still_active(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        odl2_api_fixture.setup_license(concurrency=6, available=6)
        loan, _ = odl2_api_fixture.license.loan_to(odl2_api_fixture.patron)
        loan.external_identifier = db.fresh_str()
        status_doc = {
            "status": "active",
        }

        odl2_api_fixture.api.update_loan(loan, status_doc)
        # Availability hasn't changed, and the loan still exists.
        assert 6 == odl2_api_fixture.pool.licenses_available
        assert 1 == db.session.query(Loan).count()

    def test_update_loan_removes_loan(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        odl2_api_fixture.setup_license(concurrency=7, available=7)
        _, loan = odl2_api_fixture.checkout()

        assert 6 == odl2_api_fixture.pool.licenses_available
        assert 1 == db.session.query(Loan).count()

        status_doc = {
            "status": "cancelled",
        }

        odl2_api_fixture.api.update_loan(loan, status_doc)

        # Availability has increased, and the loan is gone.
        assert 7 == odl2_api_fixture.pool.licenses_available
        assert 0 == db.session.query(Loan).count()

    def test_update_loan_removes_loan_with_hold_queue(
        self, db: DatabaseTransactionFixture, odl2_api_fixture: ODL2APIFixture
    ) -> None:
        _, loan = odl2_api_fixture.checkout()
        hold, _ = odl2_api_fixture.pool.on_hold_to(db.patron(), position=1)
        odl2_api_fixture.pool.update_availability_from_licenses()

        assert odl2_api_fixture.pool.licenses_owned == 1
        assert odl2_api_fixture.pool.licenses_available == 0
        assert odl2_api_fixture.pool.licenses_reserved == 0
        assert odl2_api_fixture.pool.patrons_in_hold_queue == 1

        status_doc = {
            "status": "cancelled",
        }

        odl2_api_fixture.api.update_loan(loan, status_doc)

        # The license is reserved for the next patron, and the loan is gone.
        assert 0 == odl2_api_fixture.pool.licenses_available
        assert 1 == odl2_api_fixture.pool.licenses_reserved
        assert 0 == hold.position
        assert 0 == db.session.query(Loan).count()
