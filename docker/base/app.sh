#!/bin/bash

# Create a user.
useradd -ms /bin/bash -U simplified

# Use the latest version of pip to install a virtual environment for the app.
python -m easy_install pip
pip install --no-cache-dir virtualenv virtualenvwrapper

# Give logs a place to go.
mkdir /var/log/simplified

# Copy scripts that run at startup.
cp /ls_build/startup/* /etc/my_init.d/
