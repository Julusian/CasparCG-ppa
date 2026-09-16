#!/bin/bash
# Remove build outputs from the series folders. Orig tarballs are kept, so they can be reused

cd "$(dirname "$0")"
rm -f */*.build */*.buildinfo */*.changes */*.dsc */*.deb */*.ddeb */*.debian.tar.* */*.upload
