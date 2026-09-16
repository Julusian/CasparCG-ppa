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
3. Sign it: `debsign <distro>/casparcg-server_XXXXXXXX_source.changes`
4. Upload it: `dput ppa:casparcg/ppa <distro>/casparcg-server_XXXXXXXX_source.changes`

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

When publishing a new major version of CEF, the package name must be updated. This allows for installing multiple branches of `casparcg-server` simultaneously.

Running a source build of CEF takes a while to lint the build. The lint will fail with errors as the source is not provided for many of the files, but the needed `changes` will still be produced

### Server

This is based on the packaging from [debian](https://salsa.debian.org/multimedia-team/casparcg-server)

There is a folder per 'branch' of casparcg (2.3, 2.4 etc). This is so that they can be published with different names to make it easier to install the previous release

There is also the 'server-default' which is a minimal package to allow for installing the 'latest'. It points to one of the other packages, and should be published when a new release branch is released.

Note: server-2.3 is not nice to package, as its cef requires some of the cef assets to be located next to the binary.  
(cef builds prior to https://bitbucket.org/chromiumembedded/cef/commits/602c163)

### Client

The client can be published from here too, using a single branch for now.
