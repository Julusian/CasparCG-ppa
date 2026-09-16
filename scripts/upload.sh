#!/bin/bash
# Sign and upload a source build to the PPA.
# Run inside an extracted CI artifact (or any folder containing a *_source.changes)
#
# Usage: upload.sh [ppa]   (default: casparcg/ppa)
#
# Uses dput if available, otherwise uploads over FTP with curl (eg on Fedora)

set -euo pipefail

PPA="${1:-casparcg/ppa}"
PPA_OWNER="${PPA%%/*}"
PPA_NAME="${PPA#*/}"
FTP_URL="ftp://ppa.launchpad.net/~${PPA_OWNER}/ubuntu/${PPA_NAME}/"

shopt -s nullglob
CHANGES=(*_source.changes)
if [[ ${#CHANGES[@]} -ne 1 ]]; then
	echo "Expected exactly one *_source.changes in $(pwd), found ${#CHANGES[@]}" >&2
	exit 1
fi
CHANGES="${CHANGES[0]}"

if grep -q "BEGIN PGP SIGNED MESSAGE" "$CHANGES"; then
	echo "Already signed: $CHANGES"
else
	echo "Signing: $CHANGES"
	# debsign also signs the .dsc and .buildinfo, updating their checksums in the .changes
	debsign "$CHANGES"
fi

# Files listed in the 'Files:' section of the .changes (the filename is the last field)
mapfile -t FILES < <(awk '/^Files:/ {in_files=1; next} in_files && /^ / {print $NF; next} {in_files=0}' "$CHANGES")

for f in "${FILES[@]}"; do
	if [[ ! -f "$f" ]]; then
		echo "Missing file listed in $CHANGES: $f" >&2
		exit 1
	fi
done

echo
echo "Upload to ppa:${PPA}:"
for f in "${FILES[@]}" "$CHANGES"; do
	echo "  $f"
done
read -r -p "Continue? [y/N] " answer
if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
	echo "Aborted"
	exit 1
fi

if command -v dput >/dev/null; then
	dput "ppa:${PPA}" "$CHANGES"
else
	# Launchpad groups an upload by FTP session, so everything must go in a single curl
	# invocation (which reuses the connection). The .changes must be last, as it
	# triggers processing of the upload
	CURL_ARGS=()
	for f in "${FILES[@]}" "$CHANGES"; do
		CURL_ARGS+=(-T "$f" "$FTP_URL")
	done
	curl --fail --show-error --globoff "${CURL_ARGS[@]}"
fi

echo "Done. Launchpad will email once the upload is accepted or rejected."
