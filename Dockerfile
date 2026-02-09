ARG python_version=3.14
ARG debian_version=slim-trixie

FROM python:${python_version}-${debian_version}

# repeat without defaults in this build-stage
ARG python_version
ARG debian_version

LABEL maintainer="Henning Thiess <ht@xomox.cc>"
LABEL description="sipstuff-k8s-operator — FastAPI service that creates SIP call K8s Jobs"

RUN apt update && \
    apt -y full-upgrade && \
    apt -y install iputils-ping locales vim tini bind9-dnsutils curl  && \
    pip install --upgrade pip && \
    rm -rf /var/lib/apt/lists/*

RUN sed -i -e 's/# de_DE.UTF-8 UTF-8/de_DE.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    update-locale LC_ALL=de_DE.UTF-8 LANG=de_DE.UTF-8 && \
    rm -f /etc/localtime && \
    ln -s /usr/share/zoneinfo/Europe/Berlin /etc/localtime

# MULTIARCH-BUILD-INFO: https://itnext.io/building-multi-cpu-architecture-docker-images-for-arm-and-x86-1-the-basics-2fa97869a99b
ARG TARGETOS
ARG TARGETARCH
RUN echo "I'm building for $TARGETOS/$TARGETARCH"

ARG UID=1200
ARG GID=1201
ARG UNAME=pythonuser
RUN groupadd -g ${GID} -o ${UNAME} && \
    useradd -m -u ${UID} -g ${GID} -o -s /bin/bash ${UNAME}

USER ${UNAME}

ENV PATH="/home/pythonuser/.local/bin:$PATH"

WORKDIR /app

COPY --chown=${UID}:${GID} requirements.txt ./
RUN pip3 install --no-cache-dir --upgrade -r ./requirements.txt

COPY --chown=${UID}:${GID} README.md pyproject.toml ./

COPY --chown=${UID}:${GID} sipstuff_k8s_operator ./sipstuff_k8s_operator
RUN pip install --no-cache-dir .

# set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

ARG gh_ref=gh_ref_is_undefined
ENV GITHUB_REF=$gh_ref
ARG gh_sha=gh_sha_is_undefined
ENV GITHUB_SHA=$gh_sha
ARG buildtime=buildtime_is_undefined
ENV BUILDTIME=$buildtime

ENTRYPOINT ["tini", "--"]
CMD ["sipstuff_k8s_operator"]
