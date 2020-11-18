#!/bin/bash
set -e
source /bd_build/buildconfig
set -x

# Install the nodesource nodejs package
# This lets us use node 10 and avoids dependency conflict between node and libxmlsec1 over the
# version of the ssl library that we find from package managemnet
curl -sSL https://deb.nodesource.com/gpgkey/nodesource.gpg.key | apt-key add -
echo "deb https://deb.nodesource.com/node_10.x bionic main" >> /etc/apt/sources.list.d/nodesource.list
echo "deb-src https://deb.nodesource.com/node_10.x bionic main" >> /etc/apt/sources.list.d/nodesource.list

# Add packages we need to build the app and its dependancies
apt-get update
$minimal_apt_get_install --no-upgrade \
  python-dev \
  python2.7 \
  python-nose \
  python-setuptools \
  gcc \
  git \
  libpcre3 \
  libpcre3-dev \
  libffi-dev \
  libjpeg-dev \
  nodejs \
  libssl-dev \
  libpq-dev \
  libxmlsec1-dev \
  libxml2-dev

# Use the latest version of pip to install a virtual environment for the app.
python -m easy_install pip
pip install --no-cache-dir virtualenv virtualenvwrapper
