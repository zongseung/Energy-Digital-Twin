FROM rust:1.94.1-bookworm AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN useradd --system --uid 10001 --create-home twin
COPY --from=build /app/target/release/jeju-twin /usr/local/bin/jeju-twin
USER twin
ENTRYPOINT ["jeju-twin"]
