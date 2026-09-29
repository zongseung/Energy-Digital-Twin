FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY target/release/jeju-data-bridge /usr/local/bin/jeju-data-bridge
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/jeju-data-bridge"]
