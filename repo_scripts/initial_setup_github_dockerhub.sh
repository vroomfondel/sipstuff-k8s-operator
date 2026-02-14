#!/bin/bash

cd "$(dirname "$0")" || exit 2

source ./include.sh
# note: source include.local.sh if found (which it should -> otherwise makes no sense)

SKIP_DOCKERHUB=false
if [[ -z "${DHREPO:-}" || "$DHREPO" == "UNSET" ]]; then
  SKIP_DOCKERHUB=true
  echo "DHREPO is not set or 'UNSET' — skipping all DockerHub operations."
fi

if [[ "$SKIP_DOCKERHUB" == "false" ]]; then
# Create public DockerHub repo via API
DHREPO_NS="${DHREPO%%/*}"
DHREPO_NAME="${DHREPO##*/}"

# Create repo + org access token via API if DOCKER_TOKEN is missing or not an OAT
if [[ -z "$DOCKER_TOKEN" || "$DOCKER_TOKEN" != dckr_oat* ]]; then
  # Export vars needed by dh_login.py (sourced shell vars aren't visible to child processes)
  export DOCKERHUB_ADMIN_USER DOCKERHUB_ADMIN_PASSWORD DOCKERHUB_TOTP_SECRET

  # Login to Docker Hub (handles MFA/TOTP if DOCKERHUB_TOTP_SECRET is set)
  DH_JWT=$(python3 ./dh_login.py)

  if [[ -z "$DH_JWT" ]]; then
    echo "ERROR: DockerHub login failed" >&2
    exit 123
  fi

  curl -s -f -X POST 'https://hub.docker.com/v2/repositories/' \
    -H "Authorization: Bearer $DH_JWT" \
    -H 'Content-Type: application/json' \
    -d "{\"namespace\":\"$DHREPO_NS\",\"name\":\"$DHREPO_NAME\",\"is_private\":false}"

  # Docs: https://docs.docker.com/reference/api/hub/latest/#tag/org-access-tokens
  OAT_DESC="CI/CD push token for $DHREPO, created $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  OAT_EXPIRES=$(date -u -d "+365 days" +%Y-%m-%dT%H:%M:%SZ)
  OAT_RESPONSE=$(curl -s -f -X POST "https://hub.docker.com/v2/orgs/$DHREPO_NS/access-tokens" \
    -H "Authorization: Bearer $DH_JWT" \
    -H 'Content-Type: application/json' \
    -d "{\"label\":\"$DHREPO\",\"description\":\"$OAT_DESC\",\"expires_at\":\"$OAT_EXPIRES\",\"resources\":[{\"type\":\"TYPE_REPO\",\"path\":\"*/*/public\",\"scopes\":[\"image-pull\"]},{\"type\":\"TYPE_REPO\",\"path\":\"$DHREPO\",\"scopes\":[\"image-pull\",\"image-push\"]}]}")

  # Extract token value (dckr_oat_...) — shown once, cannot be retrieved again
  echo "$OAT_RESPONSE" >&2
  DOCKER_TOKEN=$(echo "$OAT_RESPONSE" | jq -r '.token')
  if [[ -z "$DOCKER_TOKEN" ]]; then
    echo "ERROR: OAT token creation/extraction failed" >&2
    exit 124
  fi
  echo "OAT token: $DOCKER_TOKEN"

  # Persist to include.local.sh
  sed -i "s|^DOCKER_TOKEN=.*|DOCKER_TOKEN=\"$DOCKER_TOKEN\"|" include.local.sh
fi

# Ensure DockerHub repo exists (no-op if already created in the block above)
# Try unauthenticated first (works for public repos), then authenticated (private repos)
DHREPO_EXISTS=false
if curl -s -f -o /dev/null "https://hub.docker.com/v2/repositories/$DHREPO/"; then
  DHREPO_EXISTS=true
fi

if [[ "$DHREPO_EXISTS" == "false" ]]; then
  # Could be private or truly non-existent — need admin login to tell
  export DOCKERHUB_ADMIN_USER DOCKERHUB_ADMIN_PASSWORD DOCKERHUB_TOTP_SECRET
  DH_JWT=$(python3 ./dh_login.py)
  if [[ -z "$DH_JWT" ]]; then
    echo "ERROR: DockerHub admin login failed" >&2
    exit 123
  fi

  if curl -s -f -o /dev/null "https://hub.docker.com/v2/repositories/$DHREPO/" \
    -H "Authorization: Bearer $DH_JWT"; then
    DHREPO_EXISTS=true
    echo "DockerHub repo $DHREPO exists (private)"
  else
    echo "DockerHub repo $DHREPO does not exist yet, creating..."
    if ! curl -s -f -X POST 'https://hub.docker.com/v2/repositories/' \
      -H "Authorization: Bearer $DH_JWT" \
      -H 'Content-Type: application/json' \
      -d "{\"namespace\":\"$DHREPO_NS\",\"name\":\"$DHREPO_NAME\",\"is_private\":false}" > /dev/null; then
      echo "ERROR: Failed to create DockerHub repo $DHREPO" >&2
      exit 129
    fi
    echo "Created DockerHub repo: $DHREPO"
  fi
fi

# Validate Docker Hub token has push+pull access to target repo
echo "Validating Docker Hub token permissions for $DHREPO..."
DH_CHECK_ARGS=("$DOCKER_TOKENUSER" "$DOCKER_TOKEN" "--json")
if [[ "$DOCKER_TOKENUSER" != "$DHREPO_NS" ]]; then
  DH_CHECK_ARGS+=("-n" "$DHREPO_NS")
fi
DH_CHECK_JSON=$(python3 ./check_dockerhub_token.py "${DH_CHECK_ARGS[@]}")

if [[ -z "$DH_CHECK_JSON" ]]; then
  echo "ERROR: Docker Hub token validation failed" >&2
  exit 125
fi

# Check if target repo has both pull and push
REPO_PERMS=$(echo "$DH_CHECK_JSON" | jq -r --arg repo "$DHREPO" \
  '[.repositories[] | select(.repository == $repo) | .permissions[]] | join(" ")')

if [[ "$REPO_PERMS" != *pull* || "$REPO_PERMS" != *push* ]]; then
  CURRENT_PERMS=$(echo "$DH_CHECK_JSON" | jq -r --arg repo "$DHREPO" \
    '.repositories[] | select(.repository == $repo) | .permissions | join(", ")' 2>/dev/null)

  if [[ -z "$CURRENT_PERMS" ]]; then
    echo "WARNING: Token has NO permissions for $DHREPO (repo may not exist or token has no access)"
  else
    echo "WARNING: Token only has [$CURRENT_PERMS] for $DHREPO (need: pull, push)"
  fi

  # Docker Hub API does not support PATCHing OAT resources — must create a new token
  read -rp "Create a new token with push+pull for $DHREPO? (replaces current DOCKER_TOKEN) [y/N] " EXTEND_ANSWER
  if [[ "$EXTEND_ANSWER" != [yY] ]]; then
    echo "Aborted by user." >&2
    exit 1
  fi

  # Login to Docker Hub as admin to create a new OAT
  export DOCKERHUB_ADMIN_USER DOCKERHUB_ADMIN_PASSWORD DOCKERHUB_TOTP_SECRET
  DH_JWT=$(python3 ./dh_login.py)
  if [[ -z "$DH_JWT" ]]; then
    echo "ERROR: DockerHub admin login failed" >&2
    exit 123
  fi

  OAT_DESC="CI/CD push token for $DHREPO, created $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  OAT_EXPIRES=$(date -u -d "+365 days" +%Y-%m-%dT%H:%M:%SZ)
  OAT_RESPONSE=$(curl -s -w '\n%{http_code}' -X POST "https://hub.docker.com/v2/orgs/$DHREPO_NS/access-tokens" \
    -H "Authorization: Bearer $DH_JWT" \
    -H 'Content-Type: application/json' \
    -d "{\"label\":\"$DHREPO\",\"description\":\"$OAT_DESC\",\"expires_at\":\"$OAT_EXPIRES\",\"resources\":[{\"type\":\"TYPE_REPO\",\"path\":\"*/*/public\",\"scopes\":[\"image-pull\"]},{\"type\":\"TYPE_REPO\",\"path\":\"$DHREPO\",\"scopes\":[\"image-pull\",\"image-push\"]}]}")
  OAT_HTTP=$(echo "$OAT_RESPONSE" | tail -1)
  OAT_BODY=$(echo "$OAT_RESPONSE" | sed '$d')

  if [[ "$OAT_HTTP" -lt 200 || "$OAT_HTTP" -ge 300 ]]; then
    echo "ERROR: Failed to create new OAT (HTTP $OAT_HTTP)" >&2
    echo "$OAT_BODY" >&2
    exit 127
  fi

  echo "$OAT_BODY" >&2
  DOCKER_TOKEN=$(echo "$OAT_BODY" | jq -r '.token')
  if [[ -z "$DOCKER_TOKEN" || "$DOCKER_TOKEN" == "null" ]]; then
    echo "ERROR: OAT token creation/extraction failed" >&2
    exit 124
  fi
  echo "New OAT token: $DOCKER_TOKEN"

  # Persist new token to include.local.sh
  sed -i "s|^DOCKER_TOKEN=.*|DOCKER_TOKEN=\"$DOCKER_TOKEN\"|" include.local.sh

  # Re-validate with the new token
  echo "Re-validating token permissions..."
  DH_CHECK_ARGS=("$DOCKER_TOKENUSER" "$DOCKER_TOKEN" "--json")
  if [[ "$DOCKER_TOKENUSER" != "$DHREPO_NS" ]]; then
    DH_CHECK_ARGS+=("-n" "$DHREPO_NS")
  fi
  DH_CHECK_JSON=$(python3 ./check_dockerhub_token.py "${DH_CHECK_ARGS[@]}")
  REPO_PERMS=$(echo "$DH_CHECK_JSON" | jq -r --arg repo "$DHREPO" \
    '[.repositories[] | select(.repository == $repo) | .permissions[]] | join(" ")')

  if [[ "$REPO_PERMS" != *pull* || "$REPO_PERMS" != *push* ]]; then
    echo "ERROR: New token still lacks push+pull for $DHREPO" >&2
    exit 128
  fi
  echo "Re-validation successful"
fi

echo "Docker Hub token validated: push+pull OK for $DHREPO"
fi

# Create public gist with badge files if GIST_ID is not yet set
if [[ -z "$GIST_ID" || "$GIST_ID" != ghp_* ]]; then
  REPO_SHORT="${GHREPO##*/}"
  GIST_DESC="$REPO_SHORT clone tracking"

  # Check if a gist with this description already exists
  EXISTING_GIST_ID=$(GH_TOKEN="$GIST_TOKEN" gh gist list --public -L 100 | grep "$GIST_DESC" | head -1 | cut -f1)

  if [[ -n "$EXISTING_GIST_ID" ]]; then
    GIST_ID="$EXISTING_GIST_ID"
    echo "Found existing gist: $GIST_ID (description: $GIST_DESC)"
  else
    HIST_FILE="/tmp/${REPO_SHORT}_clone_history.json"
    BADGE_FILE="/tmp/${REPO_SHORT}_clone_count.json"
    echo '{}' > "$HIST_FILE"
    echo '{}' > "$BADGE_FILE"

    GIST_URL=$(GH_TOKEN="$GIST_TOKEN" gh gist create --public --desc "$GIST_DESC" "$HIST_FILE" "$BADGE_FILE")
    GIST_ID="${GIST_URL##*/}"
    echo "Created gist: $GIST_URL (ID: $GIST_ID)"

    rm "$HIST_FILE" "$BADGE_FILE"
  fi

  # Persist to include.local.sh
  sed -i "s|^GIST_ID=.*|GIST_ID=\"$GIST_ID\"|" include.local.sh
fi

# Replace GIST_ID default in update_badge.py if it doesn't match
CURRENT_GIST_DEFAULT=$(grep -oP 'os\.environ\.get\("GIST_ID",\s*"\K[^"]+' update_badge.py)
if [[ -n "$GIST_ID" && "$CURRENT_GIST_DEFAULT" != "$GIST_ID" ]]; then
  sed -i "s|$CURRENT_GIST_DEFAULT|$GIST_ID|" update_badge.py
  echo "Updated GIST_ID default in update_badge.py: $CURRENT_GIST_DEFAULT -> $GIST_ID"
fi

# Create GitHub repo if it doesn't exist yet
if ! gh repo view "$GHREPO" &>/dev/null; then
  gh repo create "$GHREPO" --public
  echo "Created GitHub repo: $GHREPO"
fi

gh secret set GIST_ID --body "$GIST_ID" --repo "$GHREPO"
if [[ "$SKIP_DOCKERHUB" == "false" ]]; then
  gh secret set DOCKERHUB_TOKEN --body "$DOCKER_TOKEN" --repo "$GHREPO"
  gh secret set DOCKERHUB_USERNAME --body "$DOCKER_TOKENUSER" --repo "$GHREPO"
fi
gh secret set GIST_TOKEN --body "$GIST_TOKEN" --repo "$GHREPO"
gh secret set REPO_PRIV_TOKEN --body "$REPO_PRIV_TOKEN" --repo "$GHREPO"

# NOTE: REPO_TOKEN only needed locally