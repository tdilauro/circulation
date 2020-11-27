import logging
import urlparse
from lyrasis.app_xray import app
import sys

url = None
if len(sys.argv) > 1:
    url = sys.argv[1]

if __name__ == "__main__":
    base_url = url or u'http://localhost:6500/'
    scheme, netloc, path, parameters, query, fragment = urlparse.urlparse(base_url)
    if ':' in netloc:
        host, port = netloc.split(':')
        port = int(port)
    else:
        host = netloc
        port = 80

    # Required for subdomain support.
    app.config['SERVER_NAME'] = netloc

    debug = True

    # Workaround for a "Resource temporarily unavailable" error when
    # running in debug mode with the global socket timeout set by isbnlib
    if debug:
        import socket
        socket.setdefaulttimeout(None)

    logging.info("Starting app on %s:%s", host, port)
    app.run(debug=debug, host=host, port=port, threaded=True)
    app.run(url)
