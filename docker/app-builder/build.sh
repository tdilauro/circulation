#!/bin/bash
set -ex

cd /var/www/circulation

# Setup a virtual environment for the app.
virtualenv -p /usr/bin/python2.7 env

# Pass runtime environment variables to the app at runtime.
touch environment.sh
SIMPLIFIED_ENVIRONMENT=/var/www/circulation/environment.sh
echo "if [[ -f $SIMPLIFIED_ENVIRONMENT ]]; then \
      source $SIMPLIFIED_ENVIRONMENT; fi" >> env/bin/activate

# Install required python libraries.
set +x && source env/bin/activate && set -x
pip install --no-cache-dir -r requirements.txt

# Install NLTK.
python -m textblob.download_corpora
mv /root/nltk_data /usr/lib/

# Initialize admin interface
cd api/admin
npm install
cd ../..
