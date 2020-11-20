#!/bin/bash

cd /var/www

# Make shallow clones of the passed in git repo so we still have
# git folders, but we don't need a copy of the complete git history
git clone --depth=1 file:///var/www/circulation cm
git clone --depth=1 file:///var/www/circulation/core cm/core

cd circulation

# Add a .version file to the directory. This file
# supplies an endpoint to check the app's current version.
printf "$(git describe --tags)" > ../cm/.version

# Add files to track commit used
printf "$(git log --pretty=format:'%H' -n 1)" > ../cm/.commit
printf "$(git --git-dir=core/.git log --pretty=format:'%H' -n 1)" > ../cm/core/.commit

cd ..

# Copy data we need from circulation to cm then replace cm
cp -R circulation/env cm/env
mkdir -p cm/api/admin/node_modules/simplified-circulation-web/dist
cp -R circulation/api/admin/node_modules/simplified-circulation-web/dist cm/api/admin/node_modules/simplified-circulation-web/dist
rm -Rf circulation
mv cm circulation

# Clean up git remotes
cd circulation
git remote remove origin
git --git-dir=core/.git remote remove origin
