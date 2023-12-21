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

### General notes

Add changelog entry `dch`  
Make sure to replace the `UNRELEASED` tag with ubuntu distribution (currently `jammy`)

Build binary versions of each package: `debuild -b -uc -us`

Build source versions of each pacakge `debuild -S -uc -us`

Sign a build `debsign casparcg-server_XXXXXXXX__source.changes`

Upload a build `dput ppa:casparcg/ppa casparcg-server_XXXXXXXX__source.changes`

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

There is also the 'server-latest' which is a minimal package to allow for installing the 'latest'. It points to one of the other packages, and should be published when a new release branch is released.

### Client

The client can be published from here too, using a single branch for now.
