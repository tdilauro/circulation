#!/bin/bash
set -ex

# Link the repository code to /home/simplified
su - simplified -c "ln -s /var/www/circulation /home/simplified/circulation"
