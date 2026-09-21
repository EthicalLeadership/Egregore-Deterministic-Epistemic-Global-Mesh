FROM golang:1.22 AS base
WORKDIR /src
COPY . .
RUN go mod init dt1 2>/dev/null || true

FROM base AS build-es
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/es ./es

FROM base AS build-bpc
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/bpc ./bpc

FROM base AS build-gbs
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/gbs ./gbs

FROM base AS build-cir
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/cir ./cir

FROM base AS build-co
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/co ./co

FROM base AS build-gbw
WORKDIR /src
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/gbw ./gbw

FROM alpine:3.20 AS es
COPY --from=build-es /out/es /usr/local/bin/es
ENTRYPOINT ["/usr/local/bin/es"]

FROM alpine:3.20 AS bpc
COPY --from=build-bpc /out/bpc /usr/local/bin/bpc
ENTRYPOINT ["/usr/local/bin/bpc"]

FROM alpine:3.20 AS gbs
COPY --from=build-gbs /out/gbs /usr/local/bin/gbs
ENTRYPOINT ["/usr/local/bin/gbs"]

FROM alpine:3.20 AS cir
COPY --from=build-cir /out/cir /usr/local/bin/cir
ENTRYPOINT ["/usr/local/bin/cir"]

FROM alpine:3.20 AS co
COPY --from=build-co /out/co /usr/local/bin/co
ENTRYPOINT ["/usr/local/bin/co"]

FROM alpine:3.20 AS gbw
COPY --from=build-gbw /out/gbw /usr/local/bin/gbw
ENTRYPOINT ["/usr/local/bin/gbw"]