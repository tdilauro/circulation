#!/bin/bash
set -ex

# Add logrotate configuration files
cp /ls_build/logrotate/logrotate.conf /etc/
cp /ls_build/logrotate/default_logrotate /etc/logrotate.d/
cp /ls_build/logrotate/simplified_logrotate.conf /etc/logrotate.d/simplified.conf

chmod 644 /etc/logrotate.conf \
  /etc/logrotate.d/default_logrotate \
  /etc/logrotate.d/simplified.conf

# Remove logrotate for dpkg as we will do
# our own in the default_logrotate.
rm -rf /etc/logrotate.d/dpkg
