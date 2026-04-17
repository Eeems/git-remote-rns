#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

wheel="$(find dist -name "*linux_${arch}.whl" | head -n1)"
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
if [[ "$arch" != "x86_64" ]]; then
	docker run \
		--privileged \
		--rm \
		tonistiigi/binfmt --install all
fi
case "$arch" in
i686) platform="linux/386" ;;
armv7l) platform="linux/arm/v7" ;;
*) platform="linux/${arch}" ;;
esac
docker run \
	--rm \
	--volume="$(pwd):/src" \
	--platform="$platform" \
	"$image" \
	/bin/sh -ec "$script"
