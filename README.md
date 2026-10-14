# CasparCG PPA

The PPA is available at https://launchpad.net/~casparcg/+archive/ubuntu/ppa

## Usage

```
sudo add-apt-repository ppa:casparcg/ppa
sudo apt update

sudo apt install casparcg-server
# OR
sudo apt install casparcg-client
```

## Development

Some notes on how to publish new versions of packages.

This process is based upon the [debian guide](https://wiki.debian.org/BuildingTutorial)

### Layout

Each Ubuntu series has its own folder (`jammy`, `noble`, `resolute`), containing a folder per package. Only the `debian` folder of each package is tracked in git, the upstream source is unpacked alongside it when building.

Packages in `generic` don't depend on the series (eg the media-scanner, which is a prebuilt binary). These are uploaded once to `jammy`, then copied to the other series in Launchpad (Copy packages, keeping the binaries).

### Tooling

`scripts/ppa_tools.py` automates the tedious parts:

* `python3 scripts/ppa_tools.py bump <package> <version>` adds a changelog entry for a new upstream version, in every series folder containing that package. It prompts for which patches to keep.
* `python3 scripts/ppa_tools.py setup-local <distro> <package>` fetches the orig tarball(s) into the series folder, unpacks them into the package folder, and installs the build dependencies. Add `--skip-deps` to skip the dependency install, or `--orig <path>` to supply the upstream tarball yourself.
* `python3 scripts/ppa_tools.py fetch-source <distro> <package>` only fetches the orig tarball(s).
* `python3 scripts/ppa_tools.py update-cef [package]` bumps a CEF package (default `cef`) to the newest stable build of the CEF major it is at, updating `source.json` and the changelog in every series folder. `--dry-run` only reports whether there is an update.

The upstream source is described by `debian/source.json`, or `debian/watch` (via `uscan`) if that is not present. `source.json` can use a `direct_url` (CEF), a `github_asset`, or `uscan`, and can list extra `components` tarballs (the media-scanner uses these for the amd64 and arm64 binaries). The orig tarballs are named to match the changelog version, so rebuild suffixes like `+2` work.

When bumping CEF, update the `url` in `source.json` to the new build from https://cef-builds.spotifycdn.com/

Some old builds are no longer available upstream (eg cef-71); for these use `--orig` with a copy of the tarball.

### CI

On every push, GitHub Actions builds (source and binary) each package that changed. A single package can also be built on demand with the "Run workflow" button, specifying the folder and package.

The source build is uploaded as an artifact named `source-<distro>-<package>`. These are unsigned, and are **not** uploaded to Launchpad automatically.

### Publishing

Publishing is a manual step, done locally:

1. Bump the version: `python3 scripts/ppa_tools.py bump <package> <version>` (or edit the changelog with `dch`, making sure to set the series rather than `UNRELEASED`)
2. Get a source build, either by downloading the artifact from CI, or locally:
   * `python3 scripts/ppa_tools.py setup-local <distro> <package>`
   * Run in the package folder: `debuild -S -uc -us`
   * Optionally, test build binaries: `debuild -b -uc -us`
3. Sign and upload it: run `scripts/upload.sh` inside the extracted artifact (or the distro folder, if it only contains one build). This uses `dput` if installed, otherwise uploads with `curl`.

   Or manually: `debsign <file>_source.changes` then `dput ppa:casparcg/ppa <file>_source.changes`

The orig tarball is only included in the upload when the upstream version changes. Launchpad rejects an orig tarball that differs from one it already has for the same version, so for a new upload of the same upstream version, only bump the debian revision (eg `noble1` to `noble2`).

Clean up build outputs with `./clean.sh`

#### Working with patches (quilt)

Apply all patches: `QUILT_PATCHES="debian/patches" quilt push -a`

Undo all patches: `QUILT_PATCHES="debian/patches" quilt pop -a`

Refresh a patch: `QUILT_PATCHES="debian/patches" quilt refresh <name>`

Create a patch:
* `QUILT_PATCHES="debian/patches" quilt new <name>`
* `QUILT_PATCHES="debian/patches" quilt edit <filename>`
* `QUILT_PATCHES="debian/patches" quilt refresh`

### CEF

CEF is published as its own package, to minimise the size of each deb and the build process.

This package will most likely be the cause of the PPA running out of space, as each update consumes 5% of the available space.

There are two kinds of CEF package:

- `cef-<major>` (eg `cef-142`): built for CEF's experimental API, so only works with the exact CEF build it was compiled against. A new CEF major needs a new package name. Kept as-is for the server branches already using them.
- `cef` (source `casparcg-cef`): built for CEF stable API versions (https://chromiumembedded.github.io/cef/api_versioning). Anything built against stable API NNNNN runs on any CEF that still supports that version, so newer CEF releases go into the same package and users get Chromium updates without a new server build.

`cef` builds one runtime package, `casparcg-cef`, and a `casparcg-cef-apiNNNNN-dev` package for each API version listed in `debian/control`. Each -dev package has the headers, a `libcef_dll_wrapper` built for exactly NNNNN, and a CMake config. The runtime `Provides: casparcg-cef-apiNNNNN` for each of them, and that is what servers depend on. The API versions are read from the -dev package names in `debian/control`, so that is the only place to change them.

The rule: whenever `casparcg-cef` provides `casparcg-cef-apiNNNNN`, it must be able to run anything built against stable API NNNNN. The package version is the upstream CEF version.

The "Update CEF" workflow runs `update-cef` daily. When there is a newer build of the same CEF major, it pushes an `update/cef-<version>` branch, opens a pull request and builds it. For it to open pull requests, "Allow GitHub Actions to create and approve pull requests" must be enabled in the repository's Actions settings. It never moves to a newer CEF major, as that needs the API versions checking first.

Updating to a newer CEF (by hand, or reviewing the workflow's pull request):
1. Check include/cef_api_versions.h in the new tarball: every NNNNN in `debian/control` must be between CEF_API_VERSION_MIN and CEF_API_VERSION_LAST. (Otherwise the wrapper build fails with "#error Building with unsupported CEF_API_VERSION value".)
2. Update the URLs in debian/source.json and bump the changelog, in every series folder (`update-cef` does both).
3. Test the HTML producer, then publish. No server rebuild needed. The API is guaranteed, the behaviour of a new Chromium is not.

Adding an API version (eg a server wants API added after NNNNN): add another `casparcg-cef-apiMMMMM-dev` stanza to `debian/control`, copying the existing one, in every series folder. The existing ones are unaffected.

When CEF drops NNNNN: first rebuild the maintained server branches against a newer `casparcg-cef-apiMMMMM-dev` and publish them. Then remove the `casparcg-cef-apiNNNNN-dev` stanza and publish the new CEF. Once it no longer provides `casparcg-cef-apiNNNNN`, apt won't upgrade `casparcg-cef` on machines with a server that still depends on it, so they keep the old CEF and keep working. But Launchpad only keeps the latest version, so new installs of that server will fail.

Building against it: Build-Depends on `casparcg-cef-apiNNNNN-dev`, use `find_package(casparcg-cef-apiNNNNN CONFIG REQUIRED)` and link `CasparCG::CEF`. Don't set CEF_API_VERSION yourself. In the server's debian/control, Depends on casparcg-cef-apiNNNNN without a version pin.

Running a source build of CEF takes a while to lint the build. The lint will fail with errors as the source is not provided for many of the files, but the needed `changes` will still be produced

### Server

This is based on the packaging from [debian](https://salsa.debian.org/multimedia-team/casparcg-server)

There is a folder per 'branch' of casparcg (2.3, 2.4 etc). This is so that they can be published with different names to make it easier to install the previous release

There is also the 'server-default' which is a minimal package to allow for installing the 'latest'. It points to one of the other packages, and should be published when a new release branch is released.

Note: server-2.3 is not nice to package, as its cef requires some of the cef assets to be located next to the binary.  
(cef builds prior to https://bitbucket.org/chromiumembedded/cef/commits/602c163)

### Client

The client can be published from here too, using a single branch for now.
