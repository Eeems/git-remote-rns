#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

if [[ "$libc" == "musl" ]]; then
	image="python:${python}-alpine"
	image="python:{$python}"
fi
if [[ "$arch" != "x86_64" ]]; then
	docker run \
		--privileged \
		--rm \
		tonistiigi/binfmt --install all
fi
script=$(
	cat <<EOF
pip install dist/*_$arch.whl;
python -m pytest -vv tests/
EOF
)
docker run \
	--rm \
	--volume="$(pwd):/src" \
	--platform="linux/${arch}" \
	"$image" \
	/bin/bash -ec "$script"
