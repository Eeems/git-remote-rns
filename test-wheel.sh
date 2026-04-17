#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

if [[ "$libc" == "musl" ]]; then
	image="python:${python}-alpine"
else
	image="python:${python}"
fi
if [[ "$arch" != "x86_64" ]]; then
	docker run \
		--privileged \
		--rm \
		tonistiigi/binfmt --install all
fi
wheel="$(find dist -name "*linux_${arch}.whl" | head -n1)"
script=$(
	cat <<EOF
cd /src;
pip install "${wheel}"[web,test];
python -m pytest -vv tests/;
EOF
)
docker run \
	--rm \
	--volume="$(pwd):/src" \
	--platform="linux/${arch}" \
	"$image" \
	/bin/bash -ec "$script"
