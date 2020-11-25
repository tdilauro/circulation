#!/bin/bash
set -ex

# Configure uwsgi.
mkdir /etc/uwsgi/
cp /ls_build/uwsgi/uwsgi.ini /etc/uwsgi/uwsgi.ini

# Make log folder
mkdir /var/log/uwsgi
cp /ls_build/uwsgi/syslog.conf /etc/syslog-ng/conf.d/uwsgi.conf

# Prepare uwsgi for runit.
mkdir /etc/service/uwsgi
cp /ls_build/uwsgi/uwsgi.runit /etc/service/uwsgi/run

# Make folder for uwsgi sock
mkdir /var/run/uwsgi
chown simplified:simplified /var/run/uwsgi
