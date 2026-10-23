FROM jellyfin/jellyfin:latest

COPY wrapper/ /wrapper/
RUN chmod 0755 /wrapper/ffmpeg /wrapper/ffprobe
