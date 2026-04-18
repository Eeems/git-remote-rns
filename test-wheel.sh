#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

wheel="$(find wheelhouse -name "*linux_${arch}.whl" | head -n1)"
script=$(
  cat <<EOF
cd /src;
pip install "${wheel}"[web,test];
git config --global user.email 'root@localhost';
git config --global user.name "Github Runner";
git config --global init.defaultBranch trunk;
mkdir -p /tmp/test
cd /tmp/test;
cp -r /src/tests .
python -m pytest -vv tests;
EOF
)
if [[ "$libc" == "musl" ]]; then
  image="python:${python}-alpine"
  script="apk add --no-cache git;$script"
else
  image="python:${python}"
fi
case "$arch" in
i686)
  echo "WARNING: Unable to test i686 as there is no suitable python image. Skipping without error for now."
  exit 0
  ;;
s390x)
  echo "WARNING: Unable to test s390x as not all dependencies have wheels for it. Skipping without error for now."
  exit 0
  ;;
riscv64)
  echo "WARNING: Unable to test riscv64 as not all dependencies have wheels for it. Skipping without error for now."
  exit 0
  ;;
ppc64le)
  if [[ "$libc" == "musl" ]]; then
    echo "WARNING: Unable to test ppc64le on musl as not all dependencies have wheels for it. Skipping without error for now."
    exit 0
  fi
  platform="linux/${arch}"
  ;;
armv7l)
  if [[ "$libc" == "musl" ]]; then
    echo "WARNING: Unable to test armv7l on musl as not all dependencies have wheels for it. Skipping without error for now."
    exit 0
  fi
  platform="linux/arm/v7"
  ;;
*) platform="linux/${arch}" ;;
esac
if [[ "$arch" != "x86_64" ]]; then
  docker run \
    --privileged \
    --rm \
    tonistiigi/binfmt --install all
fi
docker run \
  --rm \
  --volume="$(pwd):/src" \
  --platform="$platform" \
  "$image" \
  /bin/sh -ec "$script"
