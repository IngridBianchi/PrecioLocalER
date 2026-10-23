#!/usr/bin/env bash
# Instala el runtime de Intel OpenMP (libiomp5.so) en una imagen Debian.
#
# Por qué: el SVD que usa SsaForecaster (ML.NET) es MKL. En Windows
# MklImports.dll trae todo; en Linux `libMklImports.so` (que sí llega por
# NuGet) enlaza contra `libiomp5.so`, que no está ni en la imagen base de
# .NET ni en los repos de Debian. Sin él, `Fit()` lanza DllNotFoundException.
#
# El paquete `intel-oneapi-runtime-openmp` son unos pocos MB; no se instala
# el MKL completo (~1 GB) porque la parte de ML.NET ya viene en el NuGet.
#
# Uso: scripts/install-mkl-runtime.sh   (requiere root)
set -euo pipefail

KEYRING=/usr/share/keyrings/intel-oneapi-archive-keyring.gpg
REPO_LIST=/etc/apt/sources.list.d/oneAPI.list
KEY_URL=https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS-2023.PUB

if [ -f /opt/intel/oneapi/redist/lib/libiomp5.so ] || [ -f /usr/lib/libiomp5.so ]; then
    echo "libiomp5.so ya presente; nada que hacer."
    exit 0
fi

apt-get update -qq
apt-get install -y --no-install-recommends curl ca-certificates gnupg

curl -fsSL "$KEY_URL" | gpg --dearmor -o "$KEYRING"
chmod 644 "$KEYRING"
echo "deb [arch=amd64 signed-by=$KEYRING] https://apt.repos.intel.com/oneapi all main" > "$REPO_LIST"

apt-get update -qq
apt-get install -y --no-install-recommends intel-oneapi-runtime-openmp

# El .so queda en /opt/intel/oneapi/redist/lib, que no está en el path de ld.
echo "/opt/intel/oneapi/redist/lib" > /etc/ld.so.conf.d/intel-oneapi.conf
ldconfig

ldd /opt/intel/oneapi/redist/lib/libiomp5.so >/dev/null
echo "libiomp5.so instalado correctamente."
