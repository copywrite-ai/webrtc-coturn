ARG UBUNTU_VERSION=24.04
ARG GST_PLUGINS_RS_REF=0.14.1
ARG PUBLISHER_BASE_IMAGE=tunnel-whip-publisher-base:ubuntu24.04-gst-rs-0.14.1

FROM ${PUBLISHER_BASE_IMAGE}

ARG UBUNTU_VERSION
ARG GST_PLUGINS_RS_REF

COPY publish-whip.sh /usr/local/bin/publish-whip.sh
COPY publish-whip.py /usr/local/bin/publish-whip.py

RUN chmod +x /usr/local/bin/publish-whip.sh /usr/local/bin/publish-whip.py \
  && gst-inspect-1.0 whipclientsink >/dev/null \
  && gst-inspect-1.0 textoverlay >/dev/null

ENTRYPOINT ["/usr/local/bin/publish-whip.sh"]
