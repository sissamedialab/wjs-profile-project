# Please build as
# docker build --tag debian-python-git ./Dockerfile-python+git
FROM debian:11-slim
RUN apt-get update && \
    apt-get install -y file && \
    apt-get install -y python3 pip && \
    apt-get install -y git
