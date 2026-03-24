#!/bin/bash
set -e
libc=${libc:-glibc}
arch=${arch:-x86_64}
python=${python:-3.11}

python_interpreter=cp${python//./}-cp${python//./}
script=$(
  cat <<EOF
manylinux-interpreters ensure $python_interpreter;
export PATH=\$PATH:/opt/python/$python_interpreter/bin;
cd /src;
python -m pip install --upgrade build;
python -m build --wheel;
auditwheel repair dist/*.whl;
EOF
)
if [[ "$libc" == "musl" ]]; then
  image="musllinux_1_2_$arch"
elif [[ "$arch" == "armv7l" ]]; then
  image="manylinux_2_35_$arch"
else
  image="manylinux_2_34_$arch"
fi
if [[ "$arch" != "x86_64" ]]; then
  docker run \
    --privileged \
    --rm \
    tonistiigi/binfmt --install all
fi
docker run \
  --rm \
  -v "$(pwd):/src" \
  quay.io/pypa/"$image":latest \
  /bin/bash -c "$script"
