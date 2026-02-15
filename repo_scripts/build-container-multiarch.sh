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
readonly DOCKER_BUILD_CONTEXT=$(dirname "$(realpath --relative-to="${SCRIPT_DIR}" "${SCRIPT_DIR}/${DOCKERFILE}")")
REMOTE_ARM64_CONNECTION="${REMOTE_ARM64_CONNECTION:-}"  # e.g. "user@rock5b"; made readonly after sourcing include.sh
# Podman's Go SSH client cannot use the SSH agent or encrypted keys.
# Use a dedicated unencrypted key (generate with: ssh-keygen -t ed25519 -f ~/.ssh/id_podman -N "")
REMOTE_ARM64_SSH_IDENTITY="${REMOTE_ARM64_SSH_IDENTITY:-}"
readonly BUILDER_NAME=mbuilder
readonly ENABLE_PARALLEL_BUILDS=1
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

format_duration() {
  local seconds="$1"
  local mins=$(( seconds / 60 ))
  local secs=$(( seconds % 60 ))
  if (( mins > 0 )); then
    printf '%dm %ds' "${mins}" "${secs}"
  else
    printf '%ds' "${secs}"
  fi
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

  # Lock REMOTE_ARM64_CONNECTION after include.sh (and include.local.sh) had a chance to set it
  readonly REMOTE_ARM64_CONNECTION

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

ensure_remote_arm64_connection() {
  local user_host="${REMOTE_ARM64_CONNECTION}"
  # Podman's Go SSH client cannot use the SSH agent or encrypted keys.
  # Use a dedicated unencrypted key (generate with: ssh-keygen -t ed25519 -f ~/.ssh/id_podman -N "")
  local ssh_identity="${REMOTE_ARM64_SSH_IDENTITY}"

  if [[ ! -f "${ssh_identity}" ]]; then
    die "SSH identity '${ssh_identity}' not found. Create it with: ssh-keygen -t ed25519 -f ${ssh_identity} -N \"\" && ssh-copy-id -i ${ssh_identity} ${user_host}"
  fi

  # Check if connection already registered
  if podman system connection list --format "{{.Name}}" | grep -qxF "${user_host}"; then
    log "Podman connection '${user_host}' already registered"
  else
    log "Registering podman connection '${user_host}'..."
    local remote_uid
    remote_uid="$(ssh "${user_host}" id -u)" \
      || die "SSH to '${user_host}' failed — check SSH key auth"
    local sock_path
    if [[ "${remote_uid}" == "0" ]]; then
      sock_path="/run/podman/podman.sock"
    else
      sock_path="/run/user/${remote_uid}/podman/podman.sock"
    fi

    podman system connection add "${user_host}" "ssh://${user_host}${sock_path}" \
      --identity "${ssh_identity}" \
      || die "Failed to register podman connection '${user_host}'"
  fi

  # Validate connection is viable
  log "Validating remote connection '${user_host}'..."
  podman --connection "${user_host}" info >/dev/null 2>&1 \
    || die "Podman connection '${user_host}' is not responding — ensure podman.socket is enabled on the remote host"
}

copy_image_from_remote() {
  local connection="$1"
  local image="$2"
  log "Copying image from ${connection} to host: ${image}"
  podman image scp "${connection}::${image}"
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
  local -A build_durations=()
  local builds_start=$SECONDS
  local timing_dir
  timing_dir="$(mktemp -d)"

  # Ensure remote arm64 connection is set up and viable
  if [[ -n "${REMOTE_ARM64_CONNECTION}" ]]; then
    ensure_remote_arm64_connection
  fi

  # Add latest tag if not already latest
  local -a build_args=("${BUILD_BASE_ARGS[@]}")

  # Build for each platform (in parallel)
  for platform in "${PLATFORMS[@]}"; do
    local arch="${platform#*/}"
    local platform_tag="${DOCKER_IMAGE}-${arch}"
    local connect_arg=""

    # Check if platform needs remote builder or VM
    if [[ "${arch}" == "arm64" && -n "${REMOTE_ARM64_CONNECTION}" ]]; then
      log "Platform ${platform} delegated to remote: ${REMOTE_ARM64_CONNECTION}"
      connect_arg="--connection ${REMOTE_ARM64_CONNECTION}"
    elif platform_needs_vm "${platform}"; then
      log "Platform ${platform} needs VM for emulation"
      ensure_podman_vm_running
      connect_arg="--connection podman-machine-default"
    fi

    platform_tags+=("${platform_tag}")
    platform_connect_args["${platform}"]="${connect_arg}"

    local label="[${arch}]"
    if [[ -n "${connect_arg}" ]]; then
      label="[${arch}/remote]"
    fi

    log "Building for ${platform} -> ${platform_tag}..."
    log "ENABLE_PARALLEL_BUILDS: ${ENABLE_PARALLEL_BUILDS}"
    # shellcheck disable=SC2086
    if (( ${ENABLE_PARALLEL_BUILDS:-0} == 1 )) ; then
      log "PARALLEL_BUILDS ENABLED"

      echo "(podman ${connect_arg} build \"${build_args[@]}\" --platform \"${platform}\" -t \"${platform_tag}\" .) &"
      (
        set -o pipefail
        build_start=$SECONDS
        podman ${connect_arg} build \
          "${build_args[@]}" \
          --platform "${platform}" \
          -t "${platform_tag}" \
          "${DOCKER_BUILD_CONTEXT}" 2>&1 | sed "s|^|${label} |" || exit 1
        echo $(( SECONDS - build_start )) > "${timing_dir}/${arch}"
      ) &
      build_pids+=($!)
    else
      log "PARALLEL_BUILDS DISABLED"

      local build_start=$SECONDS
      echo podman ${connect_arg} build "${build_args[@]}" --platform "${platform}" -t "${platform_tag}" "${DOCKER_BUILD_CONTEXT}"
      podman ${connect_arg} build "${build_args[@]}" --platform "${platform}" -t "${platform_tag}" "${DOCKER_BUILD_CONTEXT}" | sed "s|^|${label} |" || exit 1
      build_durations["${platform}"]=$(( SECONDS - build_start ))
      log "Build ${platform} finished in $(format_duration ${build_durations["${platform}"]})"
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
    log "All parallel builds finished in $(format_duration $(( SECONDS - builds_start )))"
    # Collect per-platform durations from subshells
    for f in "${timing_dir}"/*; do
      [[ -f "${f}" ]] || continue
      local plat_arch
      plat_arch="$(basename "${f}")"
      build_durations["linux/${plat_arch}"]="$(< "${f}")"
    done
  fi
  rm -rf "${timing_dir}"

  # Copy images built on remote to host
  local copy_start=$SECONDS
  local copy_happened=0
  for platform in "${!platform_connect_args[@]}"; do
    if [[ -n "${platform_connect_args[$platform]}" ]]; then
      local arch="${platform#*/}"
      local platform_tag="${DOCKER_IMAGE}-${arch}"
      local conn="${platform_connect_args[$platform]}"
      # Extract connection name from "--connection <name>"
      conn="${conn##*--connection }"
      copy_image_from_remote "${conn}" "${platform_tag}"
      copy_happened=1
    fi
  done
  local copy_duration=$(( SECONDS - copy_start ))

  # Create manifest from all platform images
  log "Creating manifest: ${DOCKER_IMAGE}"
  echo podman manifest create "${DOCKER_IMAGE}" "${platform_tags[@]}"
  podman manifest create "${DOCKER_IMAGE}" "${platform_tags[@]}"

  # Tag with latest (if not already latest)
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    log "Tagging as latest: ${DOCKER_IMAGE_LATEST}"
    podman tag "${DOCKER_IMAGE}" "${DOCKER_IMAGE_LATEST}"
  fi

  # Push
  local push_start=$SECONDS
  # log "To push, run:"
  # echo "  podman manifest push ${DOCKER_IMAGE} docker://${DOCKER_IMAGE}"
  echo podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE}"
  podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE}"

  # Push latest tag
  if [[ "${DOCKER_IMAGE}" != *:latest ]]; then
    log "Pushing as latest: ${DOCKER_IMAGE_LATEST}"
    podman manifest push "${DOCKER_IMAGE}" "docker://${DOCKER_IMAGE_LATEST}"
  fi
  local push_duration=$(( SECONDS - push_start ))

  # Timing summary
  log "--- Timing summary ---"
  for platform in "${!build_durations[@]}"; do
    log "  Build ${platform}: $(format_duration ${build_durations["${platform}"]})"
  done
  if (( copy_happened )); then
    log "  Copy images: $(format_duration ${copy_duration})"
  fi
  log "  Push: $(format_duration ${push_duration})"
  log "  Total: $(format_duration $(( SECONDS - builds_start )))"
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
  local main_start=$SECONDS
  setup_environment

  case "${1:-}" in
    login)
      ensure_docker_login
      ;;
    check)
      if [[ -z "${REMOTE_ARM64_CONNECTION}" ]]; then
        die "REMOTE_ARM64_CONNECTION is not set"
      fi
      ensure_remote_arm64_connection
      log "Remote arm64 connection OK"
      exit 0
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

  log "Total time: $(format_duration $(( SECONDS - main_start )))"
}

# Run main and ensure cleanup
trap 'stop_podman_vm_if_started || true' EXIT
main "$@"
