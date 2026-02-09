#!/bin/bash
set -euo pipefail

#=============================================================================
# CONFIGURATION
#=============================================================================
readonly SCRIPT_DIR="$(dirname "$(realpath "$0")")"
readonly INCLUDE_SH="include.sh"
readonly PODMAN_VM_INIT_DISK_SIZE=100
readonly PYTHON_VERSION=3.14
readonly DEBIAN_VERSION=slim-trixie
readonly DOCKER_IMAGE="docker.io/xomoxcc/sipstuff-k8s-operator:python-${PYTHON_VERSION}-${DEBIAN_VERSION}"
readonly DOCKER_IMAGE_LATEST="${DOCKER_IMAGE%:*}:latest"
readonly PLATFORMS=("linux/amd64" "linux/arm64")
readonly DOCKERFILE=../Dockerfile
readonly DOCKER_BUILD_CONTEXT=$(dirname "$(realpath "${DOCKERFILE}")")
readonly BUILDER_NAME=mbuilder
readonly ENABLE_PARALLEL_BUILDS=0
readonly BUILDTIME="$(date +'%Y-%m-%d %H:%M:%S %Z')"

readonly BUILD_BASE_ARGS=(
  "-f" "${DOCKERFILE}"
  "--build-arg" "buildtime=${BUILDTIME}"
  "--build-arg" "python_version=${PYTHON_VERSION}"
  "--build-arg" "debian_version=${DEBIAN_VERSION}"
  )

# podman machine ssh df -h
# podman --connection podman-machine-default system prune -a
# podman --connection podman-machine-default system df
# podman machine inspect podman-machine-default | grep -i disk

# Runtime state
PODMAN_VM_STARTED=0
DOCKER_IS_PODMAN=0

#=============================================================================
# HELPER FUNCTIONS
#=============================================================================
die() {
  echo "ERROR: $*" >&2
  exit 1
}

log() {
  echo "==> $*"
}

is_podman() {
  # Note: Don't use grep -q with pipefail, causes SIGPIPE (exit 141)
  docker --version 2>&1 | grep -i podman >/dev/null
}

#=============================================================================
# SETUP FUNCTIONS
#=============================================================================
setup_environment() {
  cd "${SCRIPT_DIR}" || die "Could not change to script directory"

  if [ -e "${INCLUDE_SH}" ] ; then
    source "${INCLUDE_SH}"
  fi

  DOCKER_CONFIG="$(realpath docker-config)"
  if ! [ -e  "${DOCKER_CONFIG}" ] ; then
    DOCKER_CONFIG="$(realpath ../docker-config)"
  fi
  if ! [ -e  "${DOCKER_CONFIG}" ] ; then
    DOCKER_CONFIG="${HOME}/.docker"
  fi

  export DOCKER_CONFIG
  export REGISTRY_AUTH_FILE="${DOCKER_CONFIG}/config.json"

  if is_podman; then
    DOCKER_IS_PODMAN=1
  fi
}

ensure_docker_login() {
  if [[ ! -e "${REGISTRY_AUTH_FILE}" ]]; then
    log "Logging in to Docker registry..."
    echo "${DOCKER_TOKEN}" | docker login --username "${DOCKER_TOKENUSER}" --password-stdin \
      || die "Docker login failed"
  fi
}

setup_docker_buildx() {
  if ! docker buildx inspect "${BUILDER_NAME}" --bootstrap >/dev/null 2>&1; then
    log "Setting up Docker buildx builder..."
    docker run --privileged --rm tonistiigi/binfmt --install all
    docker buildx create --name "${BUILDER_NAME}"
    docker buildx use "${BUILDER_NAME}"
  fi
}

#=============================================================================
# PODMAN VM FUNCTIONS
#=============================================================================
ensure_podman_vm_running() {
  if ! podman machine list | grep -q "podman-machine-default"; then
    log "Initializing podman machine..."
    podman machine init --disk-size ${PODMAN_VM_INIT_DISK_SIZE}
  fi

  if ! podman machine list --format "{{.Running}}" | grep -q "true"; then
    log "Starting podman machine..."
    podman machine start || die "Could not start podman machine (VM)"
    PODMAN_VM_STARTED=1
  fi
}

stop_podman_vm_if_started() {
  if (( PODMAN_VM_STARTED == 1 )); then
    log "Stopping podman machine..."
    podman machine stop
  fi
}

platform_needs_vm() {
  local platform="$1"
  ! podman run --rm --platform "${platform}" alpine uname -m >/dev/null 2>&1
}

copy_image_from_vm() {
  local image="$1"
  log "Copying image from VM to host: ${image}"
  podman image scp "podman-machine-default::${image}"
}

#=============================================================================
# BUILD FUNCTIONS
#=============================================================================
build_with_docker() {
  log "Building with Docker buildx (multi-arch)..."

  setup_docker_buildx

  printf -v platforms_csv '%s,' "${PLATFORMS[@]}"
  platforms_csv="${platforms_csv%,}"

  # Add latest tag if not already latest
  local -a build_args=("${BUILD_BASE_ARGS[@]}")
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    build_args+=("-t" "${DOCKER_IMAGE_LATEST}")
  fi

  build_args+=("-t" "${DOCKER_IMAGE}")

  docker buildx build \
    "${build_args[@]}" \
    --platform "${platforms_csv}" \
    --push \
    "${DOCKER_BUILD_CONTEXT}"
}

build_with_podman() {
  log "Building with Podman manifest workflow..."

  # Remove existing manifest/image if it exists
  echo podman manifest rm "${DOCKER_IMAGE}"
  podman manifest rm "${DOCKER_IMAGE}" 2>/dev/null || true
  echo podman image rm "${DOCKER_IMAGE}"
  podman image rm "${DOCKER_IMAGE}" 2>/dev/null || true

  # Track platform-specific data
  local -a platform_tags=()
  local -A platform_connect_args=()
  local -a build_pids=()

  # Add latest tag if not already latest
  local -a build_args=("${BUILD_BASE_ARGS[@]}")

  # Build for each platform (in parallel)
  for platform in "${PLATFORMS[@]}"; do
    local arch="${platform#*/}"
    local platform_tag="${DOCKER_IMAGE}-${arch}"
    local connect_arg=""

    # Check if platform needs VM
    if platform_needs_vm "${platform}"; then
      log "Platform ${platform} needs VM for emulation"
      ensure_podman_vm_running
      connect_arg="--connection podman-machine-default"
    fi

    platform_tags+=("${platform_tag}")
    platform_connect_args["${platform}"]="${connect_arg}"

    log "Building for ${platform} -> ${platform_tag} (background)..."
    # shellcheck disable=SC2086
    if (( ${ENABLE_PARALLEL_BUILDS:-0} == 1 )) ; then
      echo "(podman ${connect_arg} build \"${build_args[@]}\" --platform \"${platform}\" -t \"${platform_tag}\" .) &"
      (
        podman ${connect_arg} build \
          "${build_args[@]}" \
          --platform "${platform}" \
          -t "${platform_tag}" \
          "${DOCKER_BUILD_CONTEXT}" || exit 1
      ) &
      build_pids+=($!)
    else
      echo podman ${connect_arg} build "${build_args[@]}" --platform "${platform}" -t "${platform_tag}" "${DOCKER_BUILD_CONTEXT}"
      podman ${connect_arg} build "${build_args[@]}" --platform "${platform}" -t "${platform_tag}" "${DOCKER_BUILD_CONTEXT}" || exit 1
    fi
  done


  # Wait for all builds to complete
  if (( ${#build_pids[@]} > 0 )); then
    log "Waiting for ${#build_pids[@]} parallel builds..."
    local failed=0
    for pid in "${build_pids[@]}"; do
      if ! wait "$pid"; then
        log "Build failed (PID $pid)"
        failed=1
      fi
    done
    (( failed == 1 )) && die "One or more builds failed"
  fi

  # Copy images built in VM to host
  for platform in "${!platform_connect_args[@]}"; do
    if [[ -n "${platform_connect_args[$platform]}" ]]; then
      local arch="${platform#*/}"
      local platform_tag="${DOCKER_IMAGE}-${arch}"
      copy_image_from_vm "${platform_tag}"
    fi
  done

  # Create manifest from all platform images
  log "Creating manifest: ${DOCKER_IMAGE}"
  echo podman manifest create "${DOCKER_IMAGE}" "${platform_tags[@]}"
  podman manifest create "${DOCKER_IMAGE}" "${platform_tags[@]}"

  # Tag with latest (if not already latest)
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    log "Tagging as latest: ${DOCKER_IMAGE_LATEST}"
    podman tag "${DOCKER_IMAGE}" "${DOCKER_IMAGE_LATEST}"
  fi

  # log "To push, run:"
  # echo "  podman manifest push ${DOCKER_IMAGE} docker://${DOCKER_IMAGE}"
  echo podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE}"
  podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE}"

  # Push latest tag
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    log "Pushing as latest: ${DOCKER_IMAGE_LATEST}"
    podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE_LATEST}"
  fi
}

build_local_only() {
  log "Building local image only..."

  export BUILDKIT_PROGRESS=plain

  # Detect native platform
  local native_arch
  native_arch="$(uname -m)"
  case "${native_arch}" in
    x86_64)  native_arch="amd64" ;;
    aarch64) native_arch="arm64" ;;
  esac
  local native_platform="linux/${native_arch}"

  # Add latest tag if not already latest
  local -a build_args=("${BUILD_BASE_ARGS[@]}")
  build_args+=("--platform" "${native_platform}")
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    build_args+=("-t" "${DOCKER_IMAGE_LATEST}")
  fi

  build_args+=("-t" "${DOCKER_IMAGE}")

  echo docker build \
    "${build_args[@]}" \
    "${DOCKER_BUILD_CONTEXT}"

  docker build \
    "${build_args[@]}" \
    "${DOCKER_BUILD_CONTEXT}"
}

#=============================================================================
# MAIN
#=============================================================================
main() {
  setup_environment

  case "${1:-}" in
    login)
      ensure_docker_login
      ;;
    onlylocal)
      ensure_docker_login
      build_local_only
      ;;
    *)
      ensure_docker_login
      if (( DOCKER_IS_PODMAN == 1 )); then
        build_with_podman
      else
        build_with_docker
      fi
      ;;
  esac
}

# Run main and ensure cleanup
trap 'stop_podman_vm_if_started || true' EXIT
main "$@"
