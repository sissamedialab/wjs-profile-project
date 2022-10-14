# Please build as
# eval $(cat .env) DOCKER_BUILDKIT=1 docker build --tag debian-python-git-janeway --build-arg GIT_ACCESS_TOKEN ./Dockerfile-python+git+janeway
# where `( umask 0077; echo "GIT_ACCESS_TOKEN=******" > .env; )`
# this way you don't even leave the GIT_ACCESS_TOKEN variable in the environment
#
# To push to the registry (so that the image is available to the CI/CD framework):
# 1. tag the image for the registry:
#    docker tag debian-python-git-janeway registry.gitlab.sissamedialab.it/wjs/wjs-profile-project/debian-python-git-janeway
#    (this can be done diretly during creation)
# 2. login to the registry:
#    docker login registry.gitlab.sissamedialab.it
# 3. push the image:
#    docker push registry.gitlab.sissamedialab.it/wjs/wjs-profile-project/debian-python-git-janeway:latest
#
FROM debian-python-git
ARG GIT_ACCESS_TOKEN
ENV GIT_ACCESS_TOKEN ${GIT_ACCESS_TOKEN}
# Adding a git repo does not seem to work (I the the web page...)
# ADD https://git:${GIT_ACCESS_TOKEN}@gitlab.sissamedialab.it/wjs/janeway.git ./janeway
# from https://docs.docker.com/engine/reference/builder/#adding-a-git-repository-add-git-ref-dir
# TODO: drop `pip install nose` when requirements gets fixed
RUN --mount=type=cache,mode=0755,target=/root/.cache/pip  \
    git clone --depth 1 https://git:${GIT_ACCESS_TOKEN}@gitlab.sissamedialab.it/wjs/janeway.git && \
    cd janeway && \
    git checkout jcom && \
    sed -i 's/jsmin.*/jsmin/' requirements.txt && \
    sed -i '/mysqlclient/d' requirements.txt && \
    pip install -r requirements.txt && \
    pip install nose
