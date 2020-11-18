#!/bin/bash
set -e
source /bd_build/buildconfig
set -x

apt-get update
$minimal_apt_get_install nginx

# Configure nginx.
rm /etc/nginx/sites-enabled/default
cp /ls_build/nginx/nginx.conf /etc/nginx/conf.d/circulation.conf
echo "daemon off;" >> /etc/nginx/nginx.conf

# Prepare nginx for runit.
mkdir /etc/service/nginx
cp /ls_build/nginx/nginx.runit /etc/service/nginx/run
