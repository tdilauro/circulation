#!/bin/bash

# Set our working dir
cd /source

# If submodule isn't initialzied then do it
if [[ ! -f "core/.git" ]]; then
  # If submodule is using git, switch to https
  git submodule init
  git config submodule.core.url `git config submodule.core.url | perl -p -e 's|git@(.*?):|https://\1/|g'`
  git submodule update --init --recursive
fi

# Add a .version file to the directory. This file
# supplies an endpoint to check the app's current version.
printf "$(git describe --tags)" > .version

# Add files to track commit used
printf "$(git log --pretty=format:'%H' -n 1)" > .commit
cd core
printf "$(git log --pretty=format:'%H' -n 1)" > .commit
cd ..

# Remove git history as is just bloats the image
rm -Rf .git
rm -Rf core/.git

# Remove docker files from source folder
rm -Rf docker
