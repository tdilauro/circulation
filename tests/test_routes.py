import contextlib
from nose.tools import (
    assert_raises,
    eq_,
    set_trace,
)
from flask import Response
from werkzeug.exceptions import MethodNotAllowed

from api import app
from api import routes
from api.opds import CirculationManagerAnnotator

from test_controller import ControllerTest

class MockApp(object):
    """Pretends to be a Flask application with a configured
    CirculationManager.
    """
    def __init__(self):
        self.manager = MockManager()


class MockManager(object):
    """Pretends to be a CirculationManager with configured controllers."""

    def __init__(self):
        self._cache = {}

        # This is used by the allows_patron_web annotator.
        self.patron_web_client_url = "http://patron/web"

    def __getattr__(self, controller_name):
        return self._cache.setdefault(
            controller_name, MockController(controller_name)
        )

class MockControllerMethod(object):
    """Pretends to be one of the methods of a controller class."""
    def __init__(self, controller, name):
        """Constructor.

        :param controller: A MockController.
        :param name: The name of this method.
        """
        self.controller = controller
        self.name = name

    def __call__(self, *args, **kwargs):
        """Simulate a successful method call.

        :return: A Response object, as required by Flask, with this
        method smuggled out as the 'method' attribute.
        """
        self.args = args
        self.kwargs = kwargs
        response = Response("I called %s" % repr(self), 200)
        response.method = self
        return response

    def __repr__(self):
        return "<MockControllerMethod %s.%s>" % (
            self.controller.name, self.name
        )

class MockController(MockControllerMethod):
    """Pretends to be a controller.

    A controller has methods, but it may also be called _as_ a method,
    so this class subclasses MockControllerMethod.
    """
    def __init__(self, name):
        """Constructor.

        :param name: The name of the controller.
        """
        self.name = name
        self._cache = {}

    def __getattr__(self, method_name):
        """Locate a method of this controller as a MockControllerMethod."""
        return self._cache.setdefault(
            method_name, MockControllerMethod(self, method_name)
        )

    def __repr__(self):
        return "<MockControllerMethod %s>" % self.name


class RouteTest(ControllerTest):

    def setup(self, _db=None):
        super(RouteTest, self).setup(_db=_db, set_up_circulation_manager=False)
        self.original_app = routes.app
        app = MockApp()
        routes.app = app
        self.manager = app.manager
        self.resolver = self.original_app.url_map.bind('', '/')

        # For convenience, set self.controller to a specific controller
        # whose routes are being tested.
        controller_name = getattr(self, 'CONTROLLER_NAME', None)
        if controller_name:
            self.controller = getattr(self.manager, controller_name)

    def teardown(self):
        routes.app = self.original_app

    def request(self, url, method='GET'):
        """Simulate a request to a URL without triggering any code outside
        routes.py.
        """
        # Map an incoming URL to the name of a function within routes.py
        # and a set of arguments to the function.
        function_name, kwargs = self.resolver.match(url, method)

        # Locate the function itself.
        function = getattr(routes, function_name)

        # Call it in the context of our MockApp which simulates the
        # controller code.
        with self.app.test_request_context():
            return function(**kwargs)

    def assert_request_calls(self, url, method, *args, **kwargs):
        """Make a request to the given `url` and assert that
        the given controller `method` was called with the
        given `args` and `kwargs`.
        """
        http_method = kwargs.pop('http_method', 'GET')
        response = self.request(url, http_method)
        eq_(response.method, method)
        eq_(response.method.args, args)
        eq_(response.method.kwargs, kwargs)

    def assert_supported_methods(self, url, *methods):
        """Verify that the given HTTP `methods` are the only ones supported
        on the given `url`.
        """
        # The simplest way to do this seems to be to try each of the
        # other potential methods and verify that MethodNotAllowed is
        # raised each time.
        check = set(['GET', 'POST', 'PUT', 'DELETE']) - set(methods)
        for method in check:
            assert_raises(MethodNotAllowed, self.request, url, method)


class TestIndex(RouteTest):

    CONTROLLER_NAME = "index_controller"

    def test_index(self):
        for url in '/', '':
            self.assert_request_calls(url, self.controller)

    def test_authentication_document(self):
        self.assert_request_calls(
            "/authentication_document", self.controller.authentication_document
        )

    def test_public_key_document(self):
        self.assert_request_calls(
            "/public_key_document", self.controller.public_key_document
        )

class TestOPDSFeed(RouteTest):

    CONTROLLER_NAME = 'opds_feeds'

    def test_acquisition_groups(self):
        # An incoming lane identifier is passed in to the groups()
        # method.
        method = self.controller.groups
        self.assert_request_calls("/groups", method, None)
        self.assert_request_calls(
            "/groups/<lane_identifier>", method, '<lane_identifier>'
        )

    def test_feed(self):
        # An incoming lane identifier is passed in to the feed()
        # method.
        method = self.controller.feed
        self.assert_request_calls("/feed", method, None)
        self.assert_request_calls(
            "/feed/<lane_identifier>", method, '<lane_identifier>'
        )

    def test_crawlable_library_feed(self):
        self.assert_request_calls(
            "/crawlable", self.controller.crawlable_library_feed
        )

    def test_crawlable_list_feed(self):
        self.assert_request_calls(
            "/lists/<list_name>/crawlable",
            self.controller.crawlable_list_feed, '<list_name>'
        )

    def test_crawlable_collection_feed(self):
        self.assert_request_calls(
            "/collections/<collection_name>/crawlable",
            self.manager.opds_feeds.crawlable_collection_feed, '<collection_name>'
        )

    def test_lane_search(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)


class TestSharedCollection(RouteTest):

    CONTROLLER_NAME = 'shared_collection_controller'

    def test_shared_collection_info(self):
        self.assert_request_calls(
            "/collections/<collection_name>",
            self.controller.info, '<collection_name>'
        )

    def test_shared_collection_register(self):
        url = "/collections/<collection_name>/register"
        self.assert_request_calls(
            url, self.controller.register, '<collection_name>',
            http_method='POST'
        )
        self.assert_supported_methods(url, 'POST')

    def test_shared_collection_borrow_identifier(self):
        url = "/collections/<collection_name>/<identifier_type>/an/identifier/borrow"
        self.assert_request_calls(
            url, self.controller.borrow, '<collection_name>',
            '<identifier_type>', 'an/identifier', None
        )
        self.assert_supported_methods(url, 'GET', 'POST')

    def test_shared_collection_borrow_hold_id(self):
        url = "/collections/<collection_name>/holds/<hold_id>/borrow"
        self.assert_request_calls(
            url, self.controller.borrow, '<collection_name>', None, None,
            '<hold_id>'
        )
        self.assert_supported_methods(url, 'GET', 'POST')

    def test_shared_collection_loan_info(self):
        url = "/collections/<collection_name>/loans/<loan_id>"
        self.assert_request_calls(
            url, self.controller.loan_info, '<collection_name>', '<loan_id>'
        )

    def test_shared_collection_revoke_loan(self):
        url = "/collections/<collection_name>/loans/<loan_id>/revoke"
        self.assert_request_calls(
            url, self.controller.revoke_loan, '<collection_name>', '<loan_id>'
        )

    def test_shared_collection_fulfill_no_mechanism(self):
        url = "/collections/<collection_name>/loans/<loan_id>/fulfill"
        self.assert_request_calls(
            url, self.controller.fulfill, '<collection_name>', '<loan_id>',
            None
        )

    def test_shared_collection_fulfill_with_mechanism(self):
        url = "/collections/<collection_name>/loans/<loan_id>/fulfill/<mechanism_id>"
        self.assert_request_calls(
            url, self.controller.fulfill, '<collection_name>', '<loan_id>',
            '<mechanism_id>'
        )

    def test_shared_collection_hold_info(self):
    	url = "/collections/<collection_name>/holds/<hold_id>"
	self.assert_request_calls(
	    url, self.controller.hold_info, '<collection_name>',
            '<hold_id>'
	)

    def test_shared_collection_revoke_hold(self):
    	url = "/collections/<collection_name>/holds/<hold_id>/revoke"
	self.assert_request_calls(
	    url, self.controller.revoke_hold, '<collection_name>',
            '<hold_id>'
	)

class TestProfileController(RouteTest):

    def test_patron_profile(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

class TestLoansController(RouteTest):

    def test_active_loans(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_borrow(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_fulfill(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_revoke_loan_or_hold(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_loan_or_hold_detail(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)


class TestAnnotationsController(RouteTest):

    def test_annotations(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_annotation_detail(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

    def test_annotations_for_work(self):
    	url = ""
	self.assert_request_calls(
	    url, self.controller.method,
	)

class TestURNLookupController(RouteTest):

    CONTROLLER_NAME = "urn_lookup"

    def test_work(self):
    	url = '/works'
	self.assert_request_calls(
	    url, self.controller.work_lookup
	)


class TestWorkController(RouteTest):

    CONTROLLER_NAME = "work_controller"

    def test_contributor(self):
    	url = '/works/contributor/<contributor_name>'
	self.assert_request_calls(
	    url, self.controller.contributor, "<contributor_name>", None, None
	)

    def test_contributor_language(self):
    	url = '/works/contributor/<contributor_name>/<languages>'
	self.assert_request_calls(
	    url, self.controller.contributor,
            "<contributor_name>", "<languages>", None
	)

    def test_contributor_language_audience(self):
    	url = '/works/contributor/<contributor_name>/<languages>/<audiences>'
	self.assert_request_calls(
	    url, self.controller.contributor,
            "<contributor_name>", "<languages>", "<audiences>"
	)

    def test_series(self):
    	url = '/works/series/<series_name>'
	self.assert_request_calls(
	    url, self.controller.series, "<series_name>", None, None
	)

    def test_series_language(self):
    	url = '/works/series/<series_name>/<languages>'
	self.assert_request_calls(
	    url, self.controller.series, "<series_name>", "<languages>", None
	)

    def test_series_language_audience(self):
    	url = '/works/series/<series_name>/<languages>/<audiences>'
	self.assert_request_calls(
	    url, self.controller.series, "<series_name>", "<languages>",
            "<audiences>"
	)

    def test_permalink(self):
    	url = '/works/<identifier_type>/an/identifier'
	self.assert_request_calls(
	    url, self.controller.permalink,
            "<identifier_type>", "an/identifier"
	)

    def test_recommendations(self):
    	url = '/works/<identifier_type>/an/identifier/recommendations'
	self.assert_request_calls(
	    url, self.controller.recommendations,
            "<identifier_type>", "an/identifier"
	)

    def test_related_books(self):
    	url = '/works/<identifier_type>/an/identifier/related_books'
	self.assert_request_calls(
	    url, self.controller.related, "<identifier_type>", "an/identifier"
	)

    def test_report(self):
    	url = '/works/<identifier_type>/an/identifier/report'
	self.assert_request_calls(
	    url, self.controller.report,
            "<identifier_type>", "an/identifier",
	)
        self.assert_supported_methods(url, 'GET', 'POST')


class TestAnalyticsController(RouteTest):
    CONTROLLER_NAME = "analytics_controller"

    def test_track_analytics_event(self):
    	url = '/analytics/<identifier_type>/an/identifier/<event_type>'
	self.assert_request_calls(
	    url, self.controller.track_event,
            "<identifier_type>", "an/identifier", "<event_type>"
	)

class TestAdobeVendorID(RouteTest):

    CONTROLLER_NAME = "adobe_vendor_id"

    def test_adobe_vendor_id_get_token(self):
        # TODO: This requires auth.
    	url = '/AdobeAuth/authdata'
	self.assert_request_calls(
	    url, self.controller.create_authdata_handler,
	)
        # TODO: test what happens when vendor ID is not configured.

    def test_adobe_vendor_id_signin(self):
    	url = '/AdobeAuth/SignIn'
	self.assert_request_calls(
	    url, self.controller.signin_handler, http_method='POST'
	)
        self.assert_supported_methods(url, 'POST')

    def test_adobe_vendor_id_accountinfo(self):
    	url = '/AdobeAuth/AccountInfo'
	self.assert_request_calls(
	    url, self.controller.userinfo_handler, http_method='POST'
	)
        self.assert_supported_methods(url, 'POST')

    def test_adobe_vendor_id_status(self):
    	url = '/AdobeAuth/Status'
	self.assert_request_calls(
	    url, self.controller.status_handler,
	)


class TestAdobeDeviceManagement(RouteTest):
    CONTROLLER_NAME = "adobe_device_management"
    # TODO: These don't work because they require auth.

    def test_adobe_drm_devices(self):
    	url = "/AdobeAuth/devices"
	self.assert_request_calls(
            url, self.controller.device_id_list_handler
        )
        self.assert_supported_methods(url, 'GET', 'POST')

    def test_adobe_drm_device(self):
    	url = "/AdobeAuth/devices/<device_id>"
	self.assert_request_calls(
	    url, self.controller.device_id_handler, "<device_id>",
            http_method='DELETE'
	)
        self.assert_supported_methods(url, 'DELETE')

class TestOAuthController(RouteTest):
    # TODO: We might be able to do a better job of checking that
    # flask.request.args are propagated through, instead of checking
    # an empty dict.
    CONTROLLER_NAME = "oauth_controller"

    def test_oauth_authenticate(self):
    	url = "/oauth_authenticate"
        _db = self.manager._db
	self.assert_request_calls(
	    url, self.controller.oauth_authentication_redirect, {}, _db
	)

    def test_oauth_callback(self):
    	url = "/oauth_callback"
        _db = self.manager._db
	self.assert_request_calls(
	    url, self.controller.oauth_authentication_callback, _db, {}
	)


class TestODLNotificationController(RouteTest):
    CONTROLLER_NAME = "odl_notification_controller"

    def test_odl_notify(self):
    	url = "/odl_notify/<loan_id>"
	self.assert_request_calls(
	    url, self.controller.notify, "<loan_id>"
	)
        self.assert_supported_methods(url, 'GET', 'POST')


class TestHeartbeatController(RouteTest):
    CONTROLLER_NAME = "heartbeat"

    def test_heartbeat(self):
    	url = "/heartbeat"
	self.assert_request_calls(url, self.controller.heartbeat)


class TestHealthCheck(RouteTest):
    # This code isn't in a controller, and it doesn't really do anything,
    # so we check that it returns a specific result.
    def test_health_check(self):
        response = self.request("/healthcheck.html")
        eq_(200, response.status_code)

        # This is how we know we actually called health_check() and
        # not a mock method -- the Response returned by the mock
        # system has a status message in its .data.
        eq_("", response.data)


class TestLoadstormVerification(RouteTest):
    # TODO: There's no test for this, but it's not too bad because no
    # one uses it.
    pass
