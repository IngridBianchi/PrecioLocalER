# Imagen del servicio ml-dotnet (.NET 8 + ML.NET).
# Hardening WP1: non-root, restore en capa cacheable, healthcheck real.
# Uso: docker build -t preciolocal-ml-dotnet services/ml-dotnet
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

COPY PrecioLocal.Ml.csproj ./
RUN dotnet restore PrecioLocal.Ml.csproj

COPY . ./

# -r linux-x64: sin RID, NuGet no copia los natives de runtimes/ al output y
# el servicio arranca pero todo forecast degrada.
RUN dotnet publish PrecioLocal.Ml.csproj \
        -c Release \
        -r linux-x64 \
        --self-contained false \
        -o /app/publish \
        /p:UseAppHost=true

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app

# El SVD de SsaForecaster es MKL: libMklImports.so (que llega por NuGet)
# necesita libiomp5.so, que no está en Debian ni en la imagen base.
COPY scripts/install-mkl-runtime.sh /tmp/install-mkl-runtime.sh
RUN chmod +x /tmp/install-mkl-runtime.sh \
    && /tmp/install-mkl-runtime.sh \
    && rm -rf /tmp/install-mkl-runtime.sh \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y

COPY --from=build /app/publish .

USER app

ENV ASPNETCORE_ENVIRONMENT=Production \
    ASPNETCORE_HTTP_PORTS=8001

EXPOSE 8001

# La imagen no trae curl: el probe usa el propio runtime con --healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["dotnet", "PrecioLocal.Ml.dll", "--healthcheck"]

CMD ["dotnet", "PrecioLocal.Ml.dll"]
