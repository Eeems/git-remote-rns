#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

wheel="$(find wheelhouse -name "*_${arch}.whl" | head -n1)"
if [[ -z "$wheel" ]]; then
  echo "No wheel found for architecture $arch"
  exit 1
fi
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
python -um pytest -vv tests;
EOF
)
if [[ "$libc" == "musl" ]]; then
  image="python:${python}-alpine"
  script="apk add --no-cache git;$script"
else
  image="python:${python}"
fi
install_rust() {
  if [[ "$libc" == "musl" ]]; then
    script="apk add --no-cache gcc musl-dev python3-dev libffi-dev openssl-dev cargo pkgconfig;$script"
  elif [[ "$libc" == "glibc" ]]; then
    script="apt-get update;DEBIAN_FRONTEND=\"noninteractive\" apt-get install -y rustc cargo;$script"
  else
    echo "ERROR: Unknown libc for i686"
    exit 1
  fi
}
case "$arch" in
i686)
  install_rust
  platform="linux/386"
  ;;
s390x)
  install_rust
  platform="linux/${arch}"
  ;;
riscv64)
  install_rust
  platform="linux/${arch}"
  ;;
ppc64le)
  if [[ "$libc" == "musl" ]]; then
    install_rust
  fi
  platform="linux/${arch}"
  ;;
armv7l)
  if [[ "$libc" == "musl" ]]; then
    install_rust
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
