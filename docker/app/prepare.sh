#!/bin/bash

set -e
source /bd_build/buildconfig
set -x

# add packages needed for final image
apt-get update
$minimal_apt_get_install --no-upgrade \
  python2.7 \
  python-nose \
  python-setuptools \
  libpcre3 \
  libffi6 \
  libjpeg8 \
  libssl1.1 \
  libpq5 \
  libxmlsec1-openssl \
  libxml2
