FROM jellyfin/jellyfin:latest

RUN set -eux; \
    mv /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/lib/jellyfin-ffmpeg/ffmpeg.real; \
    mv /usr/lib/jellyfin-ffmpeg/ffprobe /usr/lib/jellyfin-ffmpeg/ffprobe.real

COPY wrapper/ffmpeg /usr/lib/jellyfin-ffmpeg/ffmpeg
COPY wrapper/ffprobe /usr/lib/jellyfin-ffmpeg/ffprobe

RUN chmod 0755 \
    /usr/lib/jellyfin-ffmpeg/ffmpeg \
    /usr/lib/jellyfin-ffmpeg/ffprobe
