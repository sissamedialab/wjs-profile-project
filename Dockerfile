# Please build as
# eval $(cat .env) DOCKER_BUILDKIT=1 docker build --build-arg GIT_ACCESS_TOKEN .
# where `( umask 0077; echo "GIT_ACCESS_TOKEN=******" > .env; )`
# this way you don't even leave the GIT_ACCESS_TOKEN variable in the environment
FROM debian-python-git-janeway
COPY ./cicd_*settings.py .
ARG GIT_ACCESS_TOKEN
ENV GIT_ACCESS_TOKEN=${GIT_ACCESS_TOKEN}
# TODO: where do PACKAGE_NAME and PIP_INDEX_URL come from?
ENV PACKAGE_NAME=wjs.jcom_profile
ENV PACKAGE_REPO_NAME=wjs-profile-project
ENV DJANGO_SETTINGS_MODULE=core.cicd_merged_settings
ENV JANEWAY_SETTINGS_MODULE=core.cicd_settings
# Adding a git repo does not seem to work (I the the web page...)
# ADD https://git:${GIT_ACCESS_TOKEN}@gitlab.sissamedialab.it/wjs/janeway.git ./janeway
# from https://docs.docker.com/engine/reference/builder/#adding-a-git-repository-add-git-ref-dir
RUN --mount=type=cache,mode=0755,target=/root/.cache/pip  \
    mv cicd_settings.py cicd_merged_settings.py janeway/src/core && \
    git clone --depth 1 --no-single-branch https://git:${GIT_ACCESS_TOKEN}@gitlab.sissamedialab.it/wjs/${PACKAGE_REPO_NAME}.git && \
    pip install ./${PACKAGE_REPO_NAME}[test]
# ENTRYPOINT ["/bin/bash", "-c", "sleep 10000"]
# CMD ["-c", "pytest -c /${PACKAGE_REPO_NAME}/pytest.ini /${PACKAGE_REPO_NAME}/"]
WORKDIR /janeway/src
RUN JANEWAY_SETTINGS_MODULE=core.cicd_settings python3 ./manage.py install_themes
CMD ["/bin/bash", "-c", "pytest -c /${PACKAGE_REPO_NAME}/pytest.ini /${PACKAGE_REPO_NAME}/ -v -x --create-db"]
