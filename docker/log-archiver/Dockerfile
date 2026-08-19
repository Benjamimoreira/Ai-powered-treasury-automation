FROM alpine:3.20

RUN apk add --no-cache bash tzdata

COPY archive-logs.sh /usr/local/bin/archive-logs.sh
COPY crontab /etc/crontabs/root
RUN chmod +x /usr/local/bin/archive-logs.sh \
    && mkdir -p /archive

CMD ["crond", "-f", "-l", "2"]
