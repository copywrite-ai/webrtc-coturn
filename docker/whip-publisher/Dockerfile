ARG UBUNTU_VERSION=24.04
ARG GST_PLUGINS_RS_REF=0.14.1
ARG PUBLISHER_BASE_IMAGE=tunnel-whip-publisher-base:ubuntu24.04-gst-rs-0.14.1

FROM ${PUBLISHER_BASE_IMAGE}

ARG UBUNTU_VERSION
ARG GST_PLUGINS_RS_REF

RUN set -eux; \
  apt-get update \
  && apt-get install -y --no-install-recommends iproute2 python3-cairo python3-gi-cairo \
  && rm -rf /var/lib/apt/lists/*

COPY docker/whip-publisher/publish-whip.sh /usr/local/bin/publish-whip.sh
COPY docker/whip-publisher/publish-whip.py /usr/local/bin/publish-whip.py
COPY vendor/open-raster-timing-marker/src/python/ortm /opt/ortm/ortm

ENV PYTHONPATH=/opt/ortm

RUN chmod +x /usr/local/bin/publish-whip.sh /usr/local/bin/publish-whip.py \
  && python3 -c 'import cairo; import gi; import ortm; assert ortm.__version__ == "0.1.0"' \
  && gst-inspect-1.0 whipclientsink >/dev/null \
  && gst-inspect-1.0 cairooverlay >/dev/null \
  && gst-inspect-1.0 v4l2src >/dev/null

ENTRYPOINT ["/usr/local/bin/publish-whip.sh"]
