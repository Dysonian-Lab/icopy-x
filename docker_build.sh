#!/bin/bash
set -e

cat > /etc/apt/sources.list << SRCEOF
deb [arch=amd64] http://archive.ubuntu.com/ubuntu/ xenial main restricted universe
deb [arch=amd64] http://archive.ubuntu.com/ubuntu/ xenial-updates main restricted universe
deb [arch=armhf] http://ports.ubuntu.com/ubuntu-ports/ xenial main restricted universe
deb [arch=armhf] http://ports.ubuntu.com/ubuntu-ports/ xenial-updates main restricted universe
SRCEOF

dpkg --add-architecture armhf
apt-get update -qq
apt-get install -y -qq \
  gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf \
  make pkg-config git zip \
  libbz2-dev:armhf zlib1g-dev:armhf liblz4-dev:armhf

git clone --depth 1 --branch v4.21611 https://github.com/RfidResearchGroup/proxmark3.git /tmp/pm3
cd /tmp/pm3

echo "Applying iCopy-X patches..."
for p in /patches/pm3_*.patch; do
  [ -f "$p" ] || continue
  if git apply --check "$p" 2>/dev/null; then
    git apply "$p" && echo "  Applied: $(basename $p)"
  else
    echo "  Skipped (client-only or N/A): $(basename $p)"
  fi
done

sed -i "s/-fstack-clash-protection//g" Makefile.defs client/Makefile 2>/dev/null || true
sed -i "s/static const uint8_t protocol_marker\[\]/const uint8_t protocol_marker[]/" client/src/cmdhfaliro.c 2>/dev/null || true
if [ -f client/src/cmdhfvas.c ]; then
  sed -i "s/^static const uint16_t \(VAS_STATUS_[A-Z_]*\) = \(0x[0-9A-Fa-f]*\);/#define \1 \2/" client/src/cmdhfvas.c
fi

export PKG_CONFIG_PATH=/usr/lib/arm-linux-gnueabihf/pkgconfig
export PKG_CONFIG_LIBDIR=/usr/lib/arm-linux-gnueabihf/pkgconfig

echo "Starting build..."
make -j$(nproc) client \
  PLATFORM=PM3ICOPYX \
  CC=arm-linux-gnueabihf-gcc \
  CXX=arm-linux-gnueabihf-g++ \
  LD=arm-linux-gnueabihf-ld \
  "AR=arm-linux-gnueabihf-ar rcs" \
  RANLIB=arm-linux-gnueabihf-ranlib \
  cpu_arch=arm \
  SKIPQT=1 SKIPPYTHON=1 SKIPREVENGTEST=1 SKIPGD=1 SKIPBT=1 SKIPREADLINE=1

mkdir -p /out
cp client/proxmark3 /out/proxmark3
cd client && zip -r /out/lua.zip luascripts/ lualibs/

echo "=== BUILD COMPLETE ==="
ls -la /out/proxmark3 /out/lua.zip
