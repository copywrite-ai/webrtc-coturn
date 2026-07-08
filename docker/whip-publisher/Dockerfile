ARG BASE_IMAGE=docker.1ms.run/library/ubuntu:24.04

FROM ${BASE_IMAGE} AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/root/.cargo/bin:${PATH}
ARG GST_PLUGINS_RS_REF=0.14.1

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    libglib2.0-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer-plugins-bad1.0-dev \
    libgirepository1.0-dev \
    libcairo2-dev \
    libnice-dev \
    libssl-dev \
    pkg-config \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal \
  && cargo install cargo-c

ENV CARGO_NET_GIT_FETCH_WITH_CLI=true

RUN git clone --depth 1 --branch "${GST_PLUGINS_RS_REF}" \
    https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git /tmp/gst-plugins-rs \
  && mkdir -p /opt/gst-plugins-rs \
  && cd /tmp/gst-plugins-rs \
  && cargo cinstall -p gst-plugin-webrtc --prefix=/opt/gst-plugins-rs --release \
  && cargo cinstall -p gst-plugin-webrtchttp --prefix=/opt/gst-plugins-rs --release \
  && ln -s "/opt/gst-plugins-rs/lib/$(gcc -dumpmachine)" /opt/gst-plugins-rs/lib/gstreamer-runtime

FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib/gstreamer-runtime

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    gstreamer1.0-nice \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/gst-plugins-rs /opt/gst-plugins-rs

COPY publish-whip.sh /usr/local/bin/publish-whip.sh
RUN chmod +x /usr/local/bin/publish-whip.sh \
  && gst-inspect-1.0 whipclientsink >/dev/null

ENTRYPOINT ["/usr/local/bin/publish-whip.sh"]
