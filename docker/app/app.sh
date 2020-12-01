#!/bin/bash
set -ex

# Link the repository code to /home/simplified
su - simplified -c "ln -s /var/www/circulation /home/simplified/circulation"

# Link lyrasis profiling hooks
BASE_PATH=/var/www/circulation
cd $BASE_PATH
LIB_PATH=$(source env/bin/activate && python -c "from distutils.sysconfig import get_python_lib; print(get_python_lib())")
ln -s $BASE_PATH/lyrasis/lyrasis_hooks.py $LIB_PATH/lyrasis_hooks.py
ln -s $BASE_PATH/lyrasis/lyrasis_hooks.pth $LIB_PATH/lyrasis_hooks.pth
#cp /var/www/circulation/lyrasis/sitecustomize.py /usr/lib/python2.7/sitecustomize.py